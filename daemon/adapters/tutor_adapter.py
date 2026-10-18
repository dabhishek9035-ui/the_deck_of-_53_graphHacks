"""Tutor adapter: bridges finding handling, Socratic LLM prompting, guardrails, and telemetry."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional

from contracts import GraphTrace, TutorOutput

log = logging.getLogger("tutor.adapters.tutor")

# Strict Daemon-level Output Safeguard:
# Independent of tutor's internal guard, the pet NEVER outputs code blocks or direct fixes.
CODE_FENCE_RX = re.compile(r"```", re.IGNORECASE)
DIRECT_FIX_RX = [
    re.compile(r"\b(?:change|replace|substitute|switch)\s+.+?\s+(?:to|with|for|by)\b", re.IGNORECASE),
    re.compile(r"\b(?:use|try using|consider using)\s+.+?\s+instead of\b", re.IGNORECASE),
    re.compile(r"\b(?:set|update)\s+[\w\.\(\)\[\]]+\s+to\s+[^.!?\n]+", re.IGNORECASE),
    re.compile(r"\b(?:the fix|the solution|the correct code)\s+(?:is|would be)\b", re.IGNORECASE),
    re.compile(r"\b(?:just|simply)\s+(?:write|put|add|insert|delete|remove|change|replace)\b", re.IGNORECASE),
    re.compile(r"\b[a-zA-Z_]\w*\s*(?:=|\+=|-=|\*=|/=|:=)\s*(?:\(|\[|\{|\"|\'|-?\d+|[a-zA-Z_]\w*\(|[a-zA-Z_]\w*\s*[\+\-\*/%])"),
    re.compile(r"\breturn\s+(?:-?\d+|true|false|null|none|nil|[-+*/\w]+\s*;|\w+\s*\([^)]*\)|\w+;|\"[^\"]*\"|'[^']*')", re.IGNORECASE),
]

FALLBACK_QUESTIONS = {
    "boundary_off_by_one": "Consider what happens when the search window reduces to a single element. Does your loop still inspect it?",
    "infinite_loop": "What condition in your code guarantees that the loop advances toward the stopping condition on every iteration?",
    "array_indexing": "What are the legal index boundaries for an array of size N in this zero-indexed sequence?",
    "guess_and_check": "Let's pause. Before modifying another variable, what exact state caused the previous attempt to fail?",
    "general": "Can you walk me through what you expect this part of the algorithm to do on the smallest input you can think of?",
}


def sanitize_final_output(question: str, mistake_id: Optional[str] = None, require_question: bool = True) -> str:
    """Final independent safeguard against code leakage."""
    q = (question or "").strip()
    if not q or CODE_FENCE_RX.search(q):
        return FALLBACK_QUESTIONS.get(mistake_id or "general", FALLBACK_QUESTIONS["general"])
    for rx in DIRECT_FIX_RX:
        if rx.search(q):
            log.warning("Daemon safeguard caught direct fix phrasing in output: %r", q)
            return FALLBACK_QUESTIONS.get(mistake_id or "general", FALLBACK_QUESTIONS["general"])
    if require_question:
        if q.count("?") > 1:
            # Keep the final Socratic question sentence if multiple were generated
            parts = [p.strip() for p in q.split("?") if p.strip()]
            if parts:
                q = parts[-1] + "?"
        elif q.count("?") == 0:
            q = q.rstrip(".! ") + "?"
    return q[:600]


class TutorAdapter:
    def __init__(self, graph_adapter, model: str = "qwen2.5-coder:7b") -> None:
        self.graph_adapter = graph_adapter
        self.model = model
        self._mistake_history: Dict[Tuple[str, str], int] = {}

    def handle(self, finding: Dict[str, Any], student_id: str = "local-user") -> Dict[str, Any]:
        """Handle a Finding and return Contract 4 TutorOutput dict."""
        t0 = time.monotonic()
        mid = finding.get("mistake_id") or "general"
        kind = finding.get("kind", "test_failure")
        concept_ids = finding.get("concept_ids") or []
        primary_concept = concept_ids[0] if concept_ids else "logic"

        # Track mistake recurrence for progressive hint escalation and stressed pet state
        key = (student_id, mid)
        recurrence = self._mistake_history.get(key, 0)
        self._mistake_history[key] = recurrence + 1

        # State and hint level rules:
        # 1. Guess spam -> hint level 0, detective
        # 2. 3rd recurrence (recurrence >= 2) -> stressed / detective, hint level 3 or 4
        # 3. Otherwise -> asking, hint level scales 1..4
        if kind == "guess_spam":
            hint_level = 0
            pet_state = "detective"
        elif recurrence >= 2:
            hint_level = min(4, recurrence + 1)
            pet_state = "stressed"
        else:
            hint_level = min(4, max(1, recurrence + 1))
            pet_state = "asking"

        g = self.graph_adapter._get_graph()
        raw_output = None
        query_ms = None

        if g:
            try:
                from tutor.engine import handle_tutor
                q_start = time.perf_counter()
                raw_output = handle_tutor(
                    graph=g,
                    finding=finding,
                    student_id=student_id,
                    model=self.model,
                )
                query_ms = round((time.perf_counter() - q_start) * 1000, 2)
            except Exception:
                log.exception("tutor.engine.handle_tutor failed; falling back to curated seed question")

        if not raw_output:
            query_ms = round((time.perf_counter() - t0) * 1000, 2)
            fallback_q = FALLBACK_QUESTIONS.get(mid, FALLBACK_QUESTIONS["general"])
            raw_output = {
                "question": fallback_q,
                "hint_level": hint_level,
                "targets_concept": primary_concept,
                "mistake_id": mid,
                "pet_state": pet_state,
                "graph_trace": {
                    "cypher": "MATCH (m:Mistake {id:$mid})-[:INVOLVES]->(c:Concept) RETURN m, c",
                    "query_ms": query_ms,
                    "nodes": [
                        {"id": f"mistake:{mid}", "label": "Mistake", "props": {"id": mid}},
                        {"id": f"concept:{primary_concept}", "label": "Concept", "props": {"id": primary_concept}},
                    ],
                    "edges": [
                        {"from": f"mistake:{mid}", "to": f"concept:{primary_concept}", "type": "INVOLVES"}
                    ],
                },
            }

        # Override pet state if recurrence was detected
        if recurrence >= 2 and kind != "guess_spam":
            raw_output["pet_state"] = "stressed"
            raw_output["hint_level"] = max(3, raw_output.get("hint_level", 3))

        # Enforce final safeguard on the question
        safe_q = sanitize_final_output(raw_output.get("question", ""), mid)
        raw_output["question"] = safe_q

        # Ensure measured query_ms is attached if not already provided
        if "graph_trace" in raw_output and isinstance(raw_output["graph_trace"], dict):
            if raw_output["graph_trace"].get("query_ms") is None:
                raw_output["graph_trace"]["query_ms"] = query_ms

        return raw_output

    def handle_reply(
        self,
        text: str,
        student_id: str,
        last_finding: Optional[Dict[str, Any]] = None,
        active_code: Optional[str] = None,
        active_file: Optional[str] = None,
        active_language: Optional[str] = None,
        cursor: Optional[Dict[str, Any]] = None,
        lesson: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Process student answer to pet's Socratic question dynamically via engine."""
        g = self.graph_adapter._get_graph() if hasattr(self.graph_adapter, "_get_graph") else None
        try:
            from tutor.engine import handle_reply as _engine_reply
        except ImportError:
            from daemon.tutor.engine import handle_reply as _engine_reply

        raw = _engine_reply(
            text=text,
            student_id=student_id,
            last_finding=last_finding,
            graph=g,
            model=self.model,
            active_code=active_code,
            active_file=active_file,
            active_language=active_language,
            cursor=cursor,
            lesson=lesson,
        )
        mid = (last_finding or {}).get("mistake_id")
        raw["question"] = sanitize_final_output(raw.get("question", ""), mid)
        return raw

    def generate_idle_hint(
        self,
        active_code: str,
        active_file: str,
        lesson: Optional[Dict[str, Any]] = None,
        student_id: str = "local-user",
    ) -> Dict[str, Any]:
        """Generate a non-question guiding hint based on active code via Socratic engine & Qwen model."""
        try:
            from tutor.engine import generate_idle_hint as _engine_idle_hint
        except ImportError:
            from daemon.tutor.engine import generate_idle_hint as _engine_idle_hint

        return _engine_idle_hint(
            active_code=active_code,
            active_file=active_file,
            lesson=lesson,
            student_id=student_id,
            model=self.model,
        )

    def reset_recurrence(self, student_id: str, mistake_id: Optional[str] = None) -> None:
        """Called when student passes tests, lowering recurrence and hint level."""
        if mistake_id:
            key = (student_id, mistake_id)
            if key in self._mistake_history:
                self._mistake_history[key] = 0
        else:
            for k in list(self._mistake_history.keys()):
                if k[0] == student_id:
                    self._mistake_history[k] = 0
