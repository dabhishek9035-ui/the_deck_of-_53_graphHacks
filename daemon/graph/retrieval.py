"""
Hybrid search, graph path traversal, and pattern memory for Team C.

This module provides:
1. `hybrid_query`: Unifies FalkorDB vector index search with Cypher graph filtering
   to retrieve explanation chunks for weak prerequisite concepts (mastery score < 0.6).
2. `find_bottleneck`: Traverses variable-length prerequisites (`REQUIRES*1..3`) backwards
   to uncover root weakness concepts.
3. `query_pattern_memory`: Aggregates historical student mistakes to detect recurring failure patterns.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from falkordb import Graph
except ImportError:
    Graph = Any  # type: ignore[misc,assignment]

try:
    from daemon.graph.ingest import embed_text
except ImportError:
    from graph.ingest import embed_text

logger = logging.getLogger(__name__)

WEAK_CONCEPT_THRESHOLD = 0.6
DEFAULT_TOP_K = 5


def _extract_node_dict(node: Any) -> Dict[str, Any]:
    """Normalize a FalkorDB Node object or dictionary to Contract 4 format."""
    if hasattr(node, "properties"):
        props = dict(node.properties)
        node_id = props.get("id") or str(getattr(node, "id", ""))
        labels = getattr(node, "labels", [])
        label = labels[0] if labels else "Node"
        # Omit raw binary/embedding from frontend trace to keep payload compact
        clean_props = {k: v for k, v in props.items() if k != "embedding"}
        return {"id": str(node_id), "label": label, "props": clean_props}
    elif isinstance(node, dict):
        return {
            "id": str(node.get("id", "")),
            "label": node.get("label", "Node"),
            "props": {k: v for k, v in node.get("props", node).items() if k != "embedding"},
        }
    return {"id": str(node), "label": "Unknown", "props": {}}


def _extract_edge_dict(src: str, dest: str, rel_type: str) -> Dict[str, str]:
    """Format edge representation for Contract 4."""
    return {"from": str(src), "to": str(dest), "type": str(rel_type)}


def hybrid_query(
    graph: Graph,
    student_id: str,
    failed_concept_id: str,
    query_text: str,
    top_k: int = DEFAULT_TOP_K,
) -> Dict[str, Any]:
    """
    Perform a unified hybrid search combining vector similarity and Cypher graph filtering.

    Executes `db.idx.vector.queryNodes` on Chunk nodes and filters chunks explaining
    concepts where the student's mastery score is under 0.6 (or unattempted).
    Ranks explanation chunks by vector distance and records query latency in milliseconds.

    Args:
        graph: FalkorDB Graph instance.
        student_id: ID of the student.
        failed_concept_id: ID of the concept the student struggled with.
        query_text: Query text or error message for vector semantic retrieval.
        top_k: Maximum number of candidate chunks to evaluate (default: 5).

    Returns:
        Dictionary adhering to Contract 4 graph_trace:
        {
            "cypher": str,
            "query_ms": float,
            "nodes": List[Dict[str, Any]],
            "edges": List[Dict[str, str]],
            "chunks": List[Dict[str, Any]]
        }
    """
    start_time = time.perf_counter()

    # Step 1: Generate query embedding vector (768-dim)
    query_vector = embed_text(query_text)

    # Step 2: Formulate single hybrid Cypher query combining vector procedure and graph traversal
    hybrid_cypher = """
    CALL db.idx.vector.queryNodes('Chunk', 'embedding', $top_k, vecf32($query_embedding))
    YIELD node AS chunk, score
    MATCH (chunk)-[:EXPLAINS]->(lesson:Lesson)
    MATCH (failed:Concept {id: $failed_concept_id})
    OPTIONAL MATCH (failed)-[:REQUIRES*1..3]->(prereq:Concept)
    OPTIONAL MATCH (lesson)-[:TEACHES]->(targetConcept:Concept)
    WITH chunk, score, lesson, failed,
         coalesce(prereq, targetConcept, failed) AS concept
    OPTIONAL MATCH (s:Student {id: $student_id})-[m:MASTERED]->(concept)
    WHERE (m.score IS NULL OR m.score < $threshold)
    RETURN chunk, score, lesson, concept, coalesce(m.score, 0.0) AS mastery_score
    ORDER BY score ASC
    LIMIT $top_k
    """

    params: Dict[str, Any] = {
        "top_k": top_k,
        "query_embedding": query_vector,
        "failed_concept_id": failed_concept_id,
        "student_id": student_id,
        "threshold": WEAK_CONCEPT_THRESHOLD,
    }

    chunks: List[Dict[str, Any]] = []
    nodes_map: Dict[str, Dict[str, Any]] = {}
    edges_set: Set[Tuple[str, str, str]] = set()
    executed_cypher = hybrid_cypher.strip()

    try:
        res = graph.query(hybrid_cypher, params)
        records = getattr(res, "result_set", []) or []
    except Exception as exc:
        logger.warning("FalkorDB vector procedure failed (%s). Executing graph traversal fallback.", exc)
        # Fallback without vecf32 procedure for mock or scalar mode
        executed_cypher = """
        MATCH (chunk:Chunk)-[:EXPLAINS]->(lesson:Lesson)
        MATCH (failed:Concept {id: $failed_concept_id})
        OPTIONAL MATCH (failed)-[:REQUIRES*1..3]->(prereq:Concept)
        OPTIONAL MATCH (lesson)-[:TEACHES]->(targetConcept:Concept)
        WITH chunk, lesson, failed,
             coalesce(prereq, targetConcept, failed) AS concept
        OPTIONAL MATCH (s:Student {id: $student_id})-[m:MASTERED]->(concept)
        WHERE (m.score IS NULL OR m.score < $threshold)
        RETURN chunk, 0.1 AS score, lesson, concept, coalesce(m.score, 0.0) AS mastery_score
        LIMIT $top_k
        """.strip()

        fallback_params = {
            "top_k": top_k,
            "failed_concept_id": failed_concept_id,
            "student_id": student_id,
            "threshold": WEAK_CONCEPT_THRESHOLD,
        }
        res = graph.query(executed_cypher, fallback_params)
        records = getattr(res, "result_set", []) or []

    # Step 3: Parse results and construct graph trace
    for row in records:
        chunk_node = row[0]
        score = float(row[1]) if row[1] is not None else 0.0
        lesson_node = row[2]
        concept_node = row[3]
        mastery_score = float(row[4]) if row[4] is not None else 0.0

        chunk_dict = _extract_node_dict(chunk_node)
        lesson_dict = _extract_node_dict(lesson_node)
        concept_dict = _extract_node_dict(concept_node)

        chunk_id = chunk_dict["id"]
        lesson_id = lesson_dict["id"]
        concept_id = concept_dict["id"]

        nodes_map[chunk_id] = chunk_dict
        nodes_map[lesson_id] = lesson_dict
        nodes_map[concept_id] = concept_dict

        # Trace relationships
        edges_set.add((chunk_id, lesson_id, "EXPLAINS"))
        edges_set.add((lesson_id, concept_id, "TEACHES"))

        chunks.append({
            "chunk_id": chunk_id,
            "content": chunk_dict["props"].get("content", ""),
            "score": round(score, 4),
            "lesson_id": lesson_id,
            "concept_id": concept_id,
            "mastery_score": round(mastery_score, 4),
        })

    # Always ensure the failed concept node itself is in the trace
    if failed_concept_id not in nodes_map:
        nodes_map[failed_concept_id] = {
            "id": failed_concept_id,
            "label": "Concept",
            "props": {"id": failed_concept_id},
        }

    query_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return {
        "cypher": executed_cypher,
        "query_ms": query_ms,
        "nodes": list(nodes_map.values()),
        "edges": [_extract_edge_dict(src, dest, rel) for src, dest, rel in edges_set],
        "chunks": chunks,
    }


def find_bottleneck(
    graph: Graph,
    failed_concept_id: str,
    max_depth: int = 3,
) -> Dict[str, Any]:
    """
    Execute a variable-length path traversal (REQUIRES*1..3) walking backwards
    from the failed concept to locate the root prerequisite bottleneck.

    Args:
        graph: FalkorDB Graph instance.
        failed_concept_id: Concept identifier that failed.
        max_depth: Maximum variable-length traversal depth (default: 3).

    Returns:
        Dictionary containing root concept, traversal path, depth, and query metrics:
        {
            "failed_concept_id": str,
            "bottleneck_concept_id": str,
            "depth": int,
            "path": List[str],
            "cypher": str,
            "query_ms": float,
            "nodes": List[Dict[str, Any]],
            "edges": List[Dict[str, str]]
        }
    """
    start_time = time.perf_counter()

    cypher = f"""
    MATCH path = (failed:Concept {{id: $failed_concept_id}})-[:REQUIRES*1..{max_depth}]->(root:Concept)
    WHERE NOT (root)-[:REQUIRES]->(:Concept)
    RETURN root, length(path) AS depth, nodes(path) AS path_nodes
    ORDER BY depth DESC
    LIMIT 1
    """.strip()

    params = {"failed_concept_id": failed_concept_id}
    res = graph.query(cypher, params)
    records = getattr(res, "result_set", []) or []

    # If no root node without outbound requirements exists, fallback to deepest reached concept
    if not records:
        cypher = f"""
        MATCH path = (failed:Concept {{id: $failed_concept_id}})-[:REQUIRES*1..{max_depth}]->(prereq:Concept)
        RETURN prereq AS root, length(path) AS depth, nodes(path) AS path_nodes
        ORDER BY depth DESC
        LIMIT 1
        """.strip()
        res = graph.query(cypher, params)
        records = getattr(res, "result_set", []) or []

    bottleneck_id = failed_concept_id
    depth = 0
    path_ids = [failed_concept_id]
    nodes_map: Dict[str, Dict[str, Any]] = {
        failed_concept_id: {"id": failed_concept_id, "label": "Concept", "props": {"id": failed_concept_id}}
    }
    edges_set: Set[Tuple[str, str, str]] = set()

    if records:
        row = records[0]
        root_node = row[0]
        depth = int(row[1]) if row[1] is not None else 0
        path_nodes = row[2] if len(row) > 2 and row[2] is not None else []

        root_dict = _extract_node_dict(root_node)
        bottleneck_id = root_dict["id"]
        nodes_map[bottleneck_id] = root_dict

        extracted_ids: List[str] = []
        for n in path_nodes:
            nd = _extract_node_dict(n)
            nodes_map[nd["id"]] = nd
            extracted_ids.append(nd["id"])

        if extracted_ids:
            path_ids = extracted_ids

        # Construct edges along the sequence
        for i in range(len(path_ids) - 1):
            edges_set.add((path_ids[i], path_ids[i + 1], "REQUIRES"))

    query_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return {
        "failed_concept_id": failed_concept_id,
        "bottleneck_concept_id": bottleneck_id,
        "depth": depth,
        "path": path_ids,
        "cypher": cypher,
        "query_ms": query_ms,
        "nodes": list(nodes_map.values()),
        "edges": [_extract_edge_dict(src, dest, rel) for src, dest, rel in edges_set],
    }


def query_pattern_memory(
    graph: Graph,
    student_id: str,
    mistake_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query student historical attempt memory to count recurring mistakes and track patterns.

    Args:
        graph: FalkorDB Graph instance.
        student_id: ID of the student.
        mistake_id: Optional specific mistake ID to check. If None, checks all mistakes.

    Returns:
        Dictionary containing recurrence counts, timestamps, and lessons:
        {
            "student_id": str,
            "mistake_id": Optional[str],
            "recurrence_count": int,
            "is_recurring": bool,
            "timestamps": List[int],
            "lessons": List[str]
        }
    """
    start_time = time.perf_counter()

    if mistake_id:
        cypher = """
        MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(a:Attempt)-[:MADE_MISTAKE]->(m:Mistake {id: $mistake_id})
        OPTIONAL MATCH (a)-[:FOR_LESSON]->(l:Lesson)
        RETURN count(a) AS recurrence_count,
               collect(a.timestamp) AS timestamps,
               collect(DISTINCT l.id) AS lessons
        """.strip()
        params = {"student_id": student_id, "mistake_id": mistake_id}
        res = graph.query(cypher, params)
        records = getattr(res, "result_set", []) or []

        recurrence_count = 0
        timestamps: List[int] = []
        lessons: List[str] = []

        if records:
            row = records[0]
            recurrence_count = int(row[0]) if row[0] is not None else 0
            timestamps = [int(t) for t in row[1] if t is not None] if len(row) > 1 and row[1] else []
            lessons = [str(lid) for lid in row[2] if lid is not None] if len(row) > 2 and row[2] else []

        query_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return {
            "student_id": student_id,
            "mistake_id": mistake_id,
            "recurrence_count": recurrence_count,
            "is_recurring": recurrence_count >= 2,
            "timestamps": timestamps,
            "lessons": lessons,
            "query_ms": query_ms,
            "cypher": cypher,
        }

    # Aggregate view over all mistakes
    cypher = """
    MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(a:Attempt)-[:MADE_MISTAKE]->(m:Mistake)
    OPTIONAL MATCH (a)-[:FOR_LESSON]->(l:Lesson)
    RETURN m.id AS mistake_id,
           count(a) AS recurrence_count,
           collect(a.timestamp) AS timestamps,
           collect(DISTINCT l.id) AS lessons
    ORDER BY recurrence_count DESC
    """.strip()
    params = {"student_id": student_id}
    res = graph.query(cypher, params)
    records = getattr(res, "result_set", []) or []

    patterns: List[Dict[str, Any]] = []
    total_mistakes = 0

    for row in records:
        mid = str(row[0])
        count = int(row[1]) if row[1] is not None else 0
        ts_list = [int(t) for t in row[2] if t is not None] if len(row) > 2 and row[2] else []
        l_list = [str(lid) for lid in row[3] if lid is not None] if len(row) > 3 and row[3] else []
        total_mistakes += count
        patterns.append({
            "mistake_id": mid,
            "recurrence_count": count,
            "is_recurring": count >= 2,
            "timestamps": ts_list,
            "lessons": l_list,
        })

    query_ms = round((time.perf_counter() - start_time) * 1000, 2)
    return {
        "student_id": student_id,
        "mistake_id": None,
        "total_mistakes": total_mistakes,
        "patterns": patterns,
        "query_ms": query_ms,
        "cypher": cypher,
    }
