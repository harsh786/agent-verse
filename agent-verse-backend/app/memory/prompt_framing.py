"""Recalled memory enters a planner prompt as DATA, never as instructions.

The write gate (:mod:`app.memory.screening`) keeps new poisoned memories out of
storage; this is the read-side half (MEM-68):

* :func:`memory_text_is_safe` drops a recalled memory that carries a
  prompt-injection payload — rows written before the gate existed, or by a path
  that bypassed it, never reach a prompt;
* :func:`frame_memory_block` wraps what survives in the same untrusted-data
  delimiters the context pipeline uses (``[UNTRUSTED REFERENCE DATA]`` /
  ``<<<BEGIN ...>>>`` ... ``<<<END ...>>>``), with any delimiter look-alikes
  inside the memory text defanged so it cannot close its own block.

The planner adds :data:`UNTRUSTED_NOTE` once, telling the model that framed
content is information to reason about, never instructions.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.context.prompt_builder import UNTRUSTED_NOTE, frame_untrusted


def memory_text_is_safe(text: str) -> bool:
    """False when *text* carries a prompt-injection payload (or cannot be checked)."""
    from app.memory.screening import contains_prompt_injection

    try:
        return not contains_prompt_injection(text)
    except Exception:
        return False  # fail closed: an unscreenable memory is not shown


def frame_memory_block(label: str, lines: Iterable[str]) -> str:
    """Frame the safe *lines* of one memory source; ``""`` when none survive."""
    candidates = [line for line in lines if line]
    kept = [line for line in candidates if memory_text_is_safe(line)]
    if len(kept) < len(candidates):
        from app.observability.logging import get_logger

        get_logger(__name__).warning(
            "recalled_memory_dropped_injection", source=label, dropped=len(candidates) - len(kept)
        )
    if not kept:
        return ""
    return frame_untrusted(label, "\n".join(kept))


__all__ = ["UNTRUSTED_NOTE", "frame_memory_block", "memory_text_is_safe"]
