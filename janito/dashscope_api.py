"""
DashScope SDK client module for sending prompts through the **native**
DashScope SDK (``dashscope.Generation.call``).

This is the counterpart of :mod:`janito.openai_client.completions_api` for the
``"DashScope"`` API type: the same config resolution, tool loading, MCP
support, progress spinner, reasoning panel, used-files report
and token-usage summary, but talking to the DashScope native API through the
official ``dashscope`` package instead of an OpenAI-compatible endpoint.

The ``dashscope`` package is **optional**: the API type is only accepted by
``--set api-type=DashScope`` when the package is installed
(``janito.providers.REQUIRES_BY_API_TYPE``), and this module refuses to run
without it, with an actionable install message.  Because the package may be
absent, the import happens lazily inside :func:`_create_client` (checked with
``importlib.util.find_spec``, mirroring the web-mode extra check) rather than
at module import time, so importing ``janito`` never requires ``dashscope``.

Like the Completions implementation, this module owns the conversation
history **client-side**: the DashScope generation API is stateless, so every
turn re-sends the full ``messages`` list.  Tool calls are executed with the
shared :class:`~janito.tooling.executor.ToolExecutor` and their ``tool``-role
messages are appended to the history before the next round, repeating until
the model emits a final text answer.  ``run_turn`` returns the assistant
text and mutates ``previous_messages`` in place (Completions-style), so the
interactive shell treats this mode exactly like Completions.

Unlike the OpenAI-compatible types, the DashScope SDK talks to the **native**
DashScope API (``https://dashscope-intl.aliyuncs.com/api/v1`` for the
international region).  The base URL is a module-level global on the
``dashscope`` package (``dashscope.base_http_api_url``); it is set from the
provider's ``endpoint_by_api_type`` map (or a config endpoint override) before
each call.

The DashScope stream handling lives in
:mod:`janito.openai_client.dashscope_stream` and the shared client helpers in
:mod:`janito.openai_client.client_support`.
"""

from __future__ import annotations

import importlib.util
import logging
from types import SimpleNamespace
from typing import Any

# Shared agent-loop pipeline (see Client.run_turn) implemented by DashScopeClient.
from janito.openai_client.api_config import APIConfig
from janito.openai_client.base_client import Client

# Shared client helpers: the error classifier the native-SDK clients use to
# pick the observer's explainer explicitly.
from janito.openai_client.client_support import _classify_error
from janito.openai_client.dashscope_stream import _stream_response
from janito.tooling.executor import ToolExecutor
from janito.ui_config import UIConfig

from .dashscope_helpers import (
    _build_call_kwargs,
    _finalize_response,
    _handle_tool_blocks,
    _init_messages,
    _resolve_tools,
)

# Configure logger for this module
logger = logging.getLogger(__name__)


def _create_client(base_url: str | None, api_key: str) -> SimpleNamespace:
    """Prepare the native DashScope SDK client, guarding the optional package.

    The ``dashscope`` package is optional (see
    ``janito.providers.REQUIRES_BY_API_TYPE``), so its availability is checked
    explicitly with ``importlib.util.find_spec`` (mirroring the web-mode extra
    check) and the import happens lazily -- importing ``janito`` never
    requires ``dashscope``.

    The DashScope SDK is stateless at the module level: the base URL is a
    module global (``dashscope.base_http_api_url``) and the API key is passed
    per call.  This helper therefore returns a lightweight handle carrying
    the resolved ``base_url`` / ``api_key`` for the call loop instead of a
    client object.

    Args:
        base_url: The native-SDK base URL (from the provider's
            ``endpoint_by_api_type`` map or a config endpoint override).
        api_key: The API key from the auth store.

    Returns:
        A ``SimpleNamespace`` with ``base_url`` and ``api_key``.

    Raises:
        RuntimeError: If the ``dashscope`` package is not installed, with an
            actionable install message.
    """
    if importlib.util.find_spec("dashscope") is None:
        raise RuntimeError(
            "API type 'DashScope' requires the optional 'dashscope' package, "
            "which is not installed. Install it with: pip install dashscope"
        )
    import dashscope

    # The DashScope SDK routes requests through the module-level
    # ``base_http_api_url`` global.  Point it at the resolved endpoint (the
    # provider's native-SDK base URL, or a config endpoint override) before
    # the first call; the API key is passed per call below.
    if base_url:
        dashscope.base_http_api_url = base_url
        logger.debug(f"DashScope base_http_api_url set to {base_url}")

    return SimpleNamespace(base_url=base_url, api_key=api_key)


