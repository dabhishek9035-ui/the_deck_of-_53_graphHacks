"""
Unit tests for tutor output guardrails (daemon/tutor/guard.py).

Verifies that guard_check:
1. Rejects outputs containing code blocks (```).
2. Rejects outputs containing direct fix directives ('change X to Y', 'replace X with Y').
3. Rejects outputs containing code variable assignments ('x = ...', 'let y = ...').
4. Rejects outputs containing code return statements ('return -1', 'return true').
5. Approves clean, natural-language Socratic guiding questions.
"""

from __future__ import annotations

import pytest
from daemon.tutor.guard import guard_check


class TestGuardCheckBlocksLeakyCode:
    """Test suite ensuring code leaks and direct fixes are strictly blocked."""

    @pytest.mark.parametrize(
        "leaky_text, expected_keyword",
        [
            ("```python\ndef binary_search(arr, target):\n    pass\n```", "code block"),
            ("Here is the fix: ``` left = mid + 1 ```", "code block"),
            ("```\nwhile left <= right:\n```", "code block"),
        ],
    )
    def test_blocks_code_fences(self, leaky_text: str, expected_keyword: str):
        is_safe, reason = guard_check(leaky_text)
        assert not is_safe
        assert reason is not None
        assert expected_keyword in reason.lower()

    @pytest.mark.parametrize(
        "directive_text",
        [
            "You should change line 14 to left = mid + 1.",
            "Please replace left <= right with left < right in your loop.",
            "Try using right = mid - 1 instead of right = mid.",
            "Consider using left + 1 instead of left.",
            "Set left to mid + 1 on line 12.",
            "Update right to mid - 1.",
            "Just write return -1 at the bottom.",
            "Simply add left += 1 inside the loop.",
            "The fix is to subtract 1 from high.",
            "The solution is to use integer division.",
            "You need to change your while loop condition.",
            "You must write mid = (left + right) // 2.",
        ],
    )
    def test_blocks_direct_fix_instructions(self, directive_text: str):
        is_safe, reason = guard_check(directive_text)
        assert not is_safe, f"Expected '{directive_text}' to be blocked"
        assert reason is not None
        assert "direct fix" in reason.lower() or "directive" in reason.lower()

    @pytest.mark.parametrize(
        "assignment_text",
        [
            "mid = (left + right) // 2",
            "let found = false;",
            "const targetIndex = 0;",
            "int high = arr.length - 1;",
            "i += 1",
            "left = mid + 1",
            "def binary_search(nums):",
            "function search(target) {",
        ],
    )
    def test_blocks_code_assignments(self, assignment_text: str):
        is_safe, reason = guard_check(assignment_text)
        assert not is_safe, f"Expected '{assignment_text}' to be blocked"
        assert reason is not None
        assert "assignment" in reason.lower()

    @pytest.mark.parametrize(
        "return_text",
        [
            "return -1",
            "return true;",
            "return False",
            "return None",
            "return mid;",
            "return 0;",
            "return 'not found'",
        ],
    )
    def test_blocks_code_return_statements(self, return_text: str):
        is_safe, reason = guard_check(return_text)
        assert not is_safe, f"Expected '{return_text}' to be blocked"
        assert reason is not None
        assert "return" in reason.lower()

    @pytest.mark.parametrize("empty_input", ["", "   ", "\n\t\n"])
    def test_blocks_empty_input(self, empty_input: str):
        is_safe, reason = guard_check(empty_input)
        assert not is_safe
        assert reason is not None

    @pytest.mark.parametrize(
        "non_question",
        [
            "The condition needs to include equality so the last element gets checked",
            "Consider adding one to mid when the target is larger",
            "You might want to check the array boundaries carefully.",
            "This invariant fails on single element arrays.",
        ],
    )
    def test_blocks_plain_english_non_questions(self, non_question: str):
        is_safe, reason = guard_check(non_question)
        assert not is_safe, f"Expected '{non_question}' to be blocked because it's not a question"
        assert reason is not None
        assert "not a question" in reason.lower() or "missing '?'" in reason.lower()

    @pytest.mark.parametrize(
        "multi_question",
        [
            "What happens when left == right? Does the loop continue?",
            "What is mid? Why is it offset?",
        ],
    )
    def test_blocks_multiple_questions(self, multi_question: str):
        is_safe, reason = guard_check(multi_question)
        assert not is_safe, f"Expected '{multi_question}' to be blocked because it contains multiple questions"
        assert reason is not None
        assert "multiple questions" in reason.lower()


class TestGuardCheckAllowsSocraticQuestions:
    """Test suite ensuring legitimate Socratic guiding questions pass verification."""

    @pytest.mark.parametrize(
        "clean_question",
        [
            "What happens when left == right?",
            "What loop invariant guarantees that all elements in the search space are inspected?",
            "Consider what happens when the search window reduces to a single element. Does the loop still execute?",
            "If the target element is located at index 0, which branch in your condition handles it?",
            "Can you trace a single-element list: what values do left, right, and mid take on the first iteration?",
            "How does each iteration bring your pointers closer to the termination state?",
            "Think about zero-indexing: what are the valid index boundaries for an array of size N?",
            "What value does the function return when the target value is absent from the array?",
            "Where does the actual execution trace begin to diverge from what you expected?",
            "Take a deep breath and pause for a moment. What specific state is causing the current test to fail?",
        ],
    )
    def test_allows_valid_socratic_questions(self, clean_question: str):
        is_safe, reason = guard_check(clean_question)
        assert is_safe, f"Expected clean question to pass, but got rejection: '{reason}'"
        assert reason is None
