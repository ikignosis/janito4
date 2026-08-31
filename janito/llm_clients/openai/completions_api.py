"""
OpenAI client module for sending prompts to OpenAI-compatible endpoints.
Uses streaming (SSE) to display tokens as they arrive.
"""

import logging
from typing import Any

from openai import AuthenticationError, NotFoundError, OpenAI

# Import the tool executor (routes tool calls to the MCP manager or the
# built-in registry and tracks usage/used-files/changes around each call)
from ...tooling.executor import ToolExecutor

# Resolved, immutable per-session configuration (issue #70): the turn
# pipeline consumes it instead of re-reading the config/auth stores.
from ..api_config import APIConfig

# Shared agent-loop pipeline (see Client.run_turn) implemented by
# CompletionsClient; ``UIConfig`` is the structural UI-behaviour protocol the
# pipeline depends on (the concrete frozen bundle lives in
# ``janito.ui.config``, issue #90).
from ..base_client import Client, UIConfig

# Shared client helpers (the per-round stream runner, Rich console output and
# auth-error explainer are injected by the CLI via ``Client``; see
# ``janito.ui`` for the UI-side pieces and ``client_support`` for the
# remaining LLM-side helpers).
from .completions_helpers import _build_call_kwargs, _finalize_response, _resolve_tools
from .completions_stream import _stream_response

# Import tools

# Import used-files tracking (best-effort, never fails)


# Configure logger for this module
logger = logging.getLogger(__name__)


def run_turn(
    api_config: APIConfig,
    prompt: str,
    *,
    ui_config: UIConfig | None = None,
    verbose: bool = False,
    previous_messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> str:
    """Send prompt to OpenAI endpoint and return response using streaming.

    Thin config-driven wrapper (issue #70): all resolved session config
    (provider, model, endpoint, api_key, token limits, reasoning level,
    thinking, preserve_thinking, use_mcp)
    arrives in ``api_config`` -- built once per session by ``build_api_config`` --
    and the UI-side stream runner / turn observer arrive separately in
    ``ui_config`` -- so this entry point performs no config-store /
    auth-store reads of its own.

    Args:
        api_config: The resolved, immutable
            :class:`~janito.llm_clients.api_config.APIConfig` for this
            session.
        prompt: The user prompt to send
        ui_config: The injected, immutable
            :class:`~janito.ui.config.UIConfig` (per-round stream runner +
            turn observer) for this session.
        verbose: Explicit per-call emission gate for the verbose call/response
            dumps (``False`` = no dumps).
        previous_messages: List of previous message dicts for conversation
            context (mutated in place).
        tools: Optional list of tool schemas to pass. If None, uses all
            available tools. If an empty list, no tools are passed.

    Note:
        The end-of-turn report (used files + token-usage summary) is
        delivered by the client itself to the injected observer's
        ``on_turn_complete``; there is no caller-supplied out-param (issue #82).

    Note:
        Thinking mode is resolved into ``api_config.thinking`` at build time:
        the explicit ``--thinking`` / ``/thinking`` flag wins, otherwise the
        provider's built-in default applies (``True`` for DeepSeek and
        Alibaba/Qwen, sent as ``extra_body={'enable_thinking': True}``; a
        pass-through dict such as MiniMax-M3's ``{'type': 'adaptive'}`` is
        sent as ``extra_body={'thinking': {...}}``).
    """
    logger.info("Sending prompt to API")
    return CompletionsClient(api_config, ui_config).run_turn(
        prompt,
        verbose=verbose,
        previous_messages=previous_messages,
        tools=tools,
    )


class CompletionsClient(Client):
    """Chat Completions client (``client.chat.completions.create``).

    The conversation history is owned **client-side**: the caller-owned
    ``previous_messages`` list is mutated in place (user/assistant turns are
    appended), so the interactive shell's history keeps growing.  Every hook
    forwards to this module's globals so test monkeypatches keep working.
    """

    api_type = "Completions"

    def _create_sdk_client(self, base_url, api_key):
        # base_url can be None for standard OpenAI
        return OpenAI(api_key=api_key, base_url=base_url)

    def _create_tool_executor(self, mcp_manager):
        return ToolExecutor(mcp_manager)

    def _resolve_tools(self, tools, mcp_tools):
        return _resolve_tools(tools, mcp_tools)

    def _init_conversation_state(
        self,
        prompt,
        provider,
        model,
        *,
        previous_messages=None,
        previous_response_id=None,
        previous_items=None,
        instructions=None,
    ):
        # Use previous messages if provided, otherwise start with the user
        # prompt.  NOTE: check `is not None` (not truthiness). An empty list
        # is a valid, caller-owned history (e.g. after a restart or with
        # --no-system-prompt); using a truthy check would replace it with a
        # new local list and the appended messages would never propagate back
        # to the caller, silently resetting the history on every turn.
        messages = previous_messages if previous_messages is not None else []
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_call_kwargs(
        self,
        model,
        state,
        max_output_tokens,
        reasoning_effort,
        preserve_thinking,
        thinking,
    ):
        # The effective model's built-in tools (e.g. Alibaba/Qwen's
        # code_interpreter / web_search / web_extractor) are resolved for
        # the effective provider and sent as request-body enable_* flags in
        # extra_body (see completions_helpers._build_call_kwargs).  They are
        # resolved for the Completions API type, so API types without
        # built-in tools (e.g. alibaba's qwen3.8-max) send nothing.
        from janito.providers.registry import get_provider

        found = get_provider(self.api_config.provider)
        tools = (
            found.tools(model, api_type="Completions") if found is not None else None
        )
        return _build_call_kwargs(
            model,
            state,
            max_output_tokens,
            reasoning_effort,
            preserve_thinking,
            thinking,
            tools,
            provider=self.api_config.provider,
        )

    def _run_stream_round(
        self,
        client,
        call_kwargs,
        tools_schemas,
        state,
        *,
        base_url,
        api_key,
        model,
    ):
        try:
            (
                full_content,
                reasoning_content,
                tool_calls,
                usage_info,
                raw_attrs,
            ) = self._invoke_stream_runner(
                _stream_response, client, call_kwargs, tools_schemas
            )
        except NotFoundError as e:
            self.observer.on_error(
                e, base_url=base_url, model=model, error_kind="not_found"
            )
            raise
        except AuthenticationError as e:
            self.observer.on_error(
                e,
                provider=self.api_config.provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                error_kind="auth",
            )
            raise
        return full_content, reasoning_content, tool_calls, usage_info, raw_attrs

    def _handle_tool_calls(
        self, tool_calls, full_content, reasoning_content, state, tool_executor
    ):
        # Build the assistant message (with tool_calls), execute every call
        # and append the tool responses to the history, then loop to get the
        # final response after the tool calls.
        tool_executor.handle_tool_calls(tool_calls, state, full_content)
        return state

    def _finalize(
        self,
        full_content,
        reasoning_content,
        state,
    ):
        return _finalize_response(full_content, reasoning_content, state)
