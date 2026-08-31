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

from typing import Any


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
    "message_item",
]
