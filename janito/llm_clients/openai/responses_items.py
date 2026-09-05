"""Responses ``input`` item builders shared by the clients and the shell.

The Responses API represents conversation turns as typed input items
(``message`` / ``function_call`` / ``function_call_output`` / ``reasoning``).
The same ``message`` item shape was previously constructed inline in the
client modules (``responses_state`` / ``responses_helpers`` /
``conversations_api``), the shell's ``/compact`` command and the interactive
loop; this module is the single home for it.  The web loop builds its own
items through the shared adapter layer (``janito.llm_adapters.responses``,
which must not import ``llm_clients`` -- issue #90).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ConversationResult:
    """Outcome of one ``run_turn`` turn against the Responses API.

    Moved here from :mod:`janito.llm_clients.openai.conversations_api`
    (issue #110): the shared helpers module
    (:mod:`janito.llm_clients.openai.responses_helpers`) needs the type at
    import time while the client module needs the helpers, so the type lives
    in this leaf instead of either side of that cycle.

    Attributes:
        content: The assistant's final text (after any tool-call rounds).
        response_id: The server-side id of the final response. For providers
            that keep the conversation server-side (``stateless_mode``
            True), pass it as ``previous_response_id`` to the next
            ``run_turn`` call to continue the conversation. For stateless
            providers (``stateless_mode`` True) this is always ``None``
            and the history is carried client-side in ``input_items`` instead.
        message_count: Number of responses chained during this turn (1 +
            number of tool-call rounds).
        input_items: The full conversation as Responses input items, only for
            stateless providers (``stateless_mode`` True). Pass it back
            as ``previous_items`` to the next ``run_turn`` call so the
            entire history is re-sent (the server keeps no state). ``None``
            for server-side providers, which chain with ``response_id``
            (``previous_items`` is then only used to carry the pending user
            messages of an Enter-cancelled turn).
        turn_items: Display-only mirror of the completed turn as Responses
            input items (the user prompt, the assistant text and
            ``function_call`` / ``function_call_output`` items of any
            tool-call rounds, and the final assistant text).  Kept so the
            shell can render ``/history`` for server-side Responses
            providers, whose real conversation lives on the server and is
            never fetched back.  ``None`` only for turn results that did not
            go through the standard client pipeline.
    """

    content: str
    response_id: str | None
    message_count: int = 1
    input_items: list[dict[str, Any]] | None = None
    turn_items: list[dict[str, Any]] | None = None


def message_item(role: str, text: str) -> dict[str, Any]:
    """Build a Responses ``message`` input item.

    ``system`` / ``user`` messages use ``input_text`` content blocks and
    ``assistant`` messages use ``output_text``, matching how the client
    builds its items.

    Args:
        role: The message role (``"system"`` / ``"user"`` / ``"assistant"``).
        text: The plain-text content.

    Returns:
        The Responses input item dict.
    """
    block_type = "output_text" if role == "assistant" else "input_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": block_type, "text": text}],
    }


__all__ = [
    "ConversationResult",
    "message_item",
]
