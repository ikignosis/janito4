"""Shared conversation-history helpers (turn markers / truncation).

The shell's interactive loop, the ``/rewind`` command and the web backend's
WebSocket rollback all truncate a conversation back to the most recent
recorded turn start (``history_turns``); this module is the single home for
that slice.  It lives at the root level because both the ``shell`` and
``web`` domains may import from it (see the allowed-edge matrix in
``tests/test_import_graph.py``).
"""

from __future__ import annotations

from typing import Any


def rollback_to_last_turn(messages: list[Any], history_turns: list[int]) -> int:
    """Roll an aborted/errored turn back, dropping its recorded start.

    Used by the interactive shell and the web backend when a turn is
    interrupted or errors: the most recent recorded turn start is dropped
    **even when the truncation is a no-op** (Responses modes keep the
    conversation outside ``messages``, so the recorded row count can exceed
    the message count) -- otherwise /history would keep showing a marker for
    a turn that never completed.

    Args:
        messages: The client-side conversation history (mutated in place).
        history_turns: The recorded turn-start row counts (mutated in place).

    Returns:
        The number of messages removed (0 when nothing was truncated).
    """
    if not history_turns:
        return 0
    start = history_turns[-1]
    removed = max(0, len(messages) - start)
    if removed:
        del messages[start:]
    history_turns.pop()
    return removed


def truncate_to_last_turn(messages: list[Any], history_turns: list[int]) -> int:
    """Truncate ``messages`` back to the most recent recorded turn start.

    Used by ``/rewind``: only truncates when the history is strictly past
    the recorded start (nothing to undo otherwise) and drops the recorded
    start only then, so the recorded turns stay untouched at the turn
    boundary.

    Args:
        messages: The client-side conversation history (mutated in place).
        history_turns: The recorded turn-start row counts (mutated in place).

    Returns:
        The number of messages removed (0 = nothing to undo).
    """
    if not history_turns:
        return 0
    start = history_turns[-1]
    if len(messages) <= start:
        return 0
    removed = len(messages) - start
    del messages[start:]
    history_turns.pop()
    return removed


__all__ = [
    "rollback_to_last_turn",
    "truncate_to_last_turn",
]
