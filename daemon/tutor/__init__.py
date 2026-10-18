"""
Socratic Tutor Engine package for Team C.
"""

try:
    from daemon.tutor.engine import handle, handle_tutor, handle_reply, generate_idle_hint
    from daemon.tutor.guard import guard_check
except ImportError:
    from tutor.engine import handle, handle_tutor, handle_reply, generate_idle_hint
    from tutor.guard import guard_check

__all__ = ["guard_check", "handle_tutor", "handle", "handle_reply", "generate_idle_hint"]
