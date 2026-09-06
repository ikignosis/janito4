"""
OpenAI client module for sending prompts to OpenAI-compatible endpoints using
the Responses API (``client.responses.create``) with server-side conversation
state.

This module mirrors :mod:`janito.llm_clients.openai.completions_api` (same config
resolution, tool loading, MCP support, progress spinner, reasoning panel,
used-files report and token-usage summary), but targets the Responses API
instead of the Chat Completions API.

The important difference is **who owns the conversation history**. The
Completions implementation stores and updates a ``messages`` list on the
client side. This module delegates to the server: the Responses API keeps the
conversation server-side and turns are chained with ``previous_response_id``::

    result = run_turn("First question")
    result = run_turn("Follow-up", previous_response_id=result.response_id)

Tool calls work the same way: the model's ``function_call`` output items are
executed and the results are sent back as ``function_call_output`` input items
chained to the response that produced the calls, repeating until the model
emits a final text answer. Only the final ``response_id`` needs to be kept by
the caller.

**Stateless endpoints.** Some providers' ``/responses`` endpoint is stateless
(``stateless_mode`` is ``True`` in the provider's model config, e.g.
DeepSeek and Meta's Muse Spark):
it cannot resolve a ``previous_response_id`` and rejects tool outputs that
reference it. For those providers the client falls back to the Completions
model of ownership: the full conversation is tracked as Responses input items
(``ConversationResult.input_items``) and re-sent on every request via
``previous_items``, with the system instructions folded into the first turn.
The stateless requests also carry ``store: False`` (the server keeps no copy
of the conversation) and the model's declared ``responses_include`` values
(e.g. Meta's ``reasoning.encrypted_content``); the finished ``reasoning``
output items returned in the stream (the encrypted chain of thought) are
replayed verbatim in the next round's ``input`` so the cross-turn reasoning
survives.

The Responses API stream handling lives in
:mod:`janito.llm_clients.openai.responses_stream`; the shared LLM-side client
helpers (incl. the ``RequestCancelled`` control-flow exception) live in
:mod:`janito.llm_clients.client_support` and the injected UI-side pieces
(stream runner + turn observer) in :mod:`janito.ui`.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import AuthenticationError, NotFoundError, OpenAI

# Typed provider accessor (get_provider(name) -> Provider): resolves the
# effective model's built-in (native) tools for the Responses API type.
from janito.providers.registry import get_provider

# Import the tool executor (routes tool calls to the MCP manager or the
# built-in registry and tracks usage/used-files/changes around each call)
from janito.tooling.executor import ToolExecutor

# Resolved, immutable per-session configuration (issue #70): the turn
# pipeline consumes it instead of re-reading the config/auth stores.
from ..api_config import APIConfig

# Shared agent-loop pipeline (see Client.run_turn) implemented by
# ResponsesClient; ``UIConfig`` is the structural UI-behaviour protocol the
# pipeline depends on (the concrete frozen bundle lives in
# ``janito.ui.config``, issue #90).
from ..base_client import Client, UIConfig

# Shared client helpers: the RequestCancelled exception raised by the
# injected per-round stream runner (``janito.ui.stream_runner``); the
# exception itself lives here in ``client_support``.
from ..client_support import RequestCancelled
from .responses_helpers import (
    _finalize_conversation,
    _handle_tool_calls,
    _pending_items_for_cancel,
    _resolve_tools,
    _validate_stream_result,
)
from .responses_items import ConversationResult, message_item
from .responses_state import _build_call_kwargs, _init_conversation_state
from .responses_stream import _stream_response

# Import general configuration handling

# Import provider configuration for built-in defaults


# Import tools

# Import used-files tracking (best-effort, never fails)


# Configure logger for this module
logger = logging.getLogger(__name__)


def _emit_web_search_events(observer, calls, citations) -> None:
    """Fan out search-grounding events (issue #131)."""
    for _call in calls or []:
        observer.on_web_search_call()
    if calls or citations:
        observer.on_web_search_done(list(citations or []))


def run_turn(
    api_config: APIConfig,
    prompt: str,
    *,
    ui_config: UIConfig | None = None,
    verbose: bool = False,
    previous_response_id: str | None = None,
    previous_items: list[dict[str, Any]] | None = None,
    instructions: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> ConversationResult:
    """Send a prompt to the Responses API and return the final answer.

    Thin config-driven wrapper (issue #70): all resolved session config
    (provider, model, endpoint, api_key, token limits, reasoning level,
    thinking, preserve_thinking, use_mcp)
    arrives in ``api_config`` -- built once per session by ``build_api_config`` --
    and the UI-side stream runner / turn observer arrive separately in
    ``ui_config`` -- so this entry point performs no config-store /
    auth-store reads of its own.

    The conversation history lives **server-side**: the client neither
    stores nor updates a ``messages`` list. Multi-turn conversations chain
    responses with ``previous_response_id``; tool-call rounds are chained
    internally the same way, so only the final ``response_id`` matters to the
    caller.

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
        previous_response_id: The server-side id of the previous response to
            continue from (``None`` for a fresh conversation). Obtained from
            the ``response_id`` of the previous ``ConversationResult``. Only
            used for providers whose Responses API keeps server-side state
            (``stateless_mode`` False); ignored for stateless providers.
        previous_items: For stateless providers (``stateless_mode``
            False), the full conversation as Responses input items (obtained
            from the previous result's ``input_items``), which cannot resolve
            a ``previous_response_id`` and must re-send the entire history on
            every request. For server-side providers it may carry the pending
            user messages of an Enter-cancelled turn, which are re-sent as
            input items chained from ``previous_response_id`` (the last
            completed response). ``None`` for a fresh conversation.
        instructions: System instructions for the conversation. For server-side
            providers they are only sent on the first turn (the server folds
            them into the stored conversation); for stateless providers they
            are folded into the client-side history on the first turn so every
            request carries the full context.
        tools: Optional list of tool schemas to pass. If None, uses all
            available tools. If an empty list, no tools are passed.

    Returns:
        ConversationResult: the final assistant text plus, depending on the
        provider's conversation model, the server-side response id (to chain
        the next turn with ``previous_response_id``) or the full client-side
        input items (to re-send with ``previous_items``).

    Note:
        The end-of-turn report (used files + token-usage summary) is
        delivered by the client itself to the injected observer's
        ``on_turn_complete``; there is no caller-supplied out-param (issue #82).

    Note:
        Thinking mode is resolved into ``api_config.thinking`` at build time:
        the explicit ``--thinking`` / ``/thinking`` flag wins, otherwise the
        provider's built-in default applies (sent as
        ``extra_body={'enable_thinking': True}``).
    """
    logger.info("Sending prompt to Responses API")
    return ResponsesClient(api_config, ui_config).run_turn(
        prompt,
        verbose=verbose,
        previous_response_id=previous_response_id,
        previous_items=previous_items,
        instructions=instructions,
        tools=tools,
    )


class ResponsesClient(Client):
    """Responses API client (``client.responses.create``).

    The conversation state model depends on the provider: server-side
    endpoints (e.g. OpenAI) chain turns with ``previous_response_id`` (the
    history lives on the server); stateless endpoints (e.g. DeepSeek) track
    the full conversation as Responses input items and re-send them on every
    request.  Every hook forwards to this module's globals so test
    monkeypatches keep working.
    """

    api_type = "Responses"

    def _create_sdk_client(self, base_url, api_key):
        # base_url can be None for standard OpenAI
        return OpenAI(api_key=api_key, base_url=base_url)

    def _create_tool_executor(self, mcp_manager):
        return ToolExecutor(mcp_manager)

    def _resolve_tools(self, tools, mcp_tools):
        schemas = _resolve_tools(tools, mcp_tools)
        from janito.llm_adapters.responses import (
            convert_tools_for_tool_search,
            model_uses_tool_search,
        )
        from janito.tooling.tools_registry import get_tool_namespace

        if model_uses_tool_search(self.api_config.provider, self.api_config.model):
            namespaced_input = []
            for schema in schemas:
                name = schema.get("name", "")
                namespace = get_tool_namespace(name) if name else "default"
                namespaced_input.append({**schema, "namespace": namespace})
            return convert_tools_for_tool_search(namespaced_input)
        return schemas

    def _resolve_model_settings(self, provider, model):
        # All resolved at build time into the APIConfig (issue #70): thinking
        # (the --thinking / /thinking flag, or the model's built-in default)
        # and the token limits / reasoning level.  The config store /
        # provider registry is never read here.
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
        # Conversation-state model depends on the provider: some Responses
        # endpoints (e.g. OpenAI) keep the conversation server-side and chain
        # turns with previous_response_id; others (e.g. DeepSeek's /responses,
        # which is stateless) cannot resolve a previous response id, so the
        # client tracks the full conversation as Responses input items and
        # re-sends them on every request (like Chat Completions).
        (
            stateless_mode,
            response_id,
            conversation_items,
            input_items,
            pending_items,
        ) = _init_conversation_state(
            provider,
            model,
            previous_response_id,
            previous_items,
            instructions,
            prompt,
        )
        return {
            "stateless_mode": stateless_mode,
            "response_id": response_id,
            "conversation_items": conversation_items,
            "input_items": input_items,
            "pending_items": pending_items,
            "instructions": instructions,
            "message_count": 1,
            # Display-only mirror of this completed turn (Responses input
            # items) for the shell's /history command: starts with the user
            # prompt, then the assistant text / tool-call rounds are appended
            # as the turn progresses. Server-side providers keep the real
            # conversation on the server; this copy exists purely for
            # /history rendering.
            "turn_items": [message_item("user", prompt)],
        }

    def _build_call_kwargs(
        self,
        model,
        state,
        max_output_tokens,
        reasoning_effort,
        preserve_thinking,
        thinking,
    ):
        # The effective model's built-in (native) tools (e.g. Alibaba/Qwen's
        # code_interpreter / web_search / web_extractor) are resolved for
        # the effective provider and appended to the Responses tools array
        # (see responses_state._build_call_kwargs).  They are model
        # capabilities, not function tools, so they are enabled whenever the
        # model declares them for this API type -- even with no_tools / an
        # empty function-tools list (mirroring the web agent).
        from janito.tooling.tools_registry import tools_loading_enabled

        found = get_provider(self.api_config.provider)
        builtin_tools = None
        if tools_loading_enabled() and found is not None:
            builtin_tools = found.tools(model, api_type="Responses")
        return _build_call_kwargs(
            model,
            state["input_items"],
            max_output_tokens,
            reasoning_effort,
            preserve_thinking,
            thinking,
            state["response_id"],
            state["stateless_mode"],
            state["instructions"],
            builtin_tools,
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
                stream_response_id,
                raw_attrs,
                reasoning_items,
                tool_search_calls,
                tool_search_outputs,
                web_search_calls,
                web_search_citations,
            ) = self._invoke_stream_runner(
                _stream_response, client, call_kwargs, tools_schemas
            )
            for call in tool_search_calls or []:
                self.observer.on_tool_search_call(call.get("paths", []))
            for output in tool_search_outputs or []:
                self.observer.on_tool_search_output(output.get("tool_names", []))
            _emit_web_search_events(
                self.observer, web_search_calls, web_search_citations
            )
            # Only server-side conversations chain with the returned id;
            # stateless providers never send previous_response_id.
            if not state["stateless_mode"]:
                state["response_id"] = stream_response_id
            else:
                # Stateless: record the finished ``reasoning`` output items
                # (the encrypted chain of thought, e.g. Meta's Muse Spark)
                # in the client-side history BEFORE the assistant message /
                # tool calls are appended by ``_handle_tool_calls`` -- the
                # Responses API requires a reasoning item to be followed by
                # an assistant message or a function_call, and the stream
                # emits them in that order.
                state["input_items"].extend(reasoning_items)
                if state["turn_items"] is not None:
                    state["turn_items"].extend(reasoning_items)
            # Safety net: a server-side provider that never reported a
            # response id and produced neither content nor tool calls means
            # the request failed without a proper error event. Raise a clear
            # error naming the model instead of returning an empty result.
            _validate_stream_result(
                state["stateless_mode"],
                stream_response_id,
                full_content,
                tool_calls,
                model,
            )
        except NotFoundError as e:
            self.observer.on_error(
                e,
                base_url=base_url,
                model=model,
                response_id=state["response_id"],
                error_kind="not_found",
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
        except RequestCancelled as e:
            # Enter was pressed while waiting for the API. Keep the
            # conversation state so the shell can continue without losing the
            # user's message:
            #  - server-side: the aborted request created a server-side
            #    response carrying the message, but the provider discards it
            #    when the stream is interrupted (OpenAI answers
            #    ``previous_response_id not found`` for it), so it cannot
            #    chain the next turn. The caller keeps the last *completed*
            #    response id (state["response_id"]) and re-sends the pending
            #    user messages as input items chained from it.
            #  - stateless: the server keeps nothing, so hand back the full
            #    client-side items (which already include the cancelled
            #    message) for the next turn to re-send.
            if not state["stateless_mode"]:
                e.conversation_items = _pending_items_for_cancel(state)
            else:
                e.conversation_items = state["input_items"]
            raise
        return full_content, reasoning_content, tool_calls, usage_info, raw_attrs

    def _handle_tool_calls(
        self, tool_calls, full_content, reasoning_content, state, tool_executor
    ):
        # Record the assistant's tool calls in the client-side history
        # (stateless providers) and execute every call, sending the results
        # back as function_call_output items chained to the response that
        # produced the calls (server-side) or appended to the full history
        # (stateless). Then continue the loop.
        state["input_items"] = _handle_tool_calls(
            tool_calls,
            full_content,
            state["conversation_items"],
            tool_executor,
            turn_items=state["turn_items"],
        )
        state["message_count"] += 1
        return state

    def _finalize(
        self,
        full_content,
        reasoning_content,
        state,
    ):
        # Server-side: the assistant message lives on the server and the
        # caller only needs the response id to chain the next turn. Stateless:
        # append the final assistant text to the client-side history and hand
        # the full items back so the caller can re-send them next turn.
        return _finalize_conversation(
            full_content,
            state["conversation_items"],
            state["message_count"],
            state["response_id"],
            state["stateless_mode"],
            turn_items=state["turn_items"],
        )


__all__ = [
    "ConversationResult",
    "run_turn",
]