def run_turn(
    api_config: APIConfig,
    prompt: str,
    *,
    ui_config: UIConfig | None = None,
    verbose: bool = False,
    previous_messages: list[dict[str, Any]] | None = None,
    instructions: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> str:
    """Send a prompt through the native DashScope SDK and return the answer.

    Thin config-driven wrapper (issue #70): all resolved session config
    (provider, model, endpoint, api_key, token limits, reasoning level,
    thinking, preserve_thinking, use_mcp)
    arrives in ``api_config`` -- built once per session by ``build_api_config`` --
    and the UI-side stream runner / turn observer arrive separately in
    ``ui_config`` -- so this entry point performs no config-store /
    auth-store reads of its own.

    The conversation history is owned **client-side**: ``previous_messages``
    is mutated in place (user and assistant turns are appended) so the
    interactive shell's history keeps growing, exactly like Completions mode.

    Args:
        api_config: The resolved, immutable
            :class:`~janito.openai_client.api_config.APIConfig` for this
            session.
        prompt: The user prompt to send
        ui_config: The injected, immutable
            :class:`~janito.ui_config.UIConfig` (per-round stream runner +
            turn observer) for this session.
        verbose: Explicit per-call emission gate for the verbose call/response
            dumps (``False`` = no dumps).
        previous_messages: List of previous message dicts for conversation
            context (mutated in place).  DashScope accepts ``system``-role
            messages directly, so no extraction is needed (unlike the
            Anthropic Messages API).
        instructions: Accepted for signature parity with the other clients.
            DashScope takes the system prompt as a ``system``-role message in
            ``messages``; when provided as a string it is prepended as one.
        tools: Optional list of tool schemas to pass. If None, uses all
            available tools. If an empty list, no tools are passed.

    Returns:
        The assistant's final text (after any tool-call rounds).

    Raises:
        RuntimeError: If the ``dashscope`` package is not installed.

    Note:
        The end-of-turn report (used files + token-usage summary) is
        delivered by the client itself to the injected observer's
        ``on_turn_complete``; there is no ``usage_out`` out-param (issue #82).

    Note:
        Thinking mode is resolved into ``api_config.thinking`` at build time:
        the explicit ``--thinking`` / ``/thinking`` flag wins, otherwise the
        provider's built-in default applies (``True`` for Alibaba/Qwen,
        sent as ``enable_thinking=True``).
    """
    logger.info("Sending prompt to DashScope API (native SDK)")
    return DashScopeClient(api_config, ui_config).run_turn(
        prompt,
        verbose=verbose,
        previous_messages=previous_messages,
        instructions=instructions,
        tools=tools,
    )


class DashScopeClient(Client):
    """Native DashScope SDK client (``Generation.call``).

    The conversation history is owned **client-side**: ``previous_messages``
    is mutated in place (user/assistant turns are appended), exactly like
    Completions mode.  The DashScope native API is stateless, so the full
    history is re-sent on every round.  Every hook forwards to this module's
    globals so test monkeypatches keep working.
    """

    api_type = "DashScope"
    backend_default = "https://dashscope-intl.aliyuncs.com/api/v1"

    def _create_sdk_client(self, base_url, api_key):
        return _create_client(base_url, api_key)

    def _create_tool_executor(self, mcp_manager):
        return ToolExecutor(mcp_manager)

    def _resolve_tools(self, tools, mcp_tools):
        return _resolve_tools(tools, mcp_tools)

    def _resolve_model_settings(self, provider, model):
        # All resolved at build time into the APIConfig (issue #70).  The
        # native DashScope SDK does not use reasoning_effort, so the
        # reasoning level is dropped; the token limits and thinking come
        # straight from the resolved APIConfig.
        return (
            self.api_config.thinking,
            self.api_config.max_output_tokens,
            self.api_config.max_input_tokens,
            None,
        )

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
        # Build the conversation.  Unlike the Anthropic Messages API, DashScope
        # accepts ``system``-role messages directly, so the history is sent
        # as-is; a string ``instructions`` value is prepended as a system
        # message.
        return _init_messages(instructions, previous_messages, prompt)

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
        # the effective provider and sent as request-body enable_* kwargs
        # (see dashscope_helpers._build_call_kwargs).  They are resolved for
        # the DashScope API type, so API types without built-in tools (e.g.
        # alibaba's qwen3.8-max) send nothing.
        from janito.provider_accessors import get_default_tools_from_provider

        tools = get_default_tools_from_provider(
            self.api_config.provider, model, api_type="DashScope"
        )
        # The DashScope native API is stateless and the full history is
        # re-sent on every round.
        return _build_call_kwargs(model, state, max_output_tokens, thinking, tools)

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
        except Exception as e:
            # The dashscope SDK raises its own exception types; classify the
            # failure explicitly (auth / not-found / unknown) so the observer
            # picks the right explainer (the exception is always re-raised).
            self.observer.on_error(
                e,
                provider=self.api_config.provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                error_kind=_classify_error(e),
            )
            raise
        return full_content, reasoning_content, tool_calls, usage_info, raw_attrs

    def _handle_tool_calls(
        self, tool_calls, full_content, reasoning_content, state, tool_executor
    ):
        # Record the assistant's message with its content and tool_calls in
        # the client-side history, then execute every call and send the
        # results back as tool-role messages before looping to get the final
        # answer.
        _handle_tool_blocks(
            tool_calls, full_content, reasoning_content, state, tool_executor
        )
        return state

    def _finalize(
        self,
        full_content,
        reasoning_content,
        state,
    ):
        # No more tool calls, return the final response.
        return _finalize_response(full_content, reasoning_content, state)


__all__ = [
    "run_turn",
]
