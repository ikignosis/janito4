"""
Gemini SDK client module for sending prompts through the **native** Gemini
API (``client.models.generate_content_stream``, the stable ``generateContent``
API from the ``google-genai`` package).

This is the counterpart of :mod:`janito.llm_clients.openai.completions_api` for the
``"Gemini"`` API type: the same config resolution, tool loading, MCP support,
progress spinner, reasoning panel, used-files report and token-usage summary,
but talking to the native Gemini API through the official ``google-genai``
package instead of Google's OpenAI-compatibility layer.

The ``google-genai`` package is **optional**: the API type is only accepted by
``--set api-type=Gemini`` when the package is installed
(``janito.providers.REQUIRES_BY_API_TYPE``), and this module refuses to run
without it, with an actionable install message.  Because the package may be
absent, the import happens lazily inside :func:`_create_client` (checked with
``importlib.util.find_spec``, mirroring the web-mode extra check) rather than
at module import time, so importing ``janito`` never requires ``google-genai``.

Like the Completions implementation, this module owns the conversation history
**client-side**: the Gemini ``generateContent`` API is stateless, so every
turn re-sends the full ``contents`` list (plus the top-level
``system_instruction``).  Tool calls are executed with the shared
:class:`~janito.tooling.executor.ToolExecutor` and their ``function_response``
parts are appended to the history before the next round, repeating until the
model emits a final text answer.  ``run_turn`` returns the assistant text
and mutates ``previous_messages`` in place (Completions-style), so the
interactive shell treats this mode exactly like Completions.

Gemini 3.x models reason by default: thought parts stream alongside the
answer and are surfaced in the reasoning panel.  Stateless multi-turn
requests must resend the model's thought blocks verbatim, so the client keeps
them (``thought_parts`` plus the per-call ``thought_signature``) in the
client-side history and echoes them back on the next round.

The Gemini stream handling lives in :mod:`janito.llm_clients.gemini.gemini_stream`
and the wire-format helpers in :mod:`janito.llm_clients.gemini.gemini_helpers`.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

# The call-kwargs builder lives in the shared adapter layer
# (janito.llm_adapters.gemini, issue #90); the CLI-only helpers stay in
# gemini_helpers.
from janito.llm_adapters.gemini import _build_call_kwargs
from janito.tooling.executor import ToolExecutor

# Shared agent-loop pipeline (see Client.run_turn) implemented by
# GeminiClient; ``UIConfig`` is the structural UI-behaviour protocol the
# pipeline depends on (the concrete frozen bundle lives in
# ``janito.ui.config``, issue #90).
from ..api_config import APIConfig
from ..base_client import Client, UIConfig

# Shared client helpers: the error classifier the native-SDK clients use to
# pick the observer's explainer explicitly.
from ..client_support import _classify_error
from .gemini_helpers import (
    _finalize_response,
    _handle_tool_parts,
    _init_state,
    _resolve_tools,
)
from .gemini_stream import _stream_response

# Configure logger for this module
logger = logging.getLogger(__name__)


def _create_client(base_url: str | None, api_key: str) -> Any:
    """Create the native Gemini SDK client, guarding the optional package.

    The ``google-genai`` package is optional (see
    ``janito.providers.REQUIRES_BY_API_TYPE``), so its availability is checked
    explicitly with ``importlib.util.find_spec`` (mirroring the web-mode extra
    check) and the import happens lazily -- importing ``janito`` never
    requires ``google-genai``.

    Args:
        base_url: The native-SDK base URL (from the provider's
            ``endpoint_by_api_type`` map or a config endpoint override).
            ``None`` uses the SDK's default
            (``https://generativelanguage.googleapis.com``).
        api_key: The API key from the auth store.

    Returns:
        A ``google.genai.Client`` instance.

    Raises:
        RuntimeError: If the ``google-genai`` package is not installed, with
            an actionable install message.
    """
    try:
        spec = importlib.util.find_spec("google.genai")
    except ModuleNotFoundError:
        spec = None
    if spec is None:
        raise RuntimeError(
            "API type 'Gemini' requires the optional 'google-genai' package, "
            "which is not installed. Install it with: pip install google-genai"
        )
    from google import genai

    http_options = {"base_url": base_url} if base_url else None
    return genai.Client(api_key=api_key, http_options=http_options)


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
    """Send a prompt through the native Gemini SDK and return the answer.

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
            :class:`~janito.llm_clients.api_config.APIConfig` for this
            session.
        prompt: The user prompt to send
        ui_config: The injected, immutable
            :class:`~janito.ui.config.UIConfig` (per-round stream runner +
            turn observer) for this session.
        verbose: Explicit per-call emission gate for the verbose call/response
            dumps (``False`` = no dumps).
        previous_messages: List of previous message dicts for conversation
            context (mutated in place). A leading ``"system"``-role message is
            extracted and sent as the top-level Gemini
            ``system_instruction``.
        instructions: System instructions for the conversation (sent as the
            top-level Gemini ``system_instruction``). When ``None``, a
            leading ``"system"``-role message in ``previous_messages`` is
            used.
        tools: Optional list of tool schemas to pass. If None, uses all
            available tools. If an empty list, no tools are passed.

    Returns:
        The assistant's final text (after any tool-call rounds).

    Raises:
        RuntimeError: If the ``google-genai`` package is not installed.

    Note:
        The end-of-turn report (used files + token-usage summary) is
        delivered by the client itself to the injected observer's
        ``on_turn_complete``; there is no caller-supplied out-param (issue #82).

    Note:
        Thinking mode is resolved into ``api_config.thinking`` at build time.
        For Gemini models the flag is accepted for parity only: Gemini 3.x
        models reason by default and thinking depth is controlled through
        ``reasoning_effort`` (mapped to the model's ``thinking_level``)
        instead of a thinking flag.
    """
    logger.info("Sending prompt to Gemini API (native SDK)")
    return GeminiClient(api_config, ui_config).run_turn(
        prompt,
        verbose=verbose,
        previous_messages=previous_messages,
        instructions=instructions,
        tools=tools,
    )


class GeminiClient(Client):
    """Native Gemini SDK client (``client.models.generate_content_stream``).

    The conversation history is owned **client-side**: ``previous_messages``
    is mutated in place (user/assistant turns are appended), exactly like
    Completions mode.  The Gemini ``generateContent`` API is stateless, so the
    full history is re-sent (converted to Gemini ``contents``) on every
    round.  Every hook forwards to this module's globals so test
    monkeypatches keep working.
    """

    api_type = "Gemini"
    backend_default = "https://generativelanguage.googleapis.com"

    def _create_sdk_client(self, base_url, api_key):
        return _create_client(base_url, api_key)

    def _create_tool_executor(self, mcp_manager):
        return ToolExecutor(mcp_manager)

    def _resolve_tools(self, tools, mcp_tools):
        return _resolve_tools(tools, mcp_tools)

    def _resolve_model_settings(self, provider, model):
        # All resolved at build time into the APIConfig (issue #70): Gemini
        # 3.x models reason by default, and the thinking flag itself is not
        # sent on the native API (thinking depth is controlled through
        # reasoning_effort -> thinking_level instead); the token limits and
        # reasoning level come straight from the resolved APIConfig.
        return (
            self.api_config.thinking,
            self.api_config.max_output_tokens,
            self.api_config.max_input_tokens,
            self.api_config.reasoning_effort,
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
        # Build the conversation. The Gemini API takes the system prompt as a
        # top-level `system_instruction` (not a "system"-role content), so
        # system-role messages are extracted from the history and folded into
        # it; the in-place history list keeps them so the shell's
        # messages_history stays intact.
        return _init_state(instructions, previous_messages, prompt)

    def _build_call_kwargs(
        self,
        model,
        state,
        max_output_tokens,
        reasoning_effort,
        preserve_thinking,
        thinking,
    ):
        # The effective model's built-in (native) tools are resolved for the
        # Gemini API type here (get_default_tools_from_provider with
        # api_type="Gemini"); the google provider declares none, so no
        # built-in tools are sent.  Function tools are NOT resolved here:
        # they are attached to config.tools by gemini_stream._stream_response
        # (mirroring the Completions / Anthropic / DashScope clients), which
        # receives the resolved tools_schemas from the shared turn pipeline.
        from janito.providers.registry import get_provider

        found = get_provider(self.api_config.provider)
        tools = found.tools(model, api_type="Gemini") if found is not None else None
        return _build_call_kwargs(
            model,
            state["messages"],
            max_output_tokens,
            state["system"],
            reasoning_effort,
            tools,
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
                thought_parts,
            ) = self._invoke_stream_runner(
                _stream_response, client, call_kwargs, tools_schemas
            )
        except Exception as e:
            # The google-genai SDK raises its own exception types (APIError);
            # classify the failure explicitly (auth / not-found / unknown) so
            # the observer picks the right explainer (the exception is always
            # re-raised).
            self.observer.on_error(
                e,
                provider=self.api_config.provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                error_kind=_classify_error(e),
            )
            raise
        # The model's thought blocks (text + signature) must be resent
        # verbatim on the next round; carry them through the conversation
        # state for the tool-call / finalize hooks.
        state["thought_parts"] = thought_parts
        return full_content, reasoning_content, tool_calls, usage_info, raw_attrs

    def _handle_tool_calls(
        self, tool_calls, full_content, reasoning_content, state, tool_executor
    ):
        # Record the assistant's message with its content blocks (text +
        # function_call parts) in the client-side history, then execute every
        # call and send the results back as function_response parts before
        # looping to get the final answer.
        _handle_tool_parts(
            tool_calls,
            full_content,
            reasoning_content,
            state.get("thought_parts") or [],
            state["messages"],
            tool_executor,
        )
        return state

    def _finalize(
        self,
        full_content,
        reasoning_content,
        state,
    ):
        # No more tool calls, return the final response.
        return _finalize_response(
            full_content,
            reasoning_content,
            state.get("thought_parts") or [],
            state["messages"],
        )


__all__ = [
    "run_turn",
]
