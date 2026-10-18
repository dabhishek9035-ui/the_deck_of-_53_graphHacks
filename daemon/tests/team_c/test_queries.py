"""
Unit tests for graph path traversal and bottleneck detection (daemon/graph/retrieval.py).

Verifies that find_bottleneck:
1. Traverses backwards along variable-length REQUIRES paths (*1..3) to identify root concepts.
2. Correctly identifies multi-hop root bottlenecks (binary_search -> array_indexing -> variables).
3. Handles single-hop dependencies (array_indexing -> variables).
4. Handles isolated concepts (depth = 0, root is self).
5. Returns proper Contract 4 graph telemetry (nodes, edges, cypher, query_ms).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
import pytest

from daemon.graph.retrieval import find_bottleneck, query_pattern_memory


class MockGraphNode:
    """Mock FalkorDB Node object for isolated unit testing."""

    def __init__(self, node_id: str, label: str = "Concept", properties: Optional[Dict[str, Any]] = None):
        self.id = node_id
        self.labels = [label]
        self.properties = properties or {"id": node_id}


class MockQueryResult:
    """Mock FalkorDB QueryResult."""

    def __init__(self, result_set: List[Any]):
        self.result_set = result_set


class TestFindBottleneck:
    """Test suite for backward prerequisite path traversal."""

    def test_multi_hop_bottleneck(self):
        """
        Verify multi-hop traversal:
        binary_search -> array_indexing -> variables
        Root weakness bottleneck should be 'variables' at depth 2.
        """
        root_node = MockGraphNode("variables", label="Concept", properties={"id": "variables", "name": "Variables"})
        mid_node = MockGraphNode("array_indexing", label="Concept", properties={"id": "array_indexing"})
        start_node = MockGraphNode("binary_search", label="Concept", properties={"id": "binary_search"})

        mock_graph = MagicMock()
        # Mock returning (root, depth=2, path_nodes=[binary_search, array_indexing, variables])
        mock_graph.query.return_value = MockQueryResult([
            (root_node, 2, [start_node, mid_node, root_node])
        ])

        result = find_bottleneck(mock_graph, "binary_search", max_depth=3)

        assert result["failed_concept_id"] == "binary_search"
        assert result["bottleneck_concept_id"] == "variables"
        assert result["depth"] == 2
        assert result["path"] == ["binary_search", "array_indexing", "variables"]
        assert "REQUIRES*1..3" in result["cypher"]
        assert result["query_ms"] >= 0.0

        # Verify graph trace contains nodes and sequence edges
        node_ids = {n["id"] for n in result["nodes"]}
        assert "binary_search" in node_ids
        assert "array_indexing" in node_ids
        assert "variables" in node_ids

        edge_tuples = {(e["from"], e["to"], e["type"]) for e in result["edges"]}
        assert ("binary_search", "array_indexing", "REQUIRES") in edge_tuples
        assert ("array_indexing", "variables", "REQUIRES") in edge_tuples

    def test_single_hop_bottleneck(self):
        """
        Verify single-hop traversal:
        array_indexing -> variables
        Root weakness bottleneck should be 'variables' at depth 1.
        """
        root_node = MockGraphNode("variables", label="Concept", properties={"id": "variables"})
        start_node = MockGraphNode("array_indexing", label="Concept", properties={"id": "array_indexing"})

        mock_graph = MagicMock()
        mock_graph.query.return_value = MockQueryResult([
            (root_node, 1, [start_node, root_node])
        ])

        result = find_bottleneck(mock_graph, "array_indexing", max_depth=2)

        assert result["failed_concept_id"] == "array_indexing"
        assert result["bottleneck_concept_id"] == "variables"
        assert result["depth"] == 1
        assert result["path"] == ["array_indexing", "variables"]

    def test_isolated_concept_without_prerequisites(self):
        """
        An isolated concept has no prerequisites.
        Root weakness bottleneck defaults to the concept itself at depth 0.
        """
        mock_graph = MagicMock()
        # No paths returned by variable-length query
        mock_graph.query.return_value = MockQueryResult([])

        result = find_bottleneck(mock_graph, "variables", max_depth=3)

        assert result["failed_concept_id"] == "variables"
        assert result["bottleneck_concept_id"] == "variables"
        assert result["depth"] == 0
        assert result["path"] == ["variables"]
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "variables"
        assert len(result["edges"]) == 0


class TestPatternMemory:
    """Test suite for student mistake frequency tracking."""

    def test_query_pattern_memory_recurring(self):
        """Verify mistake pattern flags is_recurring when recurrence count >= 2."""
        mock_graph = MagicMock()
        # Row: (recurrence_count, timestamps, lessons)
        mock_graph.query.return_value = MockQueryResult([
            (3, [1730000000, 1730000500, 1730001000], ["binary-search"])
        ])

        result = query_pattern_memory(mock_graph, "local-user", "boundary_off_by_one")

        assert result["student_id"] == "local-user"
        assert result["mistake_id"] == "boundary_off_by_one"
        assert result["recurrence_count"] == 3
        assert result["is_recurring"] is True
        assert len(result["timestamps"]) == 3
        assert result["lessons"] == ["binary-search"]

    def test_query_pattern_memory_first_occurrence(self):
        """Verify first occurrence (recurrence = 1) is not flagged as recurring."""
        mock_graph = MagicMock()
        mock_graph.query.return_value = MockQueryResult([
            (1, [1730000000], ["binary-search"])
        ])

        result = query_pattern_memory(mock_graph, "local-user", "infinite_loop")

        assert result["recurrence_count"] == 1
        assert result["is_recurring"] is False


class TestTutorEngineContracts:
    """Test suite ensuring strict adherence to tutor engine contracts."""

    def test_targets_concept_strictly_controlled_by_engine(self, monkeypatch):
        """Verify LLM cannot overwrite targets_concept with hallucinated concepts."""
        from daemon.tutor.engine import handle_tutor

        mock_graph = MagicMock()
        mock_graph.query.return_value = MockQueryResult([])

        # Mock Ollama returning a hallucinated targets_concept
        monkeypatch.setattr(
            "daemon.tutor.engine._call_ollama_generate",
            lambda *args, **kwargs: {
                "question": "What happens when left == right?",
                "targets_concept": "hallucinated_quantum_computing_concept",
            },
        )
        monkeypatch.setattr(
            "daemon.tutor.engine.hybrid_query",
            lambda *args, **kwargs: {"query_ms": 1.23, "nodes": [], "edges": [], "chunks": []},
        )

        finding = {
            "kind": "test_failure",
            "mistake_id": "boundary_off_by_one",
            "concept_ids": ["array_indexing"],
            "file": "solution.py",
            "line": 14,
            "evidence": "IndexError",
        }

        output = handle_tutor(mock_graph, finding, student_id="local-user")
        assert output["targets_concept"] == "array_indexing"
        assert output["targets_concept"] != "hallucinated_quantum_computing_concept"
        assert output["graph_trace"]["query_ms"] == 1.23

    def test_query_ms_defaults_to_none_when_unmeasured(self, monkeypatch):
        """Verify query_ms is None (null) when missing rather than defaulting to 0.0."""
        from daemon.tutor.engine import handle_tutor

        mock_graph = MagicMock()
        mock_graph.query.return_value = MockQueryResult([])

        monkeypatch.setattr(
            "daemon.tutor.engine._call_ollama_generate",
            lambda *args, **kwargs: {
                "question": "What happens when the array is empty?",
            },
        )
        # hybrid_query missing query_ms key
        monkeypatch.setattr(
            "daemon.tutor.engine.hybrid_query",
            lambda *args, **kwargs: {"nodes": [], "edges": [], "chunks": []},
        )

        finding = {
            "kind": "test_failure",
            "mistake_id": "boundary_off_by_one",
            "concept_ids": ["array_indexing"],
        }

        output = handle_tutor(mock_graph, finding, student_id="local-user")
        assert output["graph_trace"]["query_ms"] is None
