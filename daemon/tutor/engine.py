"""
Socratic Tutor Engine for Team C.

Orchestrates pedagogical decision-making:
- Identifies root bottleneck concepts via backward dependency traversal.
- Queries student mistake memory to detect recurrence and guess-spamming.
- Computes progressive hint level (L0 to L4; L0 for guess-spamming).
- Retrieves relevant conceptual chunks via hybrid graph+vector search.
- Prompts local Qwen (qwen2.5-coder:7b via Ollama) with strict Socratic guidelines.
- Enforces output safety through guardrail retries and seed question fallback.
- Returns Contract 4 structured JSON payload with full graph telemetry.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from falkordb import Graph
except ImportError:
    Graph = Any  # type: ignore[misc,assignment]

try:
    from daemon.graph.retrieval import (
        find_bottleneck,
        hybrid_query,
        query_pattern_memory,
    )
    from daemon.graph.schema import get_client, get_graph
    from daemon.tutor.guard import guard_check
except ImportError:
    from graph.retrieval import (
        find_bottleneck,
        hybrid_query,
        query_pattern_memory,
    )
    from graph.schema import get_client, get_graph
    from tutor.guard import guard_check

logger = logging.getLogger(__name__)

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5-coder:7b"
MAX_GUARD_RETRIES = 2

# Seed questions repository for deterministic fallbacks and high-confidence scaffolding
SEED_QUESTIONS_POOL: Dict[str, Dict[int, str]] = {
    "boundary_off_by_one": {
        0: "Take a step back. What is the fundamental boundary condition you are trying to verify?",
        1: "What loop invariant guarantees that every valid index in the search space is inspected?",
        2: "Consider what happens when the search window reduces to left == right. Does your loop still execute?",
        3: "If the target element was at index 0 or the very last index, which branch in your condition would handle it?",
        4: "Trace a single-element list: what values do left, right, and mid take on the very first iteration?",
    },
    "infinite_loop": {
        0: "Take a pause. What condition in your logic guarantees that this repetition eventually stops?",
        1: "How does each iteration bring the pointers or counters strictly closer to the termination state?",
        2: "When the pointers become adjacent, does updating your boundary variables guarantee the window shrinks?",
        3: "What happens when left + 1 == right and the search condition is evaluated?",
        4: "Trace pointer movements: if neither boundary updates in a branch, why did the loop fail to progress?",
    },
    "array_indexing": {
        0: "Think about the coordinate system: what are the legal index boundaries for an array of size N?",
        1: "Which index represents the final valid element in a zero-indexed sequence?",
        2: "Could an offset or midpoint calculation ever compute an index outside the allocated length?",
        3: "How should your code behave if the input collection contains zero elements?",
        4: "What exact index is being calculated and accessed when the error occurs?",
    },
    "guess_spam": {
        0: "Take a deep breath and pause for a moment. Before modifying another line, what specific state is causing the current test to fail?",
        1: "What invariant are you trying to test with this change?",
        2: "What was the exact outcome of the previous attempt compared to this one?",
        3: "Can you write down the expected state after line 1 before running the test again?",
        4: "What specific hypothesis are you testing right now?",
    },
}

GENERIC_SEED_QUESTIONS: Dict[int, str] = {
    0: "Let's pause and take a step back. What is the overarching objective of this algorithm?",
    1: "What invariant or relationship must remain true before and after this block executes?",
    2: "Where does the actual execution trace begin to diverge from what you expected?",
    3: "How does your logic behave on the smallest valid edge case?",
    4: "Can you trace the variable values step-by-step for the failing input scenario?",
}


def _get_fallback_seed_question(
    mistake_id: str,
    concept_id: str,
    hint_level: int,
    line: Optional[int] = None,
) -> str:
    """Retrieve a curated, pre-validated Socratic seed question for fallback."""
    level = max(0, min(4, hint_level))

    # 1. Match specific mistake
    if mistake_id in SEED_QUESTIONS_POOL:
        q = SEED_QUESTIONS_POOL[mistake_id].get(level)
        if q:
            return q.format(line=line or "current")

    # 2. Match concept
    if concept_id in SEED_QUESTIONS_POOL:
        q = SEED_QUESTIONS_POOL[concept_id].get(level)
        if q:
            return q.format(line=line or "current")

    # 3. Generic fallback by hint level
    return GENERIC_SEED_QUESTIONS.get(level, GENERIC_SEED_QUESTIONS[0])


def _call_ollama_generate(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 25.0,
) -> Optional[Dict[str, Any]]:
    """Send structured JSON generation request to local Ollama instance."""
    payload = json.dumps({
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.4,
            "top_p": 0.9,
            "num_predict": 75,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_response = data.get("response", "")
            if not raw_response:
                return None
            return json.loads(raw_response)
    except Exception as exc:
        logger.warning("Ollama generation call failed or timed out: %s", exc)
        return None


def handle_tutor(
    graph: Graph,
    finding: Dict[str, Any],
    student_id: str = "local-user",
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """
    Handle a code analysis Finding and produce a Socratic TutorOutput (Contract 4).

    Steps:
    1. Query recurrence pattern memory and trace root prerequisite bottlenecks.
    2. Determine progressive hint level (0-4; guess_spam always receives L0).
    3. Retrieve relevant explanation chunks via hybrid vector+graph search.
    4. Call local Qwen model with JSON formatting and Socratic constraints.
    5. Pass output through guardrail check with retries, falling back to curated seed questions.
    6. Return Contract 4 JSON output containing graph telemetry.

    Args:
        graph: FalkorDB Graph instance.
        finding: Finding dictionary (Contract 3).
        student_id: Active student identifier (default: "local-user").
        model: Ollama model name (default: "qwen2.5-coder:7b").

    Returns:
        Contract 4 TutorOutput dictionary.
    """
    kind = finding.get("kind", "test_failure")
    mistake_id = finding.get("mistake_id") or "general_error"
    concept_ids = finding.get("concept_ids") or []
    primary_concept = concept_ids[0] if concept_ids else "logic"
    evidence = finding.get("evidence", "")
    line_number = finding.get("line")

    # Step 1: Check pattern frequency & bottleneck concept
    pattern_info = query_pattern_memory(graph, student_id, mistake_id)
    recurrence_count = pattern_info.get("recurrence_count", 0)

    bottleneck_info = find_bottleneck(graph, primary_concept)
    bottleneck_concept = bottleneck_info.get("bottleneck_concept_id", primary_concept)
    bottleneck_depth = bottleneck_info.get("depth", 0)

    # Focus on the root weakness bottleneck if identified along the dependency path
    targets_concept = bottleneck_concept if bottleneck_depth > 0 else primary_concept

    # Step 2: Determine progressive hint level (L0 to L4)
    # Rule: Guess spamming always receives Level 0 (metacognitive reflection pause)
    if kind == "guess_spam":
        hint_level = 0
        pet_state = "detective"
    else:
        # Scale hint level with mistake recurrence (L1 on first attempt, up to L4)
        hint_level = min(4, max(1, recurrence_count + 1))
        pet_state = "asking"

    # Step 3: Retrieve context chunks via hybrid search
    query_text = f"{evidence} {mistake_id} {targets_concept}".strip()
    hybrid_result = hybrid_query(graph, student_id, targets_concept, query_text, top_k=3)
    chunks_list = hybrid_result.get("chunks", [])

    context_snippets = []
    for c in chunks_list[:2]:
        content = c.get("content", "").strip()
        if content:
            context_snippets.append(f"- {content[:200]}")
    chunks_context = "\n".join(context_snippets) if context_snippets else "None available."

    # Step 4: Prepare Socratic Prompts
    hint_level_descriptions = {
        0: "Level 0: Metacognitive pause asking the student to reflect on their hypothesis or precondition without code.",
        1: "Level 1: Guiding question on invariants or core concept principles.",
        2: "Level 2: Attention-directing question towards the specific condition, pointer, or boundary.",
        3: "Level 3: Inquiry testing behavior under a specific edge-case input scenario.",
        4: "Level 4: Step-by-step walkthrough trace inquiry of the failing execution case.",
    }

    system_prompt = (
        "You are a friendly Socratic companion pet tutor. "
        "Your task is to guide a student to discover their coding error by asking ONE guiding question.\n"
        "STRICT PEDAGOGICAL RULES:\n"
        "1. NEVER give the solution, code fix, syntax replacement, variable assignment, or return statement.\n"
        "2. NEVER use direct directives such as 'change X to Y', 'replace X with Y', 'use X instead of Y', or 'set X to Y'.\n"
        "3. Focus on guiding the student's thinking at the requested hint level.\n"
        "4. Respond ONLY with a valid JSON object matching: {\"question\": \"...\"}."
    )

    code_snippet = finding.get("content") or finding.get("code")
    code_block = f"\nCurrent Code ({finding.get('file', 'file')}):\n```\n{code_snippet[:600]}\n```\n" if code_snippet else ""

    user_prompt = f"""
