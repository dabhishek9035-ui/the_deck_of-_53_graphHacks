"""STUB for C's graph module. get_progress/get_graph are additions the REST endpoints need (see README)."""
import logging

log = logging.getLogger("tutor.graph_stub")


def ingest_lesson(path):
    log.info("stub ingest_lesson(%s)", path)


def record_attempt(*a, **kw):
    return None


def update_mastery(*a, **kw):
    return None


def get_progress(student_id, mode=None):
    return {"student_id": student_id, "mode": mode or "dsa", "mastery": [
        {"concept_id": "array_indexing", "score": 0.4},
        {"concept_id": "loop_invariants", "score": 0.2},
        {"concept_id": "binary_search", "score": 0.1}]}


def get_graph(student_id):
    return {"nodes": [{"id": "c:array_indexing", "label": "Concept", "props": {"name": "array_indexing"}},
                      {"id": "c:loop_invariants", "label": "Concept", "props": {"name": "loop_invariants"}}],
            "edges": [{"from": "c:array_indexing", "to": "c:loop_invariants", "type": "PREREQUISITE_OF"}]}
