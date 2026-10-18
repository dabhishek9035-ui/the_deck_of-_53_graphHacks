"""Graph adapter: bridges FalkorDB, lesson ingestion, concept hierarchies, student progress, and UI live graph."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from contracts import (
    EvolutionInfo,
    GraphEdge,
    GraphNode,
    MasteryUpdatePayload,
    Progress,
    ProgressConcept,
    ProgressLesson,
)

log = logging.getLogger("tutor.adapters.graph")

DATA_STORE_PATH = Path.home() / ".cache" / "tutor-pet" / "student_history.json"


class GraphAdapter:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        graph_name: str = "socratic_tutor",
        require_falkordb: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.graph_name = graph_name
        self.require_falkordb = require_falkordb
        self._client = None
        self._graph = None
        self._concepts_ingested = False
        self._ingested_lessons: Set[str] = set()
        self._memory_attempts: List[Dict[str, Any]] = []
        self._memory_mastery: Dict[str, float] = {}
        self._memory_lessons: Dict[str, str] = {}
        self._memory_spaced_repetition: Dict[str, Dict[str, Any]] = {}
        self._memory_concept_streaks: Dict[str, int] = {}
        self._connect_falkordb()

    def _connect_falkordb(self) -> None:
        """Establish connection to centralized FalkorDB instance."""
        try:
            from graph.schema import get_client, get_graph, init_schema
        except ImportError:
            from daemon.graph.schema import get_client, get_graph, init_schema
        try:
            self._client = get_client(host=self.host, port=self.port)
            if not self._client.connection.ping():
                raise ConnectionError(f"FalkorDB ping failed on {self.host}:{self.port}")
            self._graph = get_graph(self._client, graph_name=self.graph_name)
            init_schema(self._graph)
            log.info("Connected to centralized FalkorDB at %s:%s (graph=%s)", self.host, self.port, self.graph_name)

            # Auto-seed student profile and initial curriculum if fresh database
            try:
                res = self._graph.query("MATCH (s:Student {id: 'local-user'}) RETURN count(s)")
                if getattr(res, "result_set", None) and res.result_set[0][0] == 0:
                    try:
                        from daemon.seeds.seed_data import seed_database
                    except ImportError:
                        from seeds.seed_data import seed_database
                    seed_database(self.graph_name)
                    log.info("Auto-seeded initial student history into FalkorDB")
            except Exception:
                log.debug("Auto-seed check skipped or failed", exc_info=True)
        except Exception as e:
            if self.require_falkordb:
                log.error("FalkorDB is required but unavailable on %s:%s: %s", self.host, self.port, e)
                raise RuntimeError(
                    f"FalkorDB is required! Socratic Code-Tutor Pet cannot operate without an active FalkorDB instance on {self.host}:{self.port}. "
                    f"Please launch FalkorDB: docker compose up -d falkordb"
                ) from e
            else:
                log.warning("FalkorDB unavailable (%s); require_falkordb is False", e)
                self._graph = None

    def _get_graph(self):
        """Return the active FalkorDB graph; reconnects or raises if required and unavailable."""
        if self._graph is not None:
            return self._graph
        self._connect_falkordb()
        return self._graph

    # ------------------------------------------------------------- Persistence
    def _load_local_state(self) -> None:
        if DATA_STORE_PATH.exists():
            try:
                data = json.loads(DATA_STORE_PATH.read_text(encoding="utf-8"))
                self._memory_attempts = data.get("attempts", [])
                self._memory_mastery = data.get("mastery", {})
                self._memory_lessons = data.get("lessons", {})
                self._memory_spaced_repetition = data.get("spaced_repetition", {})
                self._memory_concept_streaks = data.get("concept_streaks", {})
            except Exception:
                log.warning("Could not read student local state from %s", DATA_STORE_PATH)

    def _save_local_state(self) -> None:
        try:
            DATA_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "attempts": self._memory_attempts[-200:],
                "mastery": self._memory_mastery,
                "lessons": self._memory_lessons,
                "spaced_repetition": self._memory_spaced_repetition,
                "concept_streaks": self._memory_concept_streaks,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            DATA_STORE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            log.warning("Could not save student local state to %s", DATA_STORE_PATH)

    # --------------------------------------------------------------- Ingestion
    def ingest_concepts(self, concepts_path: Optional[str | Path] = None) -> None:
        """Ingest concepts.yaml to build the deep concept prerequisite DAG."""
        if self._concepts_ingested:
            return
        if not concepts_path:
            p = Path(__file__).resolve().parents[2] / "lessons" / "concepts.yaml"
            if p.exists():
                concepts_path = p
        if not concepts_path or not Path(concepts_path).exists():
            return

        try:
            raw = yaml.safe_load(Path(concepts_path).read_text(encoding="utf-8")) or {}
            concepts_dict = raw.get("concepts", {})
            g = self._get_graph()
            for cid, cdef in concepts_dict.items():
                name = cdef.get("name", cid)
                track = cdef.get("track", "dsa")
                if g:
                    g.query(
                        "MERGE (c:Concept {id: $id}) SET c.name = $name, c.track = $track",
                        {"id": cid, "name": name, "track": track},
                    )
                for req in cdef.get("requires", []):
                    if g:
                        g.query(
                            """
                            MERGE (c:Concept {id: $cid})
                            MERGE (r:Concept {id: $req})
                            MERGE (c)-[:REQUIRES]->(r)
                            """,
                            {"cid": cid, "req": req},
                        )
            self._concepts_ingested = True
            log.info("Ingested %d concepts from %s", len(concepts_dict), concepts_path)
        except Exception:
            log.exception("Failed to ingest concepts.yaml")

    def ingest_lesson(self, path: str | Path) -> None:
        """Single-argument lesson ingestion."""
        self.ingest_concepts()
        p = Path(path).resolve()
        if p in self._ingested_lessons:
            return

        g = self._get_graph()
        if g:
            try:
                from graph.ingest import ingest_lesson as _ingest
                _ingest(g, p)
                self._ingested_lessons.add(str(p))
                log.info("Ingested lesson package: %s", p)
            except Exception:
                log.exception("graph.ingest_lesson failed for %s", p)
        else:
            self._ingested_lessons.add(str(p))

    # --------------------------------------------------- Attempts & Mastery
    def record_attempt(
        self,
        student_id: str,
        lesson_id: str,
        passed: bool,
        mistake_id: Optional[str] = None,
        line: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Record attempt in FalkorDB and persistent history."""
        ts = int(time.time())
        iso_now = datetime.now(timezone.utc).isoformat()
        att_record = {
            "ts": ts,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "student_id": student_id,
            "lesson_id": lesson_id,
            "passed": passed,
            "mistake_id": mistake_id,
            "line": line,
        }
        self._memory_attempts.append(att_record)
        if passed:
            self._memory_lessons[lesson_id] = "done"
        else:
            if self._memory_lessons.get(lesson_id) != "done":
                self._memory_lessons[lesson_id] = "in_progress"
        self._save_local_state()

        g = self._get_graph()
        if not g:
            raise RuntimeError("FalkorDB is required. Cannot record attempt without active FalkorDB instance.")
        try:
            from graph.ingest import record_attempt as _rec
        except ImportError:
            from daemon.graph.ingest import record_attempt as _rec

        return _rec(
            graph=g,
            student_id=student_id,
            lesson_id=lesson_id,
            passed=passed,
            mistake_id=mistake_id,
            timestamp=ts,
        )

    def update_mastery(
        self, student_id: str, concept_id: str, passed: bool
    ) -> MasteryUpdatePayload:
        """Update mastery for a concept and return MasteryUpdatePayload."""
        old_m = self._memory_mastery.get(concept_id, 0.5)
        delta = 0.10 if passed else -0.15
        new_m = round(max(0.0, min(1.0, old_m + delta)), 2)
        self._memory_mastery[concept_id] = new_m

        # Compute spaced repetition due date using forgetting curve
        ts = int(time.time())
        try:
            from graph.ingest import calculate_forgetting_curve_due_date
        except ImportError:
            from daemon.graph.ingest import calculate_forgetting_curve_due_date

        streak = self._memory_concept_streaks.get(concept_id, 0)
        streak = streak + 1 if passed else 0
        self._memory_concept_streaks[concept_id] = streak

        due_date, interval_hours = calculate_forgetting_curve_due_date(new_m, streak, ts)
        self._memory_spaced_repetition[concept_id] = {
            "due_date": due_date,
            "interval_hours": interval_hours,
            "score": new_m,
        }
        self._save_local_state()

        g = self._get_graph()
        if not g:
            raise RuntimeError("FalkorDB is required. Cannot update mastery without active FalkorDB instance.")
        try:
            from graph.ingest import update_mastery as _upd
        except ImportError:
            from daemon.graph.ingest import update_mastery as _upd
        _upd(graph=g, student_id=student_id, concept_id=concept_id, passed=passed)

        evo = self.compute_evolution(student_id)
        return MasteryUpdatePayload(
            concept_id=concept_id,
            mastery=new_m,
            delta=delta,
            evolution=evo,
        )

    # ------------------------------------------------------------ Evolution
    def compute_evolution(self, student_id: str) -> EvolutionInfo:
        streak_info = self._compute_streak()
        streak = streak_info["current"]
        total_mastery = round(sum(self._memory_mastery.values()), 2)
        if not self._memory_mastery:
            total_mastery = 3.5  # reasonable starter score

        # Stage 1: Novice (blob) -> Stage 2: Apprentice (wizard) -> Stage 3: Master (dragon)
        if streak >= 14 or total_mastery >= 10.0:
            stage = 3
            variant = "dragon"
            stage_name = "Master"
            next_stage_name = None
            progress = 1.0
            next_req = None
        elif streak >= 3 or total_mastery >= 4.0:
            stage = 2
            variant = "wizard"
            stage_name = "Apprentice"
            next_stage_name = "Master"
            progress = min(1.0, round((total_mastery - 4.0) / 6.0, 2))
            next_req = {"min_streak": 14, "min_mastery_total": 10.0}
        else:
            stage = 1
            variant = "blob"
            stage_name = "Novice"
            next_stage_name = "Apprentice"
            progress = min(1.0, round(total_mastery / 4.0, 2))
            next_req = {"min_streak": 3, "min_mastery_total": 4.0}

        return EvolutionInfo(
            stage=stage,
            variant=variant,
            stage_name=stage_name,
            next_stage_name=next_stage_name,
            progress=progress,
            streak=streak,
            mastery_total=total_mastery,
            next=next_req,
        )

    def _load_all_concepts(self) -> Dict[str, Dict[str, Any]]:
        cpath = Path(__file__).resolve().parents[2] / "lessons" / "concepts.yaml"
        concepts: Dict[str, Dict[str, Any]] = {}
        if cpath.exists():
            try:
                raw = yaml.safe_load(cpath.read_text(encoding="utf-8")) or {}
                for cid, cdef in raw.get("concepts", {}).items():
                    if isinstance(cdef, dict):
                        concepts[cid] = {
                            "id": cid,
                            "name": cdef.get("name", cid),
                            "track": cdef.get("track", "dsa"),
                            "requires": cdef.get("requires", []),
                        }
            except Exception:
                log.exception("Error reading concepts.yaml")
        return concepts

    def _load_all_lessons(self) -> Dict[str, Dict[str, Any]]:
        lessons_dir = Path(__file__).resolve().parents[2] / "lessons"
        lessons: Dict[str, Dict[str, Any]] = {}
        if lessons_dir.exists():
            for lpath in sorted(lessons_dir.iterdir()):
                y = lpath / "lesson.yaml"
                if y.exists() and y.is_file():
                    try:
                        raw = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
                        lid = raw.get("id", lpath.name)
                        lessons[lid] = {
                            "id": lid,
                            "title": raw.get("title", lid),
                            "track": raw.get("track", "dsa"),
                            "concepts": raw.get("concepts", []),
                            "prerequisites": raw.get("prerequisites", []),
                        }
                    except Exception:
                        pass
        return lessons

    def _compute_streak(self, attempts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
        att = attempts if attempts is not None else self._memory_attempts
        if not att:
            return {"current": 0, "best": 0}

        dates = set()
        for a in att:
            d = a.get("date")
            if not d and "ts" in a:
                d = datetime.fromtimestamp(a["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            if d:
                try:
                    dates.add(datetime.strptime(d, "%Y-%m-%d").date())
                except ValueError:
                    pass
        if not dates:
            return {"current": 0, "best": 0}

        sorted_dates = sorted(dates)

        # Longest consecutive sequence across all dates
        best_streak = 1
        current_run = 1
        for i in range(1, len(sorted_dates)):
            if sorted_dates[i] == sorted_dates[i - 1] + timedelta(days=1):
                current_run += 1
                if current_run > best_streak:
                    best_streak = current_run
            else:
                current_run = 1

        # Current consecutive sequence ending at today or yesterday
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)

        if sorted_dates[-1] not in (today, yesterday):
            current_streak = 0
        else:
            current_streak = 1
            idx = len(sorted_dates) - 1
            while idx > 0 and sorted_dates[idx] == sorted_dates[idx - 1] + timedelta(days=1):
                current_streak += 1
                idx -= 1

        return {"current": current_streak, "best": max(best_streak, current_streak)}

    # ------------------------------------------------------------- Progress
    def get_progress(self, student_id: str = "local-user", mode: Optional[str] = None) -> Dict[str, Any]:
        """Produce the comprehensive progress payload from FalkorDB and real student state."""
        self.ingest_concepts()

        all_concepts = self._load_all_concepts()
        all_lessons = self._load_all_lessons()

        falkor_masteries: Dict[str, float] = {}
        falkor_spaced_rep: List[Dict[str, Any]] = []
        falkor_attempts: List[Dict[str, Any]] = []
        falkor_mistake_concepts: Dict[str, Set[str]] = {}

        g = self._get_graph()
        if g:
            try:
                # 1. Read concepts & masteries from FalkorDB
                c_res = g.query(
                    """
                    MATCH (c:Concept)
                    OPTIONAL MATCH (s:Student {id: $student_id})-[m:MASTERED]->(c)
                    RETURN c.id AS id, c.name AS name, c.track AS track, m.score AS score
                    """,
                    {"student_id": student_id},
                )
                for row in getattr(c_res, "result_set", []) or []:
                    cid = str(row[0])
                    cname = str(row[1]) if row[1] is not None else cid
                    ctrack = str(row[2]) if row[2] is not None else "dsa"
                    if cid not in all_concepts:
                        all_concepts[cid] = {"id": cid, "name": cname, "track": ctrack, "requires": []}
                    if row[3] is not None:
                        score_val = round(float(row[3]), 2)
                        falkor_masteries[cid] = score_val
                        self._memory_mastery[cid] = score_val
            except Exception:
                log.exception("FalkorDB concept mastery query failed")

            try:
                # 2. Read real spaced-repetition dates from FalkorDB
                sr_res = g.query(
                    """
                    MATCH (s:Student {id: $student_id})-[r:MASTERED]->(c:Concept)
                    WHERE r.due_date IS NOT NULL
                    RETURN c.id AS concept_id, r.due_date AS due_date, r.interval_hours AS interval_hours
                    ORDER BY r.due_date ASC
                    """,
                    {"student_id": student_id},
                )
                for row in getattr(sr_res, "result_set", []) or []:
                    due_date = int(row[1])
                    interval_h = float(row[2]) if row[2] is not None else 24.0
                    falkor_spaced_rep.append({
                        "concept_id": str(row[0]),
                        "due_at": datetime.fromtimestamp(due_date, tz=timezone.utc).isoformat(),
                        "interval_days": round(interval_h / 24.0, 1),
                    })
            except Exception:
                log.exception("FalkorDB spaced-repetition query failed")

            try:
                # 3. Read attempts from FalkorDB
                att_res = g.query(
                    """
                    MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(a:Attempt)
                    OPTIONAL MATCH (a)-[:FOR_LESSON]->(l:Lesson)
                    OPTIONAL MATCH (a)-[:MADE_MISTAKE]->(m:Mistake)
                    RETURN a.timestamp AS ts, a.passed AS passed, l.id AS lesson_id, m.id AS mistake_id
                    ORDER BY a.timestamp ASC
                    """,
                    {"student_id": student_id},
                )
                for row in getattr(att_res, "result_set", []) or []:
                    falkor_attempts.append({
                        "ts": int(row[0]),
                        "date": datetime.fromtimestamp(int(row[0]), tz=timezone.utc).strftime("%Y-%m-%d"),
                        "passed": bool(row[1]),
                        "lesson_id": str(row[2]) if row[2] is not None else None,
                        "mistake_id": str(row[3]) if row[3] is not None else None,
                    })
            except Exception:
                log.exception("FalkorDB attempt history query failed")

            try:
                # 4. Read mistake -> concept mappings from FalkorDB
                mc_res = g.query(
                    """
                    MATCH (m:Mistake)-[:INVOLVES]->(c:Concept)
                    RETURN m.id AS mistake_id, collect(c.id) AS concepts
                    """
                )
                for row in getattr(mc_res, "result_set", []) or []:
                    mid = str(row[0])
                    cids = [str(x) for x in row[1]] if row[1] else []
                    falkor_mistake_concepts.setdefault(mid, set()).update(cids)
            except Exception:
                log.exception("FalkorDB mistake concept query failed")

        attempts = falkor_attempts if falkor_attempts else self._memory_attempts
        current_masteries = {**self._memory_mastery, **falkor_masteries}

        # Concept list (all 44 concepts)
        concepts_out = []
        for cid, cinfo in sorted(all_concepts.items(), key=lambda x: (x[1].get("track", "dsa"), x[0])):
            m_val = current_masteries.get(cid, 0.0)
            concepts_out.append(ProgressConcept(id=cid, name=cinfo["name"], track=cinfo["track"], mastery=m_val))

        # Dynamic Skill Radar per track
        RADAR_AXES = {
            "dsa": [
                ("Arrays & Strings", ["arrays", "array_indexing", "strings", "two_pointers", "sliding_window"]),
                ("Pointers & Lists", ["pointers", "linked_lists", "stacks", "queues"]),
                ("Recursion & Trees", ["recursion", "base_cases", "call_stack", "tree_traversal"]),
                ("Search & Invariants", ["binary_search", "loop_invariants", "iteration_safety"]),
                ("Graphs & DP", ["graphs", "bfs", "dfs", "memoization", "optimal_substructure", "dp"]),
                ("Fundamentals", ["variables", "functions", "conditionals", "loops", "time_complexity", "edge_cases"]),
            ],
            "system_design": [
                ("Data Modeling", ["data_modeling", "caching", "sharding", "id_generation"]),
                ("Scalability", ["scaling", "capacity_estimation", "bottlenecks", "replication"]),
                ("Architecture", ["requirements_analysis", "api_design", "failure_handling", "tradeoffs", "rate_limiting"]),
            ],
        }
        skill_radar: Dict[str, List[Dict[str, Any]]] = {}
        for track_name, axes in RADAR_AXES.items():
            skill_radar[track_name] = []
            for axis_name, concept_keys in axes:
                vals = [current_masteries.get(k, 0.0) for k in concept_keys if k in all_concepts]
                avg_val = round(sum(vals) / len(vals), 2) if vals else 0.0
                skill_radar[track_name].append({"axis": axis_name, "value": avg_val})

        # Dynamic mode resolution
        active_mode = mode
        if not active_mode and attempts:
            last_lid = attempts[-1].get("lesson_id")
            if last_lid and last_lid in all_lessons:
                active_mode = all_lessons[last_lid].get("track", "dsa")
        if active_mode not in ("dsa", "system_design"):
            active_mode = "dsa"

        # Consecutive day streak
        streak = self._compute_streak(attempts)

        # Daily activity
        counts: Dict[str, int] = {}
        for a in attempts:
            d = a.get("date")
            if not d and "ts" in a:
                d = datetime.fromtimestamp(a["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            if d:
                counts[d] = counts.get(d, 0) + 1
        daily_activity = [{"date": d, "count": c} for d, c in sorted(counts.items())[-14:]]

        # Recent mistakes & patterns (never invent fake mistakes when no history)
        mistake_counts: Dict[str, int] = {}
        last_seen: Dict[str, str] = {}
        for a in attempts:
            mid = a.get("mistake_id")
            if mid:
                mistake_counts[mid] = mistake_counts.get(mid, 0) + 1
                d = a.get("date")
                if not d and "ts" in a:
                    d = datetime.fromtimestamp(a["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
                if d:
                    last_seen[mid] = d

        recent_mistakes = [
            {
                "mistake_id": mid,
                "count": cnt,
                "last_seen": last_seen.get(mid, ""),
                "concept_ids": sorted(list(falkor_mistake_concepts.get(mid, []))),
            }
            for mid, cnt in mistake_counts.items()
        ]
        patterns = [
            {"mistake_id": mid, "kind": "recurring" if cnt > 1 else "isolated", "count": cnt}
            for mid, cnt in mistake_counts.items()
        ]

        # Hint dependency per day (never a single static point at 1.5)
        date_hints: Dict[str, List[float]] = {}
        for a in attempts:
            d = a.get("date")
            if not d and "ts" in a:
                d = datetime.fromtimestamp(a["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            if d:
                hl = a.get("hint_level")
                if hl is None:
                    hl = 0.0 if a.get("passed") else 1.0
                date_hints.setdefault(d, []).append(float(hl))

        hint_dependency = [
            {"date": d, "avg_hint_level": round(sum(hls) / len(hls), 2)}
            for d, hls in sorted(date_hints.items())[-14:]
        ]

        # Lessons status & dynamic progress (never flat 0.5)
        lessons_out = []
        for lid, linfo in sorted(all_lessons.items(), key=lambda x: (x[1].get("track", "dsa"), x[0])):
            l_attempts = [a for a in attempts if a.get("lesson_id") == lid]
            passed = any(a.get("passed") for a in l_attempts)
            if passed or self._memory_lessons.get(lid) == "done":
                st = "done"
                prog = 1.0
            elif l_attempts or self._memory_lessons.get(lid) == "in_progress":
                st = "in_progress"
                taught = linfo.get("concepts", [])
                if taught:
                    avg_m = sum(current_masteries.get(cid, 0.0) for cid in taught) / len(taught)
                    prog = round(max(0.1, min(0.9, avg_m)), 2)
                else:
                    prog = round(min(0.8, 0.2 * len(l_attempts)), 2)
            else:
                st = "not_started"
                prog = 0.0
            lessons_out.append(ProgressLesson(id=lid, title=linfo["title"], track=linfo["track"], status=st, progress=prog))

        # Dynamic recommended next lesson
        recommended_next = None
        in_prog = [l for l in lessons_out if l.status == "in_progress"]
        if in_prog:
            recommended_next = {"lesson_id": in_prog[0].id, "reason": f"continue {in_prog[0].title}"}
        else:
            not_started_mode = [l for l in lessons_out if l.status == "not_started" and l.track == active_mode]
            if not_started_mode:
                recommended_next = {"lesson_id": not_started_mode[0].id, "reason": f"next in {active_mode} track"}
            else:
                not_started_any = [l for l in lessons_out if l.status == "not_started"]
                if not_started_any:
                    recommended_next = {"lesson_id": not_started_any[0].id, "reason": f"next lesson: {not_started_any[0].title}"}
                else:
                    if concepts_out:
                        lowest = min(concepts_out, key=lambda c: c.mastery)
                        for lid, linfo in all_lessons.items():
                            if lowest.id in linfo.get("concepts", []):
                                recommended_next = {"lesson_id": lid, "reason": f"strengthen {lowest.name}"}
                                break
                    if not recommended_next and lessons_out:
                        recommended_next = {"lesson_id": lessons_out[0].id, "reason": "practice core skills"}

        # Spaced repetition due dates
        spaced_repetition_due = falkor_spaced_rep
        if not spaced_repetition_due and self._memory_spaced_repetition:
            spaced_repetition_due = [
                {
                    "concept_id": cid,
                    "due_at": datetime.fromtimestamp(int(info["due_date"]), tz=timezone.utc).isoformat(),
                    "interval_days": round(float(info["interval_hours"]) / 24.0, 1),
                }
                for cid, info in sorted(self._memory_spaced_repetition.items(), key=lambda x: x[1].get("due_date", 0))
            ]

        # Timeline
        timeline = []
        for a in attempts[-15:]:
            passed = a.get("passed", False)
            lid = a.get("lesson_id", "lesson")
            timeline.append({
                "ts": a.get("ts", int(time.time())),
                "kind": "pass" if passed else "fail",
                "label": f"Tests passed ({lid})" if passed else f"Mistake in {lid}: {a.get('mistake_id', 'bug')}",
            })
        if not timeline:
            timeline.append({"ts": int(time.time()), "kind": "init", "label": "Started Socratic Pet"})

        evo = self.compute_evolution(student_id)

        progress_obj = Progress(
            mode=active_mode,
            streak=streak,
            daily_activity=daily_activity,
            skill_radar=skill_radar,
            concepts=concepts_out,
            recent_mistakes=recent_mistakes,
            patterns=patterns,
            hint_dependency=hint_dependency,
            lessons=lessons_out,
            recommended_next=recommended_next,
            spaced_repetition_due=spaced_repetition_due,
            timeline=timeline,
            evolution=evo,
        )
        return progress_obj.model_dump()

    # ------------------------------------------------------- Live Graph Snapshot
    def get_graph_snapshot(self, student_id: str = "local-user") -> Dict[str, Any]:
        """Return {nodes, edges} graph snapshot for the UI Live Graph."""
        self.ingest_concepts()
        g = self._get_graph()
        if g:
            try:
                res = g.query(
                    """
                    MATCH (n)
                    OPTIONAL MATCH (n)-[r]->(m)
                    RETURN n, r, m LIMIT 200
                    """
                )
                nodes_map: Dict[str, GraphNode] = {}
                edges_list: List[GraphEdge] = []
                for row in res.result_set:
                    n, r, m = row[0], row[1], row[2]
                    if n is not None:
                        props = dict(getattr(n, "properties", {}))
                        nid = str(props.get("id") or getattr(n, "id", ""))
                        labels = getattr(n, "labels", ["Node"])
                        clean_props = {k: v for k, v in props.items() if k != "embedding"}
                        nodes_map[nid] = GraphNode(id=nid, label=labels[0] if labels else "Node", props=clean_props)
                    if m is not None:
                        props = dict(getattr(m, "properties", {}))
                        mid = str(props.get("id") or getattr(m, "id", ""))
                        labels = getattr(m, "labels", ["Node"])
                        clean_props = {k: v for k, v in props.items() if k != "embedding"}
                        nodes_map[mid] = GraphNode(id=mid, label=labels[0] if labels else "Node", props=clean_props)
                    if r is not None and n is not None and m is not None:
                        rtype = getattr(r, "relation", "CONNECTED_TO")
                        nid = str(getattr(n, "properties", {}).get("id") or getattr(n, "id", ""))
                        mid = str(getattr(m, "properties", {}).get("id") or getattr(m, "id", ""))
                        edges_list.append(GraphEdge(from_node=nid, to_node=mid, type=rtype))
                if nodes_map:
                    return {
                        "nodes": [n.model_dump() for n in nodes_map.values()],
                        "edges": [e.model_dump(by_alias=True) for e in edges_list],
                    }
            except Exception:
                log.exception("FalkorDB graph snapshot query failed")

        # Fallback in-memory graph representation
        default_nodes = [
            GraphNode(id="student:local-user", label="Student", props={"name": "Local Developer"}),
            GraphNode(id="lesson:binary-search", label="Lesson", props={"title": "Binary Search", "track": "dsa"}),
            GraphNode(id="concept:array_indexing", label="Concept", props={"name": "Array Indexing"}),
            GraphNode(id="concept:loop_invariants", label="Concept", props={"name": "Loop Invariants"}),
            GraphNode(id="mistake:boundary_off_by_one", label="Mistake", props={"id": "boundary_off_by_one"}),
        ]
        default_edges = [
            GraphEdge(from_node="concept:binary_search", to_node="concept:loop_invariants", type="REQUIRES"),
            GraphEdge(from_node="concept:binary_search", to_node="concept:array_indexing", type="REQUIRES"),
            GraphEdge(from_node="mistake:boundary_off_by_one", to_node="concept:array_indexing", type="INVOLVES"),
            GraphEdge(from_node="student:local-user", to_node="mistake:boundary_off_by_one", type="MADE"),
        ]
        return {
            "nodes": [n.model_dump() for n in default_nodes],
            "edges": [e.model_dump(by_alias=True) for e in default_edges],
        }

    def get_graph(self, student_id: str = "local-user") -> Dict[str, Any]:
        """Alias for get_graph_snapshot to satisfy REST /api/graph contract."""
        return self.get_graph_snapshot(student_id)