Student Failure Context:
- Mistake ID: {mistake_id}
- Target Concept: {targets_concept}
- Hint Level: L{hint_level} ({hint_level_descriptions.get(hint_level)})
- Evidence / Error: {evidence}
- File/Line: {finding.get('file', 'solution')}:{line_number or 'N/A'}
{code_block}- Conceptual Context:
{chunks_context}

Formulate ONE thoughtful Socratic guiding question at Hint Level {hint_level}.
Do NOT output code or instructions on what to write.
Respond strictly in JSON format: {{"question": "..."}}
""".strip()

    # Step 5: Query Ollama with Guardrail Retries
    question: Optional[str] = None

    for attempt in range(1, MAX_GUARD_RETRIES + 1):
        parsed = _call_ollama_generate(system_prompt, user_prompt, model=model)
        if parsed and isinstance(parsed, dict):
            candidate_q = parsed.get("question")

            if candidate_q and isinstance(candidate_q, str):
                is_safe, reason = guard_check(candidate_q)
                if is_safe:
                    question = candidate_q.strip()
                    logger.debug("Ollama question passed guard check on attempt %d", attempt)
                    break
                else:
                    logger.warning("Ollama output rejected by guardrail on attempt %d: %s", attempt, reason)
                    # Append guard feedback to user prompt for next retry
                    user_prompt += f"\nPrevious attempt was rejected because it {reason}. Please rephrase as a purely conceptual question."

    # Fallback to pre-validated Socratic seed question if LLM unavailable or rejected
    if not question:
        logger.info("Using curated Socratic seed question fallback for %s at level %d", mistake_id, hint_level)
        question = _get_fallback_seed_question(mistake_id, targets_concept, hint_level, line_number)

    # Step 6: Return Contract 4 JSON output with graph trace
    return {
        "question": question,
        "hint_level": hint_level,
        "targets_concept": targets_concept,
        "mistake_id": mistake_id,
        "pet_state": pet_state,
        "graph_trace": {
            "cypher": hybrid_result.get("cypher", ""),
            "query_ms": hybrid_result.get("query_ms"),
            "nodes": hybrid_result.get("nodes", []),
            "edges": hybrid_result.get("edges", []),
        },
    }


def handle(
    finding: Dict[str, Any],
    student_id: str = "local-user",
    graph: Optional[Graph] = None,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """
    Standard daemon entry point matching `tutor.handle(finding, student_id) -> TutorOutput`.

    Args:
        finding: Finding dictionary (Contract 3).
        student_id: Active student identifier.
        graph: Optional active FalkorDB Graph instance. If None, connects to default graph.
        model: Local LLM model tag.

    Returns:
        Contract 4 TutorOutput dictionary.
    """
    if graph is None:
        client = get_client()
        graph = get_graph(client)

    return handle_tutor(graph=graph, finding=finding, student_id=student_id, model=model)


def get_smart_code_hint(active_code: str, lesson_id: str = "") -> str:
    """Smart static heuristic analyzer for immediate, accurate guidance."""
    clean = active_code.strip()
    if not clean:
        return "Start by initializing your search pointer boundaries or base cases."

    import re
    # 1. Check for common syntax errors / incomplete lines
    try:
        compile(active_code, "<string>", "exec")
    except SyntaxError as e:
        line_num = e.lineno
        bad_line = ""
        lines = active_code.splitlines()
        if line_num and 1 <= line_num <= len(lines):
            bad_line = lines[line_num - 1].strip()
        if bad_line and ("==" in bad_line or "=" in bad_line) and not bad_line.endswith(":"):
            return f"Line {line_num} looks incomplete ('{bad_line}'). Complete the statement or condition."
        return f"Check line {line_num or ''}: there is an incomplete expression or syntax error."

    # 2. Check for integer division / precedence bugs in mid calculation
    if re.search(r"mid\s*=\s*left\s*\+\s*right\s*/\s*2", active_code):
        return "Watch out for operator precedence in 'left + right / 2': division (/) runs before addition. Use '(left + right) // 2' for integer division."
    if re.search(r"mid\s*=\s*\(?\s*left\s*\+\s*right\s*\)?\s*/\s*2", active_code) and "//" not in active_code:
        return "In Python, '/' produces a float. List indices must be integers—use '// 2' instead."

    # 3. Check for binary search pointer comparisons inside loop
    if "nums[left] ==" in active_code or "nums[right] ==" in active_code:
        if "nums[mid]" not in active_code:
            return "Binary search checks the middle element ('nums[mid]'), not 'nums[left]' or 'nums[right]'."

    # 4. Check for loop condition
    if re.search(r"while\s+left\s*<\s*right\s*:", active_code):
        return "Think about the condition 'while left < right': what happens when left == right on a 1-element subarray? Consider 'while left <= right'."

    # 5. Check missing loop when bounds are set
    if "left" in active_code and "right" in active_code and "while" not in active_code:
        return "Your boundary pointers are set up. Next, create a while loop to continue searching while left <= right."

    # 6. Check mid calculation missing
    if "while" in active_code and "mid" not in active_code:
        return "Inside your loop, compute the midpoint index using 'mid = (left + right) // 2'."

    # 7. Check return statement
    if "def " in active_code and "return " not in active_code:
        return "Remember to return the index when nums[mid] == target, and return -1 if the loop finishes without finding it."

    if "NotImplementedError" in active_code and "left" not in active_code:
        return "To get started with binary search, initialize your pointer boundaries: left at 0 and right at len(nums) - 1."

    return "Compare nums[mid] with target to decide whether to search the left or right half."


def generate_idle_hint(
    active_code: str,
    active_file: str,
    lesson: Optional[Dict[str, Any]] = None,
    student_id: str = "local-user",
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """
    Generate a concise, non-question guiding hint based on the student's current code.
    Designed for the desktop pet bubble when the student is paused for 10 seconds.
    """
    import time
    t0 = time.perf_counter()

    lesson_id = (lesson or {}).get("id", "code")
    concepts = (lesson or {}).get("concepts", ["logic"])
    primary_concept = concepts[0] if concepts else "logic"
    fname = os.path.basename(active_file) if active_file else "solution"

    system_prompt = (
        "You are a friendly Socratic companion desktop pet.\n"
        "The student has paused writing code. Analyze their code and provide ONE concise, helpful hint statement.\n\n"
        "STRICT PEDAGOGICAL RULES:\n"
        "1. This hint must be a HELPFUL GUIDING STATEMENT, NOT A QUESTION. Do not use question marks.\n"
        "2. Do NOT give away full syntax or write their code for them.\n"
        "3. Point their attention to the next logical concept.\n"
        "4. Respond strictly in JSON: {\"hint\": \"...\"}."
    )

    code_lines = (active_code or "").strip().splitlines()[:50]
    code_block = "\n".join(code_lines)

    user_prompt = f"""
