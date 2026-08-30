"""
Shared module-level helpers for the native Gemini client.

The wire-format conversions (OpenAI-format chat ``messages`` -> Gemini
``contents``, tool schemas -> ``function_declarations``, usage metadata ->
the shared token-usage shape, call-kwargs building) live in the shared
adapter layer :mod:`janito.llm_adapters.gemini` (issue #90).  This module keeps the
CLI-only helpers (``_resolve_tools``, ``_init_state``, ``_handle_tool_parts``,
``_finalize_response``) that drive the
:class:`~janito.llm_clients.gemini.gemini_api.GeminiClient` turn pipeline;
``_resolve_system_instruction`` is imported from the adapter layer where the
rest of the conversion lives.
"""

import logging
from typing import Any

from janito.llm_adapters.gemini import _resolve_system_instruction
from janito.tooling.executor import ToolExecutor
from janito.tooling.tools_registry import get_session_tool_schemas

# Configure logger for this module
logger = logging.getLogger(__name__)


def _resolve_tools(
    tools: list[dict[str, Any]] | None, mcp_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve the tool schemas (built-in + MCP) in OpenAI format."""
    if tools is None:
        # Merge built-in tools with MCP tools
        built_in_tools = get_session_tool_schemas()
        tools_schemas = built_in_tools + mcp_tools
        logger.debug(
            f"Using {len(built_in_tools)} built-in tools + {len(mcp_tools)} MCP tools"
        )
    else:
        tools_schemas = tools
        logger.debug(f"Using {len(tools_schemas)} provided tools")
    return tools_schemas


def _init_state(
    instructions: str | None,
    previous_messages: list[dict[str, Any]] | None,
    prompt: str,
) -> dict[str, Any]:
    """Build the per-turn conversation state (messages + system instruction)."""
    messages = previous_messages if previous_messages is not None else []
    system = _resolve_system_instruction(instructions, messages)

    # NOTE: check `is not None` (not truthiness). An empty list is a valid,
    # caller-owned history (e.g. after a restart or with --no-system-prompt);
    # using a truthy check would replace it with a new local list and the
    # appended messages would never propagate back to the caller.
    messages.append({"role": "user", "content": prompt})
    return {"messages": messages, "system": system}


def _handle_tool_parts(
    tool_calls: list[dict[str, Any]],
    full_content: str,
    reasoning_content: str | None,
    thought_parts: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tool_executor: ToolExecutor,
) -> None:
    """Record assistant tool_calls, execute them and append tool results.

    The assistant message keeps the model's thought blocks (``thought_parts``)
    and per-call ``thought_signature`` values so the next round can resend
    them verbatim (Gemini 3.x rejects follow-up requests that drop them with
    a 400 "Function call is missing a thought_signature" error).  Tool
    results are appended as ``tool``-role messages carrying the call id,
    name and thought signature.
    """
    assistant_tool_calls = []
    for tc in tool_calls:
        call = {
            "id": tc["id"],
            "type": "function",
            "function": {
                "name": tc["name"],
                "arguments": tc["arguments"],
            },
        }
        if tc.get("thought_signature"):
            call["thought_signature"] = tc["thought_signature"]
        assistant_tool_calls.append(call)
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": full_content,
        "tool_calls": assistant_tool_calls,
    }
    if reasoning_content:
        assistant_message["reasoning_content"] = reasoning_content
    if thought_parts:
        assistant_message["thought_parts"] = thought_parts
    messages.append(assistant_message)

    # Execute every call and send the results back as tool-role messages.
    for tc in tool_calls:
        adapted_call = {
            "id": tc["id"],
            "function": {
                "name": tc["name"],
                "arguments": tc["arguments"],
            },
        }
        tool_message = tool_executor.execute_tool_call(adapted_call)
        result: dict[str, Any] = {
            "role": "tool",
            "content": tool_message["content"],
            "tool_call_id": tc["id"],
            "name": tc["name"],
        }
        if tc.get("thought_signature"):
            result["thought_signature"] = tc["thought_signature"]
        messages.append(result)


def _finalize_response(
    full_content: str,
    reasoning_content: str | None,
    thought_parts: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> str:
    """Record the final assistant message and return it."""
    # No more tool calls, return the final response. Record the final
    # assistant text in the client-side history (keeping the model's thought
    # blocks so follow-up turns can resend them verbatim).
    assistant_message: dict[str, Any] = {"role": "assistant", "content": full_content}
    if reasoning_content:
        assistant_message["reasoning_content"] = reasoning_content
    if thought_parts:
        assistant_message["thought_parts"] = thought_parts
    messages.append(assistant_message)

    return full_content
