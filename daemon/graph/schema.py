"""
FalkorDB schema definition and connection utilities for Team C.

This module provides connection helpers and schema initialization for the
Socratic Code-Tutor Pet knowledge graph:
- Graph connection and selection.
- Unique constraints on Student, Lesson, Concept, and Mistake nodes (`id IS UNIQUE`).
- Vector index on Chunk nodes for the `embedding` property (dimension: 768, metric: cosine).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Union

try:
    from falkordb import FalkorDB, Graph
except ImportError:
    # Fallback type aliases if falkordb is not installed in the current environment
    FalkorDB = Any  # type: ignore[misc,assignment]
    Graph = Any  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

# Default connection and graph configuration
DEFAULT_GRAPH_NAME = "socratic_tutor"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379

# Entities that enforce unique ID constraints
UNIQUE_NODE_LABELS: Sequence[str] = ("Student", "Lesson", "Concept", "Mistake")

# Vector index defaults
DEFAULT_VECTOR_LABEL = "Chunk"
DEFAULT_VECTOR_PROPERTY = "embedding"
DEFAULT_VECTOR_DIMENSION = 768
DEFAULT_VECTOR_SIMILARITY = "cosine"


def get_client(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    username: Optional[str] = None,
    password: Optional[str] = None,
    **kwargs: Any,
) -> FalkorDB:
    """
    Establish a connection to the local or remote FalkorDB instance.

    Args:
        host: Hostname of the FalkorDB server (default: localhost).
        port: Port of the FalkorDB server (default: 6379).
        username: Optional Redis/FalkorDB username.
        password: Optional Redis/FalkorDB password.
        **kwargs: Additional parameters forwarded to the FalkorDB client.

    Returns:
        A FalkorDB client instance.
    """
    logger.info("Connecting to FalkorDB at %s:%s", host, port)
    return FalkorDB(host=host, port=port, username=username, password=password, **kwargs)


def get_graph(
    client: Optional[FalkorDB] = None,
    graph_name: str = DEFAULT_GRAPH_NAME,
) -> Graph:
    """
    Select and return the specified FalkorDB graph namespace.

    Args:
        client: Active FalkorDB client instance. If None, connects using default host:port.
        graph_name: Name of the graph database namespace (default: "socratic_tutor").

    Returns:
        The selected Graph instance.
    """
    if client is None:
        client = get_client()

    logger.debug("Selecting FalkorDB graph: %s", graph_name)
    return client.select_graph(graph_name)


def _is_already_exists_error(error: Exception) -> bool:
    """Check if an exception indicates that the index or constraint already exists."""
    msg = str(error).lower()
    return "already exists" in msg or "duplicate" in msg or "constraint exists" in msg


def init_schema(
    graph: Graph,
    embedding_dim: int = DEFAULT_VECTOR_DIMENSION,
    similarity_function: str = DEFAULT_VECTOR_SIMILARITY,
) -> Dict[str, Any]:
    """
    Initialize schema constraints and indexes for the Socratic Tutor knowledge graph.

    Idempotently sets up:
    1. Unique ID constraints for Student, Lesson, Concept, and Mistake nodes.
    2. Range indexes supporting unique constraints.
    3. HNSW Vector index on `Chunk` nodes for property `embedding`.

    Args:
        graph: FalkorDB Graph instance to configure.
        embedding_dim: Dimensionality of embeddings (default: 768).
        similarity_function: Vector distance metric ('cosine' or 'euclidean', default: 'cosine').

    Returns:
        A dictionary summarizing the status of constraints and indexes configured.
    """
    summary: Dict[str, List[str]] = {
        "indexes_created": [],
        "constraints_created": [],
        "vector_indexes_created": [],
        "errors": [],
    }

    # 1. Setup unique constraints for Student, Lesson, Concept, and Mistake nodes
    for label in UNIQUE_NODE_LABELS:
        # Step 1a: Create supporting range index on (label.id)
        if hasattr(graph, "create_node_range_index"):
            try:
                graph.create_node_range_index(label, "id")
                summary["indexes_created"].append(f"{label}.id")
            except Exception as e:
                if not _is_already_exists_error(e):
                    logger.debug("Range index note for %s(id): %s", label, e)
        else:
            index_query = f"CREATE INDEX FOR (n:{label}) ON (n.id)"
            try:
                graph.query(index_query)
                summary["indexes_created"].append(f"{label}.id")
            except Exception as e:
                if not _is_already_exists_error(e):
                    logger.debug("Index query note for %s(id): %s", label, e)

        # Step 1b: Create unique constraint
        if hasattr(graph, "create_node_unique_constraint"):
            try:
                graph.create_node_unique_constraint(label, "id")
                summary["constraints_created"].append(f"{label}.id")
                logger.info("Created unique constraint for %s(id)", label)
            except Exception as e:
                if not _is_already_exists_error(e):
                    logger.debug("Unique constraint note for %s(id): %s", label, e)
        else:
            try:
                constraint_query = f"CREATE CONSTRAINT FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                graph.query(constraint_query)
                summary["constraints_created"].append(f"{label}.id")
            except Exception as e:
                if not _is_already_exists_error(e):
                    logger.debug("Constraint note for %s(id): %s", label, e)

    # 2. Setup Vector index on Chunk nodes for the embedding property
    vector_query = (
        f"CREATE VECTOR INDEX FOR (c:{DEFAULT_VECTOR_LABEL}) "
        f"ON (c.{DEFAULT_VECTOR_PROPERTY}) "
        f"OPTIONS {{dimension: {embedding_dim}, similarityFunction: '{similarity_function}'}}"
    )
    try:
        graph.query(vector_query)
        summary["vector_indexes_created"].append(
            f"{DEFAULT_VECTOR_LABEL}.{DEFAULT_VECTOR_PROPERTY} (dim={embedding_dim}, metric={similarity_function})"
        )
        logger.info(
            "Created vector index on %s(%s) with dimension %d and similarity '%s'",
            DEFAULT_VECTOR_LABEL,
            DEFAULT_VECTOR_PROPERTY,
            embedding_dim,
            similarity_function,
        )
    except Exception as e:
        if _is_already_exists_error(e):
            logger.debug("Vector index on %s(%s) already exists", DEFAULT_VECTOR_LABEL, DEFAULT_VECTOR_PROPERTY)
        else:
            err_msg = f"Failed to create vector index on {DEFAULT_VECTOR_LABEL}.{DEFAULT_VECTOR_PROPERTY}: {e}"
            logger.error(err_msg)
            summary["errors"].append(err_msg)

    return summary


def list_constraints(graph: Graph) -> List[Any]:
    """
    List all active constraints in the graph.

    Args:
        graph: FalkorDB Graph instance.

    Returns:
        A list of constraint records returned by `CALL db.constraints()`.
    """
    try:
        res = graph.query("CALL db.constraints()")
        return getattr(res, "result_set", [])
    except Exception as e:
        logger.warning("Failed to retrieve constraints: %s", e)
        return []


def list_indexes(graph: Graph) -> List[Any]:
    """
    List all active indexes in the graph (range, full-text, vector).

    Args:
        graph: FalkorDB Graph instance.

    Returns:
        A list of index records returned by `CALL db.indexes()`.
    """
    try:
        res = graph.query("CALL db.indexes()")
        return getattr(res, "result_set", [])
    except Exception as e:
        logger.warning("Failed to retrieve indexes: %s", e)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"Connecting to FalkorDB on {DEFAULT_HOST}:{DEFAULT_PORT}...")
    try:
        falkor_client = get_client()
        target_graph = get_graph(falkor_client)
        print(f"Initializing schema for graph '{getattr(target_graph, 'name', DEFAULT_GRAPH_NAME)}'...")
        init_summary = init_schema(target_graph)
        print("Initialization Summary:")
        for key, items in init_summary.items():
            print(f"  {key}: {items}")
    except Exception as exc:
        print(f"Error during FalkorDB schema initialization: {exc}")
