"""
Shared module-level helpers for the Responses API client.

Extracted from :mod:`janito.openai_client.conversations_api` so the client
module stays focused on the ``run_turn`` entry point and the
:class:`ResponsesClient` class.
"""

import logging
from typing import Any

# Import the tool executor (routes tool calls to the MCP manager or the
# built-in registry and tracks usage/used-files/changes around each call)
from janito.tooling.executor import ToolExecutor

# Import tools
from janito.tooling.tools_registry import get_all_tool_schemas

# Shared client helpers (usage summary out-param) and the Responses API
# stream consumer.
from .client_support import TurnUsage
from .responses_stream import _convert_tools_to_responses_format

# Configure logger for this module
logger = logging.getLogger(__name__)


def _pending_items_for_cancel(state: dict[str, Any]) -> list[dict[str, Any]] | None:
    """User messages to re-send after an Enter-cancel (server-side Responses).

    The aborted request's server-side response is discarded by the provider
    when the stream is interrupted (OpenAI answers ``previous_response_id not
    found`` for it), so the caller must not chain the next turn from it.
    Hand back the pending user messages (the cancelled prompt, plus any
    earlier cancelled prompts still awaiting a completed response in the
    caller's chain) so the next turn re-sends them as input items chained
    from the last completed response id.

    Returns ``None`` only when the state carries neither pending items nor a
    string prompt (defensive; the real flow always builds one of the two).
    """
    pending = state.get("pending_items")
    if pending:
        return [dict(item) for item in pending]
    input_items = state.get("input_items")
    if isinstance(input_items, str):
        return [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": input_items}],
            }
        ]
    return None


def _resolve_tools(
    tools: list[dict[str, Any]] | None, mcp_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve the tool schemas (built-in + MCP) and convert to Responses format."""
    if tools is None:
        built_in_tools = get_all_tool_schemas()
        tools_schemas = built_in_tools + mcp_tools
        logger.debug(
            f"Using {len(built_in_tools)} built-in tools + {len(mcp_tools)} MCP tools"
        )
    else:
        tools_schemas = tools
        logger.debug(f"Using {len(tools_schemas)} provided tools")
    # The Responses API expects function tools with name/description/parameters
    # at the top level, while the shared schema builders emit the Chat
    # Completions shape with those fields nested under "function". Convert once
    # up front so every stream round sends the correct shape.
    return _convert_tools_to_responses_format(tools_schemas)


def _validate_stream_result(
    responses_in_server: bool,
    stream_response_id: str | None,
    full_content: str,
    tool_calls: list[dict[str, Any]] | None,
    model: str,
) -> None:
    """Raise a clear error when a server-side response came back empty."""
    if (
        responses_in_server
        and stream_response_id is None
        and not full_content
        and not tool_calls
    ):
        raise RuntimeError(
            f"The Responses API returned an empty response for model "
            f"'{model}'. The model may not be supported by this "
            f"endpoint."
        )


def _handle_tool_calls(
    tool_calls: list[dict[str, Any]],
    full_content: str,
    conversation_items: list[dict[str, Any]] | None,
    tool_executor: ToolExecutor,
    turn_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    """Record and execute tool calls, returning the updated input items.

    ``conversation_items`` is the stateless client-side history (appended so
    the next request re-sends the complete story); ``turn_items`` is the
    display-only mirror of the current turn kept so the shell can render
    ``/history`` for server-side Responses providers (whose real history
    lives on the server and is never fetched back).  Either target may be
    ``None``; items are recorded wherever a target is provided.
    """
    # Record the assistant's tool calls in the client-side history
    # (stateless providers) so the next request re-sends the complete story,
    # and in the /history display mirror (all providers). Server-side
    # providers keep the conversation on the server, so only the mirror is
    # filled client-side.
    targets: list[list[dict[str, Any]]] = []
    if conversation_items is not None:
        targets.append(conversation_items)
    if turn_items is not None:
        targets.append(turn_items)

    if full_content:
        assistant_item = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_content}],
        }
        for target in targets:
            target.append(dict(assistant_item))
    for tc in tool_calls:
        call_item = {
            "type": "function_call",
            "call_id": tc["call_id"],
            "name": tc["name"],
            "arguments": tc["arguments"],
        }
        for target in targets:
            target.append(dict(call_item))

    # Execute every call and send the results back as function_call_output
    # items chained to the response that produced the calls (server-side) or
    # appended to the full history (stateless).
    tool_outputs: list[dict[str, Any]] = []
    for tc in tool_calls:
        # Adapt the Responses API call shape to what the executor expects
        # (id + function{name, arguments}).
        adapted_call = {
            "id": tc["call_id"],
            "function": {
                "name": tc["name"],
                "arguments": tc["arguments"],
            },
        }
        tool_message = tool_executor.execute_tool_call(adapted_call)
        tool_outputs.append(
            {
                "type": "function_call_output",
                "call_id": tc["call_id"],
                "output": tool_message["content"],
            }
        )
    for target in targets:
        target.extend(dict(item) for item in tool_outputs)
    if conversation_items is not None:
        return conversation_items
    return tool_outputs


def _finalize_conversation(
    full_content: str,
    conversation_items: list[dict[str, Any]] | None,
    message_count: int,
    response_id: str | None,
    responses_in_server: bool,
    turn_items: list[dict[str, Any]] | None = None,
    usage_out: TurnUsage | None = None,
) -> Any:
    """Assemble the final ConversationResult.

    ``usage_out`` (when given) receives the display metadata the caller needs
    to render the end-of-turn reports after ``run_turn`` returns (see
    :func:`janito.openai_client.client_support.display_turn_usage`).
    """
    from .conversations_api import ConversationResult

    # Record the final assistant text in the client-side history (stateless
    # providers) and in the /history display mirror (all providers).
    if full_content:
        assistant_item = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_content}],
        }
        if conversation_items is not None:
            conversation_items.append(assistant_item)
        if turn_items is not None:
            turn_items.append(dict(assistant_item))

    if usage_out is not None:
        usage_out.message_count = message_count
        usage_out.label = "Responses"
        usage_out.show_cached = True
    return ConversationResult(
        content=full_content,
        response_id=response_id if responses_in_server else None,
        message_count=message_count,
        input_items=conversation_items,
        turn_items=turn_items,
    )
