"""Pipeline glue: editor events -> debounce/batch -> analysis -> tutor -> UI broadcast.

Design rules:
  * the editor socket handler only enqueues; it never waits on analysis or the LLM
  * analysis/tutor are sync interfaces owned by B/C, so they run in worker threads
  * "thinking" is broadcast immediately when a batch fires
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib
import logging
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from config import Config
from redact import redact

log = logging.getLogger("tutor.pipeline")

VALID_TYPES = {"edit", "focus", "cursor", "diagnostics", "test_run", "file_open"}
MODES = {"dsa", "system_design"}
MAX_BATCH = 200
FALLBACK_Q = "Walk me through what you expect this part to do on the smallest input you can think of."


# ----------------------------------------------------------------- helpers
def as_dict(x: Any) -> dict | None:
    """Accept dicts, pydantic models or dataclasses from B/C's modules."""
    if x is None:
        return None
    if isinstance(x, dict):
        return x
    for attr in ("model_dump", "dict"):
        if hasattr(x, attr):
            return getattr(x, attr)()
    if dataclasses.is_dataclass(x):
        return dataclasses.asdict(x)
    return dict(x)


def sanitize_question(text: str) -> str:
    """Safety net for the core rule: the pet never outputs code or direct fixes."""
    text = (text or "").strip()
    if not text or "```" in text:
        return FALLBACK_Q
    return text[:600]


def load_backends(cfg: Config) -> SimpleNamespace:
    """Import analysis/tutor/graph with adapters; fall back to stubs per module (config.mock.*)."""
    ns = SimpleNamespace()

    # 1. Graph backend (FalkorDB is mandatory)
    try:
        from adapters.graph_adapter import GraphAdapter
        g_adapter = GraphAdapter(host=cfg.falkordb.host, port=cfg.falkordb.port, require_falkordb=True)
        ns.graph = g_adapter
        log.info("backend graph    -> GraphAdapter (FalkorDB %s:%s)", cfg.falkordb.host, cfg.falkordb.port)
    except Exception as e:
        log.critical("FalkorDB is required! Failed to initialize GraphAdapter: %s", e)
        raise RuntimeError(f"FalkorDB is required. Cannot start without active FalkorDB instance on {cfg.falkordb.host}:{cfg.falkordb.port}: {e}") from e

    # 2. Tutor backend
    if not cfg.mock.tutor:
        try:
            from adapters.tutor_adapter import TutorAdapter
            ns.tutor = TutorAdapter(ns.graph, model=cfg.ollama.model)
            log.info("backend tutor    -> TutorAdapter (Ollama model=%s)", cfg.ollama.model)
        except Exception as e:
            log.warning("real tutor adapter failed (%s); using stub", e)
            ns.tutor = importlib.import_module("mocks.tutor_stub")
    else:
        ns.tutor = importlib.import_module("mocks.tutor_stub")
        log.info("backend tutor    -> stub")

    # 3. Analysis backend
    if not cfg.mock.analysis:
        try:
            from adapters import analysis_adapter
            ns.analysis = SimpleNamespace(
                analyze=analysis_adapter.adapt_analyze,
                run_tests=analysis_adapter.adapt_run_tests,
                test_results_to_findings=analysis_adapter.adapt_test_results_to_findings,
                score_design=analysis_adapter.adapt_score_design,
            )
            log.info("backend analysis -> AnalysisAdapter")
        except Exception as e:
            log.warning("real analysis adapter failed (%s); using stub", e)
            ns.analysis = importlib.import_module("mocks.analysis_stub")
    else:
        ns.analysis = importlib.import_module("mocks.analysis_stub")
        log.info("backend analysis -> stub")

    return ns


