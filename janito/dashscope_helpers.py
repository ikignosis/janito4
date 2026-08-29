"""
Shared module-level helpers for the DashScope client.

Extracted from :mod:`janito.dashscope_api` so the client module stays focused
on the ``run_turn`` entry point and the :class:`DashScopeClient` class.
"""

import logging
from typing import Any

from janito.openai_client.client_support import TurnUsage
from janito.provider_accessors import builtin_tools_enable_flags
from janito.tooling.executor import ToolExecutor
from janito.tooling.tools_registry import get_all_tool_schemas

# Configure logger for this module
logger = logging.getLogger(__name__)


def _resolve_tools(
    tools: list[dict[str, Any]] | None, mcp_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve the tool schemas (built-in + MCP)."""
    if tools is None:
        # Merge built-in tools with MCP tools
        built_in_tools = get_all_tool_schemas()
        tools_schemas = built_in_tools + mcp_tools
        logger.debug(
            f"Using {len(built_in_tools)} built-in tools + {len(mcp_tools)} MCP tools"
        )
    else:
        tools_schemas = tools
        logger.debug(f"Using {len(tools_schemas)} provided tools")
    return tools_schemas


def _init_messages(
    instructions: str | None,
    previous_messages: list[dict[str, Any]] | None,
    prompt: str,
) -> list[dict[str, Any]]:
    """Build the conversation, prepending instructions as a system message."""
    messages = previous_messages if previous_messages is not None else []
    if instructions and not any(
        m.get("role") == "system" and m.get("content") for m in messages
    ):
        messages.insert(0, {"role": "system", "content": instructions})

    # NOTE: check `is not None` (not truthiness). An empty list is a valid,
    # caller-owned history (e.g. after a restart or with --no-system-prompt);
    # using a truthy check would replace it with a new local list and the
    # appended messages would never propagate back to the caller.
    messages.append({"role": "user", "content": prompt})
    return messages


def _build_call_kwargs(
    model: str,
    messages: list[dict[str, Any]],
    max_output_tokens: int,
    thinking: bool,
    tools=None,
) -> dict[str, Any]:
    """Build the DashScope call parameters for one round.

    ``tools`` is the effective model's built-in (native) tools list from the
    provider config (e.g. Alibaba/Qwen's ``[{"type": "code_interpreter"},
    ...]``); when declared, each ``type`` is sent as a request-body kwargs
    ``enable_*`` flag (e.g. ``enable_code_interpreter`` / ``enable_search``,
    see :func:`builtin_tools_enable_flags`).  ``None`` sends nothing.
    """
    call_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_output_tokens,
        "result_format": "message",
        "stream": True,
        "incremental_output": True,
    }
    # Enable thinking mode for Qwen models that support it (Alibaba/Qwen
    # reason by default).  Only set when True so models that always
    # reason keep their own default.
    if thinking:
        call_kwargs["enable_thinking"] = True
    # The effective model's built-in tools (e.g. code_interpreter /
    # web_search / web_extractor) are native capabilities enabled through
    # request-body kwargs (e.g. enable_code_interpreter / enable_search).
    # They are set whenever the model declares them; models without built-in
    # tools send nothing.
    flags = builtin_tools_enable_flags(tools)
    if flags:
        call_kwargs.update(flags)
    return call_kwargs


def _handle_tool_blocks(
    tool_use_blocks: list[dict[str, str]],
    full_content: str,
    reasoning_content: str | None,
    messages: list[dict[str, Any]],
    tool_executor: ToolExecutor,
) -> None:
    """Record assistant tool_calls, execute them and append tool results."""
    # Record the assistant's message with its content and tool_calls in the
    # client-side history.
    assistant_tool_calls = []
    for tc in tool_use_blocks:
        assistant_tool_calls.append(
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                },
            }
        )
    assistant_message = {
        "role": "assistant",
        "content": full_content,
        "tool_calls": assistant_tool_calls,
    }
    if reasoning_content:
        assistant_message["reasoning_content"] = reasoning_content
    messages.append(assistant_message)

    # Execute every call and send the results back as tool-role messages.
    for tc in tool_use_blocks:
        # Adapt the DashScope tool-use shape to what the executor expects
        # (id + function{name, arguments}).
        adapted_call = {
            "id": tc["id"],
            "function": {
                "name": tc["name"],
                "arguments": tc["arguments"],
            },
        }
        tool_message = tool_executor.execute_tool_call(adapted_call)
        messages.append(
            {
                "role": "tool",
                "content": tool_message["content"],
                "tool_call_id": tc["id"],
            }
        )


def _finalize_response(
    full_content: str,
    reasoning_content: str | None,
    messages: list[dict[str, Any]],
    usage_out: TurnUsage | None = None,
) -> str:
    """Record the final assistant message and return it.

    ``usage_out`` (when given) receives the display metadata the caller needs
    to render the end-of-turn reports after ``run_turn`` returns (see
    :func:`janito.openai_client.client_support.display_turn_usage`).
    """
    # No more tool calls, return the final response. Record the final
    # assistant text in the client-side history.
    assistant_message = {"role": "assistant", "content": full_content}
    if reasoning_content:
        assistant_message["reasoning_content"] = reasoning_content
    messages.append(assistant_message)

    if usage_out is not None:
        usage_out.message_count = len(messages)
        usage_out.label = "Messages"
        usage_out.show_cached = False
    return full_content
