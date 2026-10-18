"""Unit tests for GraphAdapter, get_progress, streak calculation, and spaced repetition."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from daemon.adapters.graph_adapter import GraphAdapter


class TestGraphAdapterProgress:
    def test_concepts_count_matches_concepts_yaml(self):
        """Verify all 44 concepts from concepts.yaml are loaded with matching ids."""
        adapter = GraphAdapter()
        # Disconnect FalkorDB to test base catalog
        adapter._get_graph = lambda: None
        progress = adapter.get_progress("test-student")
        concepts = progress["concepts"]
        assert len(concepts) == 44
        concept_ids = {c["id"] for c in concepts}
        assert "variables" in concept_ids
        assert "array_indexing" in concept_ids
        assert "data_modeling" in concept_ids
        assert "rate_limiting" in concept_ids

    def test_no_history_does_not_invent_fake_mistakes(self):
        """Verify when no history exists, mistakes and patterns are empty lists."""
        adapter = GraphAdapter()
        adapter._get_graph = lambda: None
        adapter._memory_attempts = []
        progress = adapter.get_progress("empty-student")
        assert progress["recent_mistakes"] == []
        assert progress["patterns"] == []
        assert progress["daily_activity"] == []
        assert progress["hint_dependency"] == []
        assert progress["streak"]["current"] == 0
        assert progress["streak"]["best"] == 0

    def test_dynamic_skill_radar_reflects_mastery(self):
        """Verify skill radar tracks are computed dynamically based on actual concept mastery."""
        adapter = GraphAdapter()
        adapter._get_graph = lambda: None
        adapter._memory_mastery = {
            "arrays": 0.8,
            "array_indexing": 0.9,
            "strings": 0.7,
            "two_pointers": 0.8,
            "sliding_window": 0.8,
        }
        progress = adapter.get_progress("radar-student")
        radar = progress["skill_radar"]
        assert "dsa" in radar
        assert "system_design" in radar

        # Arrays & Strings axis should reflect the 0.8 average
        dsa_axes = {a["axis"]: a["value"] for a in radar["dsa"]}
        assert dsa_axes["Arrays & Strings"] == 0.8

    def test_streak_calculation_consecutive_days(self):
        """Verify streak counts consecutive days and tracks best separately."""
        adapter = GraphAdapter()
        today = datetime.now(timezone.utc).date()
        day1 = (today - timedelta(days=2)).strftime("%Y-%m-%d")
        day2 = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        day3 = today.strftime("%Y-%m-%d")

        # 3 consecutive days ending today
        attempts = [
            {"date": day1, "ts": 100},
            {"date": day2, "ts": 200},
            {"date": day3, "ts": 300},
        ]
        streak = adapter._compute_streak(attempts)
        assert streak["current"] == 3
        assert streak["best"] == 3

        # Broken streak: 5 days last month, 1 day today
        old_days = [(today - timedelta(days=30 - i)).strftime("%Y-%m-%d") for i in range(5)]
        attempts_broken = [{"date": d, "ts": 100 + i} for i, d in enumerate(old_days)]
        attempts_broken.append({"date": day3, "ts": 500})
        streak_broken = adapter._compute_streak(attempts_broken)
        assert streak_broken["current"] == 1
        assert streak_broken["best"] == 5

    def test_spaced_repetition_dates_reach_ui(self):
        """Verify real spaced repetition dates computed by forgetting curve reach the UI payload."""
        adapter = GraphAdapter()
        adapter._get_graph = lambda: None
        adapter._memory_spaced_repetition = {
            "binary_search": {
                "due_date": int(datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc).timestamp()),
                "interval_hours": 48.0,
                "score": 0.7,
            }
        }
        progress = adapter.get_progress("sr-student")
        sr_items = progress["spaced_repetition_due"]
        assert len(sr_items) >= 1
        item = next(x for x in sr_items if x["concept_id"] == "binary_search")
        assert item["interval_days"] == 2.0
        assert "2026-10-20" in item["due_at"]

    def test_lesson_progress_is_not_flat_point_five(self):
        """Verify in-progress lessons calculate dynamic continuous progress from concept mastery."""
        adapter = GraphAdapter()
        adapter._get_graph = lambda: None
        adapter._memory_mastery = {
            "binary_search": 0.7,
            "loop_invariants": 0.6,
            "array_indexing": 0.8,
            "edge_cases": 0.7,
        }
        adapter._memory_lessons = {"binary-search": "in_progress"}
        adapter._memory_attempts = [{"lesson_id": "binary-search", "passed": False, "ts": 100}]

        progress = adapter.get_progress("lesson-student")
        lessons = progress["lessons"]
        bs = next(l for l in lessons if l["id"] == "binary-search")
        assert bs["status"] == "in_progress"
        # Average of 0.7, 0.6, 0.8, 0.7 is 0.7 (not 0.5!)
        assert bs["progress"] != 0.5
        assert 0.65 <= bs["progress"] <= 0.75
