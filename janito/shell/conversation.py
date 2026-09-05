"""Shared conversation-history helpers for the shell layer.

The ``/history`` command, the ``/compact`` command and the interactive shell
each need to know where the conversation lives for the current API mode
(client-side ``messages_history`` vs Responses ``conversation_items``) and
how to flatten it into the ``(role, content)`` display rows /history
renders.  This module is the single home for that logic; the three consumers
delegate to it so a new API mode only needs to be taught once.
"""

from __future__ import annotations

from typing import Any


def _content_text(content: Any) -> str:
    """Extract the plain text of a Responses ``message`` content block.

    Responses content is a list of typed blocks (``input_text`` /
    ``output_text`` / ``refusal`` / ...).  ``str`` content is returned as-is
    (defensive; the real items always use the block list form).
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text")
            if text:
                parts.append(text)
    return "".join(parts)


def _message_row(item: dict[str, Any]) -> tuple[str, str]:
    """Render a Responses ``message`` item as a ``(role, content)`` row."""
    return item.get("role", "unknown"), _content_text(item.get("content"))


def _function_call_row(item: dict[str, Any]) -> tuple[str, str]:
    """Render a Responses ``function_call`` item as a row."""
    arguments = item.get("arguments") or ""
    return "function_call", f"{item.get('name', '')}({arguments})"


def _function_call_output_row(item: dict[str, Any]) -> tuple[str, str]:
    """Render a Responses ``function_call_output`` item as a row."""
    return "function_call_output", str(item.get("output") or "")


def _reasoning_text(item: dict[str, Any]) -> str:
    """Extract human-readable text from a Responses ``reasoning`` item.

    ``summary`` may be a plain string, a list of summary blocks
    (``[{"type": "summary_text", "text": ...}]``), or an empty list when
    the provider only returns opaque encrypted content (e.g. Meta's Muse
    Spark with ``reasoning.encrypted_content``).  ``text`` is a fallback
    for the same shapes.  Encrypted content is opaque and never rendered.
    """
    for key in ("summary", "text"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list):
            parts = []
            for block in value:
                if isinstance(block, dict):
                    text = block.get("text")
                    if text:
                        parts.append(text)
                elif isinstance(block, str) and block:
                    parts.append(block)
            if parts:
                return "".join(parts)
    return ""


def _reasoning_row(item: dict[str, Any]) -> tuple[str, str] | None:
    """Render a Responses ``reasoning`` item as a row.

    Returns ``None`` when the item carries no human-readable text (e.g.
    an encrypted-only reasoning item kept for stateless replay) so the
    caller can skip it instead of showing an empty ``reasoning`` row.
    """
    text = _reasoning_text(item)
    if not text:
        return None
    return "reasoning", text


#: Per-item-type renderers for Responses input items (see
#: :func:`_responses_item_to_row`); unknown item types fall back to a raw
#: ``(item_type, str(item))`` row.
_ITEM_TO_ROW = {
    "message": _message_row,
    "function_call": _function_call_row,
    "function_call_output": _function_call_output_row,
    "reasoning": _reasoning_row,
}


def _responses_item_to_row(item: dict[str, Any]) -> tuple[str, str] | None:
    """Convert one Responses input item into a ``(role, content)`` display row.

    Stateless Responses providers (e.g. DeepSeek) keep the full conversation
    client-side as Responses input items: ``message`` items (system / user /
    assistant) plus ``function_call`` / ``function_call_output`` items for the
    tool-call rounds.  Each becomes a row so ``/history`` reads like the
    Completions history table.

    Returns ``None`` for items with nothing human-readable to show (currently
    encrypted-only ``reasoning`` items kept for stateless replay).
    """
    item_type = item.get("type", "unknown")
    renderer = _ITEM_TO_ROW.get(item_type)
    if renderer is None:
        return item_type, str(item)
    return renderer(item)


def items_to_rows(items) -> list[tuple[str, str]]:
    """Render Responses input items as ``(role, content)`` display rows.

    Encrypted-only ``reasoning`` items (no readable summary/text, only
    opaque ``encrypted_content`` for stateless replay) are skipped so
    ``/history`` does not show an empty ``reasoning`` row before every
    assistant message.  The stored items are untouched -- only the display
    rows are filtered.
    """
    rows: list[tuple[str, str]] = []
    for item in items or []:
        row = _responses_item_to_row(item)
        if row is not None:
            rows.append(row)
    return rows


def messages_to_rows(messages_history) -> list[tuple[str, str]]:
    """Render Completions-style history messages as display rows.

    Handles both dict messages and message objects (``.role`` / ``.content``
    attributes).
    """
    rows: list[tuple[str, str]] = []
    for msg in messages_history:
        if isinstance(msg, dict):
            rows.append((msg.get("role", "unknown"), msg.get("content") or ""))
        else:
            rows.append((msg.role, msg.content or ""))
    return rows


def is_stateless_conversation(shell) -> bool:
    """Whether the shell's conversation lives in stateless Responses items.

    Stateless Responses providers (e.g. DeepSeek) keep the full conversation
    client-side as input items with the system prompt folded in on the first
    turn, so ``conversation_items[0]`` is a ``system`` message.
    """
    conversation_items = getattr(shell, "conversation_items", None) or []
    return bool(conversation_items and conversation_items[0].get("role") == "system")


def effective_rows(shell) -> list[tuple[str, str]]:
    """Return ``(role, content)`` rows for the shell's whole effective history.

    Mirrors the source selection of the ``/history`` command so the recorded
    ``history_turns`` values index directly into these rows in every API
    mode:

    - stateless Responses: ``conversation_items`` (the system prompt is
      folded in on the first turn);
    - otherwise: ``messages_history``, plus (server-side Responses) the
      display-only ``mirrored_history`` of completed turns and any pending
      (Enter-cancelled) ``conversation_items``.
    """
    conversation_items = getattr(shell, "conversation_items", None) or []
    if is_stateless_conversation(shell):
        return items_to_rows(conversation_items)
    rows = messages_to_rows(getattr(shell, "messages_history", None) or [])
    rows.extend(items_to_rows(getattr(shell, "mirrored_history", None) or []))
    rows.extend(items_to_rows(conversation_items))
    return rows


def recent_conversation_rows(
    shell, limit: int = 5, roles: tuple[str, ...] = ("user", "assistant")
) -> list[tuple[str, str]]:
    """Return up to ``limit`` of the most recent dialogue rows.

    Used by the ``-C/--continue`` resume recap so the user can see where the
    previous session left off without dumping the whole transcript.  Delegates
    to :func:`effective_rows` -- the single source ``/history`` uses in every
    API mode -- and returns the tail of the rows whose role is in ``roles``
    (by default the user/assistant dialogue: the system prompt, the
    ``function_call``/``function_call_output`` tool-call plumbing and the
    ``reasoning`` trace are excluded).

    A tool-heavy turn can end in a long run of assistant-only messages, which
    would push the user's latest prompt out of a plain ``[-limit:]`` tail.
    When that happens the recap is re-anchored on the most recent user row:
    it always shows the latest question followed by the tail of its replies
    (still capped at ``limit`` rows).

    Args:
        shell: The shell whose conversation is read (any API mode).
        limit: How many of the most recent rows to return.
        roles: Row roles kept in the tail (default: the user/assistant
            dialogue).

    Returns:
        The up-to-``limit`` most recent ``(role, content)`` rows whose role
        is in ``roles``, always including the most recent user row when one
        exists.
    """
    rows = effective_rows(shell)
    dialogue = [row for row in rows if row[0] in roles]
    if not dialogue:
        return []
    tail = dialogue[-limit:]
    if any(role == "user" for role, _ in tail):
        return tail
    # A long reply run pushed the latest user prompt out of the tail; anchor
    # the recap on it so the user sees their last question and the tail of
    # the answer that followed. Only meaningful when at least one reply can
    # be shown alongside the question (limit >= 2).
    user_indexes = [i for i, (role, _) in enumerate(dialogue) if role == "user"]
    if limit < 2 or not user_indexes:
        return tail
    anchored = dialogue[user_indexes[-1]:]
    if len(anchored) <= limit:
        return anchored
    # Keep the question and the most recent replies, dropping the oldest
    # intermediate assistant rows so the recap stays bounded.
    return [anchored[0]] + anchored[-(limit - 1):]


__all__ = [
    "effective_rows",
    "is_stateless_conversation",
    "items_to_rows",
    "messages_to_rows",
    "recent_conversation_rows",
]
