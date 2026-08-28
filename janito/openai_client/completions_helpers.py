"""
Shared module-level helpers for the Chat Completions client.

Extracted from :mod:`janito.openai_client.completions_api` so the client
module stays focused on the ``send_prompt`` entry point, the
:class:`CompletionsClient` class and the shared runtime helpers
(``resolve_runtime_config``, progress bar, Enter-cancel detection).
"""

import logging
from typing import Any

# Import general configuration handling
from janito.config_loaders import (
    load_max_input_tokens,
    load_max_output_tokens,
    load_reasoning_level,
)

# Import provider configuration for base URLs and built-in defaults
from janito.provider_accessors import (
    apply_builtin_tools_to_extra_body,
    apply_thinking_to_extra_body,
    get_default_max_input_tokens_from_provider,
    get_default_max_output_tokens_from_provider,
    get_default_reasoning_level_from_provider,
    get_default_thinking_from_provider,
)

# Import tools
from janito.tooling.tools_registry import get_all_tool_schemas

# Shared client helpers (Rich console output, usage summary out-param)
from .client_support import TurnUsage

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


def _resolve_model_settings(
    provider: str,
    model: str,
    thinking: bool,
    reasoning_level: str | None,
) -> tuple[bool, int | None, int | None, str | None]:
    """Resolve thinking mode, token limits and reasoning level for ``model``.

    Returns ``(thinking, max_output_tokens, max_input_tokens,
    reasoning_level)`` where ``thinking`` is the resolved value: the
    explicit ``--thinking`` flag (``True``) when given, otherwise the
    model's built-in default (a ``True`` flag or a pass-through dict such as
    MiniMax-M3's ``{'type': 'adaptive'}``).  See
    :func:`apply_thinking_to_extra_body`.
    """
    # Thinking mode: the explicit --thinking flag wins, otherwise the
    # model's built-in default applies (True for DeepSeek and Alibaba/Qwen,
    # a dict for MiniMax-M3, which reason by default). See
    # janito.providers.get_provider_config.
    if not thinking:
        thinking = get_default_thinking_from_provider(provider, model)
    max_output_tokens = load_max_output_tokens(provider, model)
    if max_output_tokens is None:
        # Fall back to the model's built-in default (from the provider
        # config), then to a global default of 100k tokens.
        max_output_tokens = get_default_max_output_tokens_from_provider(provider, model)
    if max_output_tokens is None:
        max_output_tokens = 100000  # default to 100k tokens if not set in config

    # Load the model's max input tokens (context window) for the usage
    # summary display: a config override (--set max-input-tokens=... or the
    # interactive --config wizard) wins, otherwise the model's built-in
    # default applies.
    max_input_tokens = load_max_input_tokens(provider, model)
    if max_input_tokens is None:
        max_input_tokens = get_default_max_input_tokens_from_provider(provider, model)

    # Reasoning level (reasoning_effort): --reasoning-level CLI arg, then the
    # model-scoped configured value (--set reasoning-level=...), and finally
    # the model's built-in default (from the provider config, e.g. "xhigh"
    # for Alibaba's qwen3.8-max). None means the API's own default applies.
    reasoning_level = reasoning_level or load_reasoning_level(provider, model)
    if reasoning_level is None:
        reasoning_level = get_default_reasoning_level_from_provider(provider, model)
    return thinking, max_output_tokens, max_input_tokens, reasoning_level


def _build_call_kwargs(
    model: str,
    messages: list[dict[str, Any]],
    max_output_tokens: int | None,
    reasoning_level: str | None,
    preserve_thinking: Any,
    thinking,
    tools=None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Build the Chat Completions call parameters for one round.

    ``tools`` is the effective model's built-in (native) tools list from the
    provider config (e.g. Alibaba/Qwen's ``[{"type": "code_interpreter"},
    ...]``); when declared, each ``type`` is sent as a request-body
    ``enable_*`` flag in ``extra_body`` (see
    :func:`apply_builtin_tools_to_extra_body`).  ``None`` sends nothing.
    ``provider`` enables the Gemini-flavor guard in
    :func:`apply_thinking_to_extra_body` (Gemini-flavored providers do not
    accept ``enable_thinking``).
    """
    call_kwargs = {
        "model": model,
        "messages": messages,
        "temperature": 1.0,
    }

    # Add max_tokens if max output tokens is set in config
    if max_output_tokens is not None:
        call_kwargs["max_completion_tokens"] = max_output_tokens

    # Reasoning effort: sent whenever a reasoning level resolves (None means
    # the API's own default applies).
    if reasoning_level:
        call_kwargs["reasoning_effort"] = reasoning_level

    # Pass preserve_thinking in extra_body if defined in config
    if preserve_thinking is not None:
        call_kwargs.setdefault("extra_body", {})[
            "preserve_thinking"
        ] = preserve_thinking

    # Pass the thinking mode in extra_body: enable_thinking for flag-style
    # defaults, or the raw dict for providers with a structured thinking
    # parameter (e.g. MiniMax-M3's {"type": "adaptive"}).  Gemini-flavored
    # providers (google) skip enable_thinking -- the field does not exist on
    # their OpenAI-compatibility API.
    apply_thinking_to_extra_body(call_kwargs, thinking, provider=provider)

    # Pass the effective model's built-in tools (e.g. Alibaba/Qwen's
    # code_interpreter / web_search / web_extractor) as request-body
    # enable_* flags in extra_body.  These are model capabilities, not
    # function tools, so they are enabled whenever the model declares them
    # (see apply_builtin_tools_to_extra_body).
    apply_builtin_tools_to_extra_body(call_kwargs, tools)

    call_kwargs["stream"] = True
    call_kwargs["stream_options"] = {"include_usage": True}
    return call_kwargs


def _finalize_response(
    full_content: str,
    reasoning_content: str | None,
    messages: list[dict[str, Any]],
    usage_out: TurnUsage | None = None,
) -> str:
    """Record the final assistant message and return it.

    ``usage_out`` (when given) receives the display metadata the caller needs
    to render the end-of-turn reports after ``send_prompt`` returns (see
    :func:`janito.openai_client.client_support.display_turn_usage`).
    """
    # Build the assistant message with reasoning_content if available
    assistant_message = {"role": "assistant", "content": full_content}
    if reasoning_content:
        assistant_message["reasoning_content"] = reasoning_content

    # Add assistant message to conversation history
    messages.append(assistant_message)

    if usage_out is not None:
        usage_out.message_count = len(messages)
        usage_out.label = "Messages"
        usage_out.show_cached = True
    return full_content
