"""Stack-like conversation levels for the interactive shell (issue #124)."""

from __future__ import annotations

import copy

SNAPSHOT_FIELDS = (
    "messages_history",
    "history_turns",
    "previous_response_id",
    "conversation_items",
    "conversation_turn",
    "response_chain",
    "response_turn",
    "mirrored_history",
    "mirrored_turn",
)


def snapshot_shell(shell) -> dict:
    """Deep-copy the shell's conversation state."""
    snap = {}
    for field in SNAPSHOT_FIELDS:
        snap[field] = copy.deepcopy(getattr(shell, field, None))
    return snap


def restore_shell(shell, snap: dict) -> None:
    """Restore the shell's conversation state from a snapshot."""
    for field in SNAPSHOT_FIELDS:
        setattr(shell, field, copy.deepcopy(snap[field]))


class ConversationStack:
    """LIFO stack of conversation snapshots."""

    def __init__(self) -> None:
        self._stack: list[dict] = []

    @property
    def depth(self) -> int:
        return len(self._stack)

    def push(self, shell) -> int:
        self._stack.append(snapshot_shell(shell))
        return self.depth

    def pop(self, shell) -> int:
        if not self._stack:
            raise IndexError("empty")
        snap = self._stack.pop()
        restore_shell(shell, snap)
        return self.depth

    def clear(self) -> None:
        self._stack.clear()