Student's Current Code ({fname}):
```
{code_block}
```
Lesson: {lesson_id} (Target concept: {primary_concept})

Provide ONE concise guiding hint statement (NOT a question) about what to consider or write next in this algorithm.
Respond strictly in JSON: {{"hint": "..."}}
""".strip()

    hint_text: Optional[str] = None
    # Use shorter timeout for idle hint so UI never hangs
    for attempt in range(1, MAX_GUARD_RETRIES + 1):
        parsed = _call_ollama_generate(system_prompt, user_prompt, model=model, timeout=6.0)
        if parsed and isinstance(parsed, dict):
            cand = parsed.get("hint") or parsed.get("text")
            if cand and isinstance(cand, str):
                cand = cand.strip().rstrip("?")
                is_safe, reason = guard_check(cand, require_question=False)
                if is_safe:
                    hint_text = cand
                    break

    if not hint_text:
        hint_text = get_smart_code_hint(active_code, lesson_id)

    query_ms = round((time.perf_counter() - t0) * 1000, 2)
    nodes = [
        {"id": f"student:{student_id}", "label": "Student", "props": {"id": student_id}},
        {"id": f"concept:{primary_concept}", "label": "Concept", "props": {"id": primary_concept}},
    ]
    edges = []
    cypher = "MATCH (s:Student {id:$sid})-[:MASTERED]->(c:Concept {id:$cid}) RETURN s, c"

    return {
        "question": hint_text,
        "text": hint_text,
        "hint_level": 1,
        "targets_concept": primary_concept,
        "mistake_id": "idle_guidance",
        "pet_state": "curious",
        "graph_trace": {
            "cypher": cypher,
            "query_ms": query_ms,
            "nodes": nodes,
            "edges": edges,
        },
    }


_CONVERSATION_HISTORY: Dict[str, List[Dict[str, str]]] = {}



def handle_reply(
    text: str,
    student_id: str = "local-user",
    last_finding: Optional[Dict[str, Any]] = None,
    graph: Optional[Graph] = None,
    model: str = DEFAULT_MODEL,
    active_code: Optional[str] = None,
    active_file: Optional[str] = None,
    active_language: Optional[str] = None,
    cursor: Optional[Dict[str, Any]] = None,
    lesson: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Process a student's reply or question Socratically with contextual LLM guidance,
    tracking conversation history, code buffer, and celebrating mastery when appropriate.
    """
    import time
    t0 = time.perf_counter()
    finding = last_finding or {}
    mid = finding.get("mistake_id", "general")
    concept_ids = finding.get("concept_ids", ["logic"])
    if lesson and lesson.get("concepts"):
        targets_concept = lesson["concepts"][0]
    else:
        targets_concept = concept_ids[0] if concept_ids else "logic"
    evidence = finding.get("evidence", "Interactive discussion")
    line = (cursor or {}).get("line") or finding.get("line")

    cleaned_text = text.strip()
    lower_text = cleaned_text.lower()
    greetings = {"hi", "hello", "hey", "sup", "yo", "good morning", "good afternoon", "howdy"}

    # Track conversation history per student
    history = _CONVERSATION_HISTORY.setdefault(student_id, [])
    history.append({"role": "student", "text": cleaned_text})

    question: Optional[str] = None
    pet_state: str = "asking"

    # Handle greetings specifically
    if lower_text in greetings or any(lower_text.startswith(g + " ") for g in greetings):
        if mid and mid != "general":
            question = f"Hello! Are you ready to explore what might be happening with {mid.replace('_', ' ')} in your code?"
        elif active_file:
            fname = os.path.basename(active_file)
            question = f"Hello! I see you are working on {fname}. What part of the logic would you like to explore together?"
        else:
            question = "Hello! What algorithmic problem or concept would you like to explore together?"
    else:
        code_section = ""
        if active_code and active_code.strip():
            code_lines = active_code.strip().splitlines()[:60]
            truncated_code = "\n".join(code_lines)
            code_section = f"\nStudent's Current Code in Editor ({active_language or 'code'}):\n```{active_language or ''}\n{truncated_code}\n```"

        file_info = os.path.basename(active_file) if active_file else finding.get("file", "solution")

        # Include recent conversation turns so the model never asks in an isolated loop
        recent_history = history[-6:-1]
        history_str = ""
        if recent_history:
            lines = [f"{'Student' if h['role'] == 'student' else 'Pet'}: {h['text']}" for h in recent_history]
            history_str = "\nRecent Conversation:\n" + "\n".join(lines) + "\n"

        is_unwritten = False
        if not active_code or not active_code.strip():
            is_unwritten = True
        elif "NotImplementedError" in active_code or "pass" in active_code or "TODO" in active_code:
            lines_stripped = [l.strip() for l in active_code.strip().splitlines() if l.strip() and not l.strip().startswith("#") and not l.strip().startswith('"""') and not l.strip().startswith("'''")]
            # Only def and raise NotImplementedError / pass
            if len(lines_stripped) <= 2:
                is_unwritten = True

        system_prompt = (
            "You are a friendly Socratic companion desktop pet tutor for coding students.\n"
            "The student has replied or asked a question. Look at their code and the conversation history.\n\n"
            "PEDAGOGICAL RULES:\n"
            "1. When the student has answered your previous question correctly or grasped the concept, CONFIRM THEIR ANSWER ENTHUSIASTICALLY! Tell them they are correct (e.g., 'Spot on! You got it right! You can now write that in your code.'). Set celebrate: true and question: false. Do NOT interrogate them with another question if they already got it right!\n"
            "2. When the student has not written the implementation yet (the file is empty, has TODOs, or raises NotImplementedError), guide them on what initial variables or bounds they need.\n"
            "3. If the student asks for clarification (e.g. 'which case?'), clarify the concrete case.\n"
            "4. NEVER give away the complete code fix or syntax to write.\n"
            "5. If they still need guidance, ask ONE thoughtful Socratic question.\n"
            "6. Respond strictly in JSON: {\"text\": \"...\", \"celebrate\": true/false, \"is_question\": true/false}."
        )

        starter_note = "\nNote: The student has NOT written their solution yet (starter code with TODO / NotImplementedError). Guide their initial approach from scratch!" if is_unwritten else ""

        user_prompt = f"""
Student message: "{cleaned_text}"
Active File: {file_info} (line {line or 'current'})
{code_section}
{history_str}
Active Context:
- Target Concept: {targets_concept}
- Evidence: {evidence}{starter_note}

Review the student's reply against the previous question and code:
- If the student answered correctly or demonstrated understanding, confirm they are correct and praise them (e.g. "Spot on! That is exactly right!"). Set "celebrate": true, "is_question": false.
- Otherwise, guide them forward with ONE Socratic check.
Respond strictly in JSON: {{"text": "...", "celebrate": true/false, "is_question": true/false}}
""".strip()

        for attempt in range(1, MAX_GUARD_RETRIES + 1):
            parsed = _call_ollama_generate(system_prompt, user_prompt, model=model)
            if parsed and isinstance(parsed, dict):
                cand = parsed.get("text") or parsed.get("question")
                is_q = parsed.get("is_question", True) if parsed.get("is_question") is not None else ("?" in str(cand))
                if parsed.get("celebrate") is True:
                    pet_state = "celebrating"
                if cand and isinstance(cand, str):
                    cand = cand.strip()
                    is_safe, reason = guard_check(cand, require_question=is_q)
                    if is_safe:
                        question = cand.strip()
                        break
                    else:
                        logger.warning("Reply question rejected by guardrail: %s", reason)
                        user_prompt += f"\nPrevious attempt was rejected because it {reason}. Please rephrase conceptually."

    # High-confidence contextual fallback if LLM is offline or output was unsafe
    if not question:
        if "which case" in lower_text or "what case" in lower_text:
            question = "For example, consider an empty list nums = [] or a single element [x] where lo == hi: what does your loop condition evaluate to?"
        elif any(w in lower_text for w in ["stops", "exits", "returns -1", "false", "terminates"]):
            question = "Spot on! The loop condition evaluates to false, so it skips the loop and returns -1 cleanly. Ready to run tests to verify?"
            pet_state = "celebrating"
        elif any(w in lower_text for w in ["log n", "o(log n)", "logarithmic", "half", "divides by 2", "divide"]):
            question = "Exactly right! Halving the search space on every iteration gives O(log N) time complexity. Notice how efficient that is compared to linear search?"
            pet_state = "celebrating"
        elif "bubble" in lower_text or "bubble sort" in lower_text:
            question = "In bubble sort, how do adjacent elements decide whether they need to swap, and where does the largest element end up after each pass?"
        elif "binary search" in lower_text:
            question = "Binary search divides the search space in half each iteration—what invariant must hold true about the array order for this to work?"
        elif "quick" in lower_text or "quicksort" in lower_text:
            question = "How does choosing a pivot element partition the array into smaller sub-arrays in quicksort?"
        elif "merge" in lower_text or "mergesort" in lower_text:
            question = "In merge sort, how does the combine step guarantee two already sorted halves are merged in linear time?"
        elif "time complexity" in lower_text or "big o" in lower_text or "space complexity" in lower_text:
            question = "As the input size N scales to a million elements, what operation dominates your execution time?"
        elif "recursion" in lower_text or "recursive" in lower_text:
            question = "What base case condition ensures that your recursive function calls will eventually terminate?"
        elif "single" in lower_text or "one" in lower_text:
            question = "If the array has only one element where left == right, what does your loop condition evaluate to?"
        elif "loop" in lower_text or "while" in lower_text or "infinite" in lower_text:
            question = "What condition in your loop guarantees that the search window strictly shrinks on each step?"
        elif "mid" in lower_text or "middle" in lower_text:
            question = "How is your midpoint calculated, and could it ever evaluate outside the current boundary?"
        elif "index" in lower_text or "bound" in lower_text or "error" in lower_text:
            question = "What are the valid index boundaries for a sequence of length N?"
        elif "two pointer" in lower_text or "pointer" in lower_text:
            question = "How do you determine when to advance the left pointer versus decrementing the right pointer?"
        elif "tree" in lower_text or "bst" in lower_text:
            question = "In a Binary Search Tree, what invariant holds between a node, its left subtree, and its right subtree?"
        else:
            question = "If you trace through your logic on the smallest failing case, what value does each variable hold?"

    # Record tutor's question in conversation history
    history.append({"role": "tutor", "text": question})

    query_ms = round((time.perf_counter() - t0) * 1000, 2)
    nodes = [
        {"id": f"student:{student_id}", "label": "Student", "props": {"id": student_id}},
        {"id": f"concept:{targets_concept}", "label": "Concept", "props": {"id": targets_concept}},
    ]
    edges = []
    cypher = "MATCH (s:Student {id:$sid})-[:MASTERED]->(c:Concept {id:$cid}) RETURN s, c"

    if graph is not None:
        try:
            graph.query(cypher, {"sid": student_id, "cid": targets_concept})
        except Exception:
            pass

    return {
        "question": question,
        "hint_level": 1,
        "targets_concept": targets_concept,
        "mistake_id": mid,
        "pet_state": pet_state,
        "graph_trace": {
            "cypher": cypher,
            "query_ms": query_ms,
            "nodes": nodes,
            "edges": edges,
        },
    }