class Hub:
    """Holds the UI and editor sockets and fans messages out to them."""

    def __init__(self) -> None:
        self.ui: set = set()
        self.editors: set = set()
        self._lock = asyncio.Lock()

    async def _fanout(self, socks: set, msg: dict) -> None:
        async with self._lock:
            dead = []
            for ws in list(socks):
                try:
                    await ws.send_json(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                socks.discard(ws)

    async def to_ui(self, channel: str, payload: dict) -> None:
        await self._fanout(self.ui, {"channel": channel, "payload": payload})

    async def to_editor(self, command: str, **kw: Any) -> None:
        await self._fanout(self.editors, {"command": command, **kw})


# ---------------------------------------------------------------- pipeline
class Pipeline:
    def __init__(self, cfg: Config, hub: Hub, backends: SimpleNamespace) -> None:
        self.cfg, self.hub, self.b = cfg, hub, backends
        self.paused = False
        self.inspector = getattr(cfg, "inspector", True)
        self.last_activity = time.time()
        self._batches: dict[str, list[dict]] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._busy: set[str] = set()
        self._last_bubble = 0.0
        self._seen: dict[tuple[str, str], float] = {}
        self._last_finding: dict[str, dict] = {}
        self._lesson_cache: dict[Path, tuple[float, dict]] = {}
        self._ingested: set[str] = set()
        self._mode: str | None = None
        self._sleeping = False
        self._hold_until = 0.0
        self._watch: asyncio.Task | None = None
        self._last_bubble_payload: dict | None = None
        self._latest_reviews: dict[str, dict] = {}
        self._active_buffers: dict[str, dict] = {}
        self._resolved_mistakes: dict[str, set[str]] = {}   # sid -> set of mistake_ids that were fixed
        self._finding_code_snapshot: dict[str, str] = {}     # sid -> code when last finding was reported

    def get_review(self, design_id: str | None = None) -> dict | None:
        if design_id and design_id in self._latest_reviews:
            return self._latest_reviews[design_id]
        if self._latest_reviews:
            return next(iter(self._latest_reviews.values()))
        return None

    # ---- lifecycle
    async def start(self) -> None:
        self._watch = asyncio.create_task(self._idle_watch())

    async def stop(self) -> None:
        for t in [self._watch, *self._timers.values()]:
            if t and not t.done():
                t.cancel()

    async def _idle_watch(self) -> None:
        while True:
            await asyncio.sleep(2)
            now = time.time()
            if self.paused:
                continue

            # Sleep after long inactivity (5 min)
            if not self._sleeping and now - self.last_activity > self.cfg.sleep_after_seconds:
                self._sleeping = True
                await self.hub.to_ui("pet_state", {"state": "sleeping"})
                continue

            # 10-second stability check: If user typed code and paused for >= 10s, auto-analyze with Qwen model!
            for sid, ab in list(self._active_buffers.items()):
                file = ab.get("file")
                content = ab.get("content")
                edit_time = ab.get("last_edit_ts") or ab.get("ts", 0)
                if not file or not content or not edit_time:
                    continue

                # Has the user stopped typing for at least 10 seconds?
                idle_duration = now - edit_time
                if idle_duration >= 10.0:
                    last_analyzed = ab.get("last_analyzed_content")
                    # Trigger if code differs from what was last analyzed and cooldown has passed
                    if content != last_analyzed and (now - self._last_bubble > 5.0) and sid not in self._busy:
                        ab["last_analyzed_content"] = content
                        log.info("Student %s stable for %.1fs without typing; analyzing with Qwen: %s", sid, idle_duration, file)
                        asyncio.create_task(self._auto_analyze_stable(sid, file, content, ab))

    # ---- lessons
    def find_lesson(self, file: str) -> dict | None:
        p = Path(file).expanduser().resolve()
        roots = self.cfg.allowlist()
        for d in [p.parent, *p.parent.parents]:
            y = d / "lesson.yaml"
            if y.is_file():
                try:
                    m = y.stat().st_mtime
                    c = self._lesson_cache.get(d)
                    if c and c[0] == m:
                        return c[1]
                    data = yaml.safe_load(y.read_text()) or {}
                    data["_dir"], data["_path"] = str(d), str(y)
                    self._lesson_cache[d] = (m, data)
                    return data
                except Exception:
                    log.exception("bad lesson.yaml at %s", y)
                    return None
            if d in roots:
                break
        return None

    async def _note_lesson(self, lesson: dict | None) -> None:
        if not lesson:
            return
        track = lesson.get("track")
        mode = track if track in MODES else "dsa"
        if mode != self._mode:
            self._mode = mode
            await self.hub.to_ui("mode", {"mode": mode, "lesson_id": lesson.get("id")})
        if lesson["_path"] not in self._ingested:
            self._ingested.add(lesson["_path"])
            asyncio.create_task(self._ingest_lesson(lesson["_path"]))

    async def _ingest_lesson(self, path: str) -> None:
        try:
            await asyncio.to_thread(self.b.graph.ingest_lesson, path)
        except Exception:
            log.exception("graph.ingest_lesson failed for %s", path)

    # ---- ingest (called from /ws/editor; must be fast)
    async def ingest(self, ev: dict) -> None:
        if self.paused:
            return
        if ev.get("type") not in VALID_TYPES:
            log.debug("dropping event with bad type: %r", ev.get("type"))
            return
        file = ev.get("file", "")
        if not self.cfg.is_allowed(file):
            log.debug("dropping event outside allowlist: %s", file)
            return
        sid = ev.get("student_id") or self.cfg.default_student
        ev["student_id"] = sid
        if ev.get("content"):
            ev["content"] = redact(ev["content"])
        for d in ev.get("diagnostics") or []:
            d["message"] = redact(str(d.get("message", "")))
        log.debug("event %s %s", ev["type"], file)

        if file:
            ab = self._active_buffers.setdefault(sid, {})
            ab["file"] = file
            if ev.get("language"):
                ab["language"] = ev["language"]
            if ev.get("content"):
                ab["content"] = ev["content"]
            if ev.get("cursor"):
                ab["cursor"] = ev["cursor"]
            if ev.get("type") in ("edit", "focus"):
                ab["last_edit_ts"] = time.time()
            ab["ts"] = time.time()

        self.last_activity = time.time()
        if self._sleeping:
            self._sleeping = False
            await self.hub.to_ui("pet_state", {"state": "curious"})

        batch = self._batches.setdefault(sid, [])
        batch.append(ev)
        if len(batch) > MAX_BATCH:
            for i, e in enumerate(batch):
                if e["type"] == "cursor":
                    del batch[i]
                    break
            else:
                del batch[0]

        t = ev["type"]
        if t in ("focus", "file_open"):
            await self._note_lesson(self.find_lesson(file))
        if t == "edit":
            quiet = sid not in self._timers or self._timers[sid].done()
            if quiet and sid not in self._busy:
                await self.hub.to_ui("pet_state", {"state": "curious"})
            self._schedule(sid, self.cfg.debounce_seconds)
        elif t == "diagnostics":
            self._schedule(sid, self.cfg.debounce_seconds)
        elif t == "test_run":
            self._schedule(sid, 0)

    def _schedule(self, sid: str, delay: float) -> None:
        old = self._timers.get(sid)
        if old and not old.done():
            old.cancel()
        self._timers[sid] = asyncio.create_task(self._debounced(sid, delay))

    async def _debounced(self, sid: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        batch = self._batches.pop(sid, [])
        if not batch:
            return
        counts = Counter(e["type"] for e in batch)
        last = batch[-1]
        log.info("batch student=%s file=%s lang=%s events=%s", sid, last.get("file"),
                 last.get("language"), dict(counts))
        await self.hub.to_ui("pet_state", {"state": "thinking"})  # immediate, before any slow work
        asyncio.create_task(self._process(sid, batch))

    # ---- background processing
    async def _process(self, sid: str, batch: list[dict]) -> None:
        if sid in self._busy:  # previous job still running: requeue and retry after next quiet period
            self._batches.setdefault(sid, [])[:0] = batch
            self._schedule(sid, self.cfg.debounce_seconds)
            return
        self._busy.add(sid)
        try:
            file = next((e["file"] for e in reversed(batch) if e.get("file")), "")
            lesson = self.find_lesson(file) if file else None
            await self._note_lesson(lesson)

            # Detect if code changed since the last finding → mark old mistakes as resolved
            current_content = next((e.get("content") for e in reversed(batch) if e.get("content")), "")
            prev_snapshot = self._finding_code_snapshot.get(sid, "")
            if current_content and prev_snapshot and current_content != prev_snapshot:
                last_f = self._last_finding.get(sid)
                if last_f:
                    old_mid = last_f.get("mistake_id", "")
                    if old_mid:
                        self._resolved_mistakes.setdefault(sid, set()).add(old_mid)
                        log.info("Marked mistake '%s' as resolved for student %s (code changed)", old_mid, sid)

            # System design markdown support (e.g. design.md)
            if file and file.lower().endswith(".md") and hasattr(self.b.analysis, "score_design"):
                content = next((e.get("content") for e in reversed(batch) if e.get("content")), "")
                review, findings = await asyncio.to_thread(self.b.analysis.score_design, content, lesson)
                rev_dict = review.model_dump() if hasattr(review, "model_dump") else as_dict(review)
                lid = (lesson or {}).get("id", "url-shortener")
                self._latest_reviews[lid] = rev_dict
                await self.hub.to_ui("review", rev_dict)
                await self.hub.to_ui("mode", {"mode": "system_design"})
                finding_dicts = [as_dict(f) for f in findings]
                finding = self._pick(finding_dicts, sid)
                if finding:
                    out = as_dict(await asyncio.wait_for(
                        asyncio.to_thread(self.b.tutor.handle, finding, sid),
                        self.cfg.llm_timeout_seconds))
                    self._last_finding[sid] = finding
                    self._finding_code_snapshot[sid] = content
                    self._last_bubble = time.time()
                    self._seen[(sid, finding.get("mistake_id", ""))] = self._last_bubble
                    await self._emit(out, sid)
                elif time.time() > self._hold_until:
                    await self.hub.to_ui("pet_state", {"state": "idle"})
                return

            raw = await asyncio.wait_for(
                asyncio.to_thread(self.b.analysis.analyze, batch, lesson),
                self.cfg.llm_timeout_seconds)
            findings = [as_dict(f) for f in (raw or [])]
            finding = self._pick(findings, sid)
            if not finding:
                if time.time() > self._hold_until:
                    await self.hub.to_ui("pet_state", {"state": "idle"})
                return

            # Record attempt in graph
            try:
                lid = (lesson or {}).get("id", "unknown")
                await asyncio.to_thread(
                    self.b.graph.record_attempt,
                    sid,
                    lid,
                    False,
                    finding.get("mistake_id"),
                    finding.get("line"),
                )
            except Exception:
                log.debug("record_attempt failed", exc_info=True)

            out = as_dict(await asyncio.wait_for(
                asyncio.to_thread(self.b.tutor.handle, finding, sid),
                self.cfg.llm_timeout_seconds))
            self._last_finding[sid] = finding
            self._finding_code_snapshot[sid] = current_content
            self._last_bubble = time.time()
            self._seen[(sid, finding.get("mistake_id", ""))] = self._last_bubble
            await self._emit(out, sid)
        except Exception:
            log.exception("pipeline failure")
            await self.hub.to_ui("pet_state", {"state": "idle"})
        finally:
            self._busy.discard(sid)

    async def _auto_analyze_stable(self, sid: str, file: str, content: str, ab: dict) -> None:
        """When student stops typing for 10 seconds, analyze their code in the active file and offer a guiding hint."""
        if sid in self._busy:
            return
        self._busy.add(sid)
        try:
            await self.hub.to_ui("pet_state", {"state": "thinking"})
            lesson = self.find_lesson(file) if file else None
            lang = ab.get("language") or "python"

            batch = [{
                "type": "edit",
                "file": file,
                "content": content,
                "language": lang,
                "student_id": sid,
                "cursor": ab.get("cursor"),
                "ts": ab.get("ts", time.time()),
            }]

            raw = await asyncio.wait_for(
                asyncio.to_thread(self.b.analysis.analyze, batch, lesson),
                self.cfg.llm_timeout_seconds,
            )
            findings = [as_dict(f) for f in (raw or [])]
            finding = self._pick(findings, sid)

            # Generate non-question guiding hint directly for the desktop pet bubble
            try:
                fn_hint = getattr(self.b.tutor, "generate_idle_hint", None)
                if fn_hint:
                    out = await asyncio.wait_for(
                        asyncio.to_thread(
                            fn_hint,
                            content,
                            file,
                            lesson=lesson,
                            student_id=sid,
                        ),
                        self.cfg.llm_timeout_seconds,
                    )
                else:
                    from tutor.engine import get_smart_code_hint
                    hint_msg = get_smart_code_hint(content, (lesson or {}).get("id", ""))
                    out = {
                        "question": hint_msg,
                        "text": hint_msg,
                        "hint_level": 1,
                        "targets_concept": (lesson or {}).get("concepts", ["logic"])[0] if lesson else "logic",
                        "mistake_id": "idle_guidance",
                        "pet_state": "curious",
                    }
            except Exception:
                from tutor.engine import get_smart_code_hint
                hint_msg = get_smart_code_hint(content, (lesson or {}).get("id", ""))
                out = {
                    "question": hint_msg,
                    "text": hint_msg,
                    "hint_level": 1,
                    "targets_concept": (lesson or {}).get("concepts", ["logic"])[0] if lesson else "logic",
                    "mistake_id": "idle_guidance",
                    "pet_state": "curious",
                }

            out = as_dict(out)
            self._last_bubble = time.time()
            # Send ONLY to desktop pet bubble and UI websocket (to_sidebar=False)
            await self._emit(out, sid, to_sidebar=False)
        except Exception:
            log.exception("auto-analysis on stable pause failed")
            await self.hub.to_ui("pet_state", {"state": "idle"})
        finally:
            self._busy.discard(sid)

    def _pick(self, findings: list[dict], sid: str) -> dict | None:
        # Guess-spam bypasses cooldowns (Wiring requirement 6)
        for f in findings:
            if f.get("kind") == "guess_spam":
                return f

        resolved = self._resolved_mistakes.get(sid, set())
        now = time.time()
        if now - self._last_bubble < self.cfg.cooldowns.bubble_seconds:
            log.info("bubble cooldown active; skipping %d finding(s)", len(findings))
            return None
        for f in sorted(findings, key=lambda f: f.get("confidence", 0), reverse=True):
            if f.get("confidence", 1) < self.cfg.min_confidence:
                continue
            mid = f.get("mistake_id", "")
            # Skip mistakes the student already fixed (code changed since the finding was reported)
            if mid in resolved:
                log.info("skipping resolved mistake '%s' for student %s", mid, sid)
                continue
            if now - self._seen.get((sid, mid), 0) < self.cfg.cooldowns.same_mistake_minutes * 60:
                continue
            return f
        return None

    async def _emit(self, out: dict, sid: str, to_sidebar: bool = True) -> None:
        from adapters.tutor_adapter import sanitize_final_output
        is_q = to_sidebar and out.get("pet_state") != "celebrating"
        q = sanitize_final_output(out.get("text") or out.get("question", ""), out.get("mistake_id"), require_question=is_q)
        out["question"] = q
        out["text"] = q
        pet_st = out.get("pet_state", "asking")
        await self.hub.to_ui("pet_state", {"state": pet_st})

        trace = out.get("graph_trace") or {
            "cypher": "MATCH (m:Mistake {id:$mid})-[:INVOLVES]->(c:Concept) RETURN m, c",
            "query_ms": 1.5,
            "nodes": [],
            "edges": [],
        }

        # UI Contract 4 bubble payload (includes graph_trace so inspector drawer always has data)
        bubble_payload = {
            "question": q,
            "text": q,
            "hint_level": out.get("hint_level", 0),
            "targets_concept": out.get("targets_concept"),
            "mistake_id": out.get("mistake_id"),
            "pet_state": pet_st,
            "graph_trace": trace,
        }
        self._last_bubble_payload = bubble_payload
        await self.hub.to_ui("bubble", bubble_payload)

        # Graph update payload with highlighted nodes/edges
        highlight_nodes = [n.get("id") for n in trace.get("nodes", []) if isinstance(n, dict)]
        await self.hub.to_ui("graph_update", {
            "nodes": trace.get("nodes", []),
            "edges": trace.get("edges", []),
            "highlight": {
                "node_ids": highlight_nodes,
                "edges": trace.get("edges", []),
                "reason": out.get("mistake_id") or "finding",
            },
        })

        if to_sidebar:
            await self.hub.to_editor("show_hint", text=q, hint_level=out.get("hint_level", 0),
                                     concept=out.get("targets_concept"))
        try:
            tc = out.get("targets_concept") or "binary_search"
            is_celebrating = out.get("pet_state") == "celebrating"
            is_mistake = bool(out.get("mistake_id") and out.get("mistake_id") != "general")
            passed = is_celebrating or not is_mistake
            delta = 0.10 if is_celebrating else (-0.05 if is_mistake else 0.05)

            # Persist real mastery score in FalkorDB
            if hasattr(self.b, "graph") and self.b.graph:
                try:
                    await asyncio.to_thread(self.b.graph.update_mastery, sid, tc, passed)
                except Exception:
                    pass

            try:
                progress = await asyncio.to_thread(self.b.graph.get_progress, sid, mode=self._mode)
            except TypeError:
                progress = await asyncio.to_thread(self.b.graph.get_progress, sid)
            if progress:
                concepts = progress.get("concepts", [])
                matched = next((c for c in concepts if c.get("id") == tc), concepts[0] if concepts else None)
                if matched:
                    await self.hub.to_ui("mastery_update", {
                        "concept_id": matched.get("id", tc),
                        "mastery": matched.get("mastery", 0.5),
                        "delta": delta,
                        "evolution": progress.get("evolution"),
                    })
        except Exception:
            log.debug("mastery update unavailable", exc_info=True)

    # ---- typed or spoken reply (same path)
    async def handle_reply(
        self,
        text: str,
        sid: str,
        source: str = "typed",
        file: str | None = None,
        content: str | None = None,
        language: str | None = None,
        cursor: dict | None = None,
        selection: dict | None = None,
    ) -> dict:
        text = redact(text or "").strip()
        log.info("reply (%s) student=%s file=%s: %.80s", source, sid, file, text)
        self.last_activity = time.time()
        await self.hub.to_ui("pet_state", {"state": "thinking"})

        # Resolve active buffer context
        ab = self._active_buffers.get(sid, {})
        active_file = file or ab.get("file")
        active_content = content or ab.get("content")
        active_lang = language or ab.get("language")
        active_cursor = cursor or ab.get("cursor")

        # If file is known but content is missing, load it from disk
        if active_file and not active_content:
            try:
                p = Path(active_file)
                if p.is_file():
                    active_content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        lesson = self.find_lesson(active_file) if active_file else None
        last = self._last_finding.get(sid)
        try:
            fn = getattr(self.b.tutor, "handle_reply", None)
            if fn:
                out = await asyncio.wait_for(
                    asyncio.to_thread(
                        fn,
                        text,
                        sid,
                        last,
                        active_code=active_content,
                        active_file=active_file,
                        active_language=active_lang,
                        cursor=active_cursor,
                        lesson=lesson,
                    ),
                    self.cfg.llm_timeout_seconds,
                )
            elif last:
                out = await asyncio.wait_for(
                    asyncio.to_thread(self.b.tutor.handle, {**last, "student_reply": text}, sid),
                    self.cfg.llm_timeout_seconds,
                )
            else:
                out = {
                    "question": "What are you trying to make this code do, in one sentence?",
                    "hint_level": 0,
                    "targets_concept": None,
                    "mistake_id": None,
                    "pet_state": "asking",
                }
        except Exception:
            log.exception("reply failure")
            out = {"question": FALLBACK_Q, "hint_level": 0, "pet_state": "asking"}
        out = as_dict(out)
        if out.get("pet_state") == "celebrating":
            lid = (lesson or {}).get("id") or "binary-search"
            try:
                await asyncio.to_thread(self.b.graph.record_attempt, sid, lid, True)
            except Exception:
                pass
            await self.hub.to_ui("confetti", {"reason": "question solved", "count": 100})
        await self._emit(out, sid)
        return out

    # ---- test runs triggered by "Tutor: Run Tests"
    async def after_tests(self, sid: str, file: str, language: str, result: dict) -> None:
        results = result.get("results", [])
        all_passed = bool(results) and all(r.get("passed") for r in results)
        lid = result.get("lesson_id", "")
        lesson = self.find_lesson(file)

        if all_passed:
            self._hold_until = time.time() + 4
            try:
                await asyncio.to_thread(self.b.graph.record_attempt, sid, lid, True)
            except Exception:
                pass
            if hasattr(self.b.tutor, "reset_recurrence"):
                last_mid = (self._last_finding.get(sid) or {}).get("mistake_id")
                self.b.tutor.reset_recurrence(sid, last_mid)

            concepts = (lesson or {}).get("concepts", [])
            primary_c = concepts[0] if concepts else "loop_invariants"
            try:
                m_upd = await asyncio.to_thread(self.b.graph.update_mastery, sid, primary_c, True)
                if m_upd:
                    await self.hub.to_ui("mastery_update", as_dict(m_upd))
            except Exception:
                pass

            await self.hub.to_ui("pet_state", {"state": "celebrating"})
            await self.hub.to_ui("confetti", {"reason": "tests passed", "count": 140})
        else:
            try:
                fn_t2f = getattr(self.b.analysis, "test_results_to_findings", None)
                if fn_t2f:
                    findings = await asyncio.to_thread(fn_t2f, result, lesson, file)
                    if findings:
                        f_dicts = [as_dict(f) for f in findings]
                        picked = self._pick(f_dicts, sid)
                        if picked:
                            await asyncio.to_thread(
                                self.b.graph.record_attempt,
                                sid,
                                lid,
                                False,
                                picked.get("mistake_id"),
                                picked.get("line"),
                            )
                            out = as_dict(await asyncio.wait_for(
                                asyncio.to_thread(self.b.tutor.handle, picked, sid),
                                self.cfg.llm_timeout_seconds))
                            self._last_finding[sid] = picked
                            self._last_bubble = time.time()
                            await self._emit(out, sid)
            except Exception:
                log.exception("test failure analysis failed")

        await self.ingest({"type": "test_run", "ts": int(time.time()), "file": file, "language": language,
                           "test_result": result, "student_id": sid})
