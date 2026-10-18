"""
Output guardrails for Socratic Code-Tutor Pet (Team C).

Enforces strict pedagogical rules:
- The pet NEVER outputs code blocks (` ``` `).
- The pet NEVER outputs direct fix instructions ("change X to Y", "replace X with Y").
- The pet NEVER outputs variable assignments, return statements, or implementation code.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# 1. Code blocks / Markdown code fences
CODE_BLOCK_PATTERN = re.compile(r"```", re.IGNORECASE)

# 2. Direct fix directives and imperatives
DIRECT_FIX_PATTERNS = [
    # "change X to Y", "replace X with Y", "substitute X for Y", "switch X to Y"
    re.compile(r"\b(?:change|replace|substitute|switch)\s+.+?\s+(?:to|with|for|by)\b", re.IGNORECASE),
    # "use X instead of Y", "try using X instead of Y"
    re.compile(r"\b(?:use|try using|consider using)\s+.+?\s+instead of\b", re.IGNORECASE),
    # "set X to Y", "update X to Y"
    re.compile(r"\b(?:set|update)\s+[\w\.\(\)\[\]]+\s+to\s+[^.!?\n]+", re.IGNORECASE),
    # "just write X", "simply add X", "you need to change X"
    re.compile(r"\b(?:just|simply)\s+(?:write|put|add|insert|delete|remove|change|replace)\b", re.IGNORECASE),
    re.compile(r"\byou\s+(?:should|need to|must|have to)\s+(?:change|replace|write|put|use|set|return)\b", re.IGNORECASE),
    # "the fix is to ...", "the solution is ..."
    re.compile(r"\b(?:the fix|the solution|the correct code)\s+(?:is|would be)\b", re.IGNORECASE),
]

# 3. Code-level assignment statements
ASSIGNMENT_PATTERNS = [
    # Explicit variable declaration assignments (let x = ..., int x = ..., etc.)
    re.compile(r"\b(?:let|var|const|val|int|float|double|bool|auto|str|String)\s+[a-zA-Z_]\w*\s*=", re.IGNORECASE),
    # Programming assignments: e.g. x = y + 1, mid = (l + r) // 2, i += 1
    re.compile(r"\b[a-zA-Z_]\w*\s*(?:=|\+=|-=|\*=|/=|:=)\s*(?:\(|\[|\{|\"|\'|-?\d+|[a-zA-Z_]\w*\(|[a-zA-Z_]\w*\s*[\+\-\*/%])"),
    # Function definitions
    re.compile(r"\b(?:def|function)\s+[a-zA-Z_]\w*\s*\(", re.IGNORECASE),
]

# 4. Code-level return statements
RETURN_STATEMENT_PATTERNS = [
    # Code return: return -1, return true, return None, return x;
    re.compile(r"\breturn\s+(?:-?\d+|true|false|null|none|nil|[-+*/\w]+\s*;|\w+\s*\([^)]*\)|\w+;|\"[^\"]*\"|'[^']*')", re.IGNORECASE),
    # return statement at the start of a line
    re.compile(r"^\s*return\s+[^.!?\n]+", re.MULTILINE | re.IGNORECASE),
]


def guard_check(output_text: str, require_question: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validate that tutor output strictly obeys Socratic guidelines.

    Blocks:
    1. Code fences (```).
    2. Direct fix directives ("change X to Y", "replace X with Y").
    3. Code variable assignments (x = ..., int y = ...).
    4. Code return statements (return true, return -1).

    Args:
        output_text: Natural language string proposed by the LLM.
        require_question: If True, requires output to contain exactly one question mark.

    Returns:
        (is_safe, failure_reason)
        - If safe: (True, None)
        - If unsafe: (False, "Reason for rejection")
    """
    if not output_text or not output_text.strip():
        return False, "Output is empty"

    text = output_text.strip()

    # Rule 1: Check for markdown code fences
    if CODE_BLOCK_PATTERN.search(text):
        return False, "Contains forbidden code block (```)"

    # Rule 2: Check for direct fix instructions
    for pattern in DIRECT_FIX_PATTERNS:
        match = pattern.search(text)
        if match:
            return False, f"Contains direct fix directive: '{match.group(0)}'"

    # Rule 3: Check for code assignment statements
    for pattern in ASSIGNMENT_PATTERNS:
        match = pattern.search(text)
        if match:
            return False, f"Contains code assignment statement: '{match.group(0)}'"

    # Rule 4: Check for code return statements
    for pattern in RETURN_STATEMENT_PATTERNS:
        match = pattern.search(text)
        if match:
            return False, f"Contains code return statement: '{match.group(0)}'"

    # Rule 5: Question check if required
    q_count = text.count("?")
    if require_question:
        if q_count == 0:
            return False, "Output is not a question (missing '?')"
        if q_count > 1:
            return False, f"Output contains multiple questions ({q_count} '?'), expected exactly one"

    return True, None
