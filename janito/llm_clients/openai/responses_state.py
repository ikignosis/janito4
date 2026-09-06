"""
Conversation-state helpers for the Responses API client.

The Responses API keeps the conversation server-side for most providers
(``stateless_mode`` False) and chains turns with
``previous_response_id``; stateless providers (e.g. DeepSeek) cannot resolve
a previous response id, so the client tracks the full conversation as
Responses input items and re-sends them on every request.  These helpers
build that per-round state and the call parameters; they were extracted from
:mod:`janito.llm_clients.openai.conversations_api`.
"""

from typing import Any

from janito.providers.payloads import apply_thinking_to_extra_body
from janito.providers.registry import get_provider

from .responses_items import message_item


def stateless_mode(provider: str, model: str | None) -> bool:
    """Whether the Responses API keeps the conversation on the server.

    Resolved for the effective ``model``: a per-provider/model config
    override wins over the built-in default (``True`` for server-side
    providers such as OpenAI; ``False`` for stateless endpoints such as
    DeepSeek's ``/responses``).  The single resolution point for this
    capability -- shared by the conversation-state setup below and the CLI
    banner's ``(server-side / client-side)`` annotation.
    """
    from janito.config_loaders import load_stateless_mode_from_config

    override = load_stateless_mode_from_config(provider, model)
    if override is not None:
        return override
    found = get_provider(provider)
    return bool(found.model_config(model).get("stateless_mode", False)) if found is not None else False


def _init_conversation_state(
    provider: str,
    model: str | None,
    previous_response_id: str | None,
    previous_items: list[dict[str, Any]] | None,
    instructions: str | None,
    prompt: str,
) -> tuple[bool, str | None, list[dict[str, Any]] | None, str | list[dict[str, Any]], list[dict[str, Any]] | None,]:
    """Set up the server-side or stateless conversation state.

    Returns ``(stateless_mode, response_id, conversation_items,
    input_items, pending_items)``.  ``pending_items`` only applies to
    server-side conversations: the user messages that are not yet part of a
    completed response in the caller's chain (e.g. an Enter-cancelled prompt
    that must survive the cancel).  The caller keeps them across cancelled
    turns and re-sends them (chained from the last completed response id)
    until a turn completes; ``None`` for stateless conversations.

    The ``stateless_mode`` flag is resolved for the effective ``model``
    (a per-provider/model config override wins over the built-in default).
    """
    stateless_mode_flag = stateless_mode(provider, model)
    if not stateless_mode_flag:
        response_id = previous_response_id
        conversation_items: list[dict[str, Any]] | None = None
        # The first round sends the raw prompt; tool-call rounds send the
        # function_call_output items chained to the previous response.
        input_items: str | list[dict[str, Any]] = prompt
        # Pending user messages (e.g. an Enter-cancelled prompt) that must be
        # re-sent because they are not yet in a completed response the caller
        # chains from.  They are sent as input items chained after the last
        # completed response (previous_response_id), followed by the new
        # prompt; on a cancel they are handed back so the next turn re-sends
        # them (the aborted server response itself is discarded by the
        # provider and cannot be chained from).
        pending_items: list[dict[str, Any]] | None = []
        if previous_items:
            pending_items.extend(dict(item) for item in previous_items)
        pending_items.append(message_item("user", prompt))
        if previous_items:
            # Pending messages exist: send them (plus the new prompt) as
            # explicit input items chained from the last completed response.
            input_items = pending_items
    else:
        # Stateless: never chain with previous_response_id; each request
        # re-sends the entire conversation as input items.
        response_id = None
        conversation_items = list(previous_items or [])
        # Fold the system instructions into the history on the first turn so
        # the stateless server receives the full context on every request.
        if not conversation_items and instructions:
            conversation_items.append(message_item("system", instructions))
        conversation_items.append(message_item("user", prompt))
        input_items = conversation_items
        pending_items = None
    return (
        stateless_mode_flag,
        response_id,
        conversation_items,
        input_items,
        pending_items,
    )


def _responses_include(provider: str | None, model: str) -> list[str] | None:
    """Return the model's declared Responses ``include`` values, if any."""
    found = get_provider(provider) if provider else None
    include = found.model_config(model).get("responses_include") if found is not None else None
    if isinstance(include, (list, tuple)) and include:
        return [str(entry) for entry in include]
    return None


def _reasoning_param(model: str, reasoning_effort: str | None, provider: str | None) -> dict[str, Any] | None:
    """Return the Responses ``reasoning`` param, or ``None`` to omit it.

    Models declaring ``thinking_summary`` (e.g. Meta's Muse Spark) request
    ``reasoning.summary="auto"`` so the private chain of thought is
    returned as summary text (``response.reasoning_summary_text`` deltas,
    surfaced via ``on_reasoning``).  Responses-only: Chat Completions has
    no summary.
    """
    found = get_provider(provider) if provider else None
    summary = bool(found.model_config(model).get("thinking_summary", False)) if found is not None else False
    if not reasoning_effort and not summary:
        return None
    reasoning: dict[str, Any] = {}
    if reasoning_effort:
        reasoning["effort"] = reasoning_effort
    if summary:
        reasoning["summary"] = "auto"
    return reasoning


def _build_call_kwargs(
    model: str,
    input_items: str | list[dict[str, Any]],
    max_output_tokens: int | None,
    reasoning_effort: str | None,
    preserve_thinking: Any,
    thinking,
    response_id: str | None,
    stateless_mode: bool,
    instructions: str | None,
    builtin_tools=None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Build the Responses API call parameters for one round.

    ``builtin_tools`` is the effective model's built-in (native) tools list
    from the provider config (e.g. Alibaba/Qwen's ``[{"type":
    "code_interpreter"}, ...]`` for the Responses API).  Each entry is a
    model capability, already in the Responses ``tools`` format; it is
    carried in ``call_kwargs`` under the reserved ``_builtin_tools`` key and
    merged into the final ``tools`` array by ``_stream_response`` (it must
    not go through the function-schema conversion).  ``None`` sends nothing.
    ``provider`` enables the Gemini-flavor guard in
    :func:`apply_thinking_to_extra_body` (Gemini-flavored providers do not
    accept ``enable_thinking``).

    Stateless providers (``stateless_mode`` True) also send
    ``store: False`` (the server keeps no copy of the conversation) and the
    model's declared ``responses_include`` values (e.g. Meta's
    ``reasoning.encrypted_content``, so the reasoning output items returned
    in ``output`` carry the encrypted chain of thought that stateless
    replay needs).
    """
    call_kwargs: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "temperature": 1.0,
    }

    if stateless_mode:
        # Stateless providers: the full history is re-sent in ``input`` and
        # the server must keep no copy of the conversation (Meta's docs pair
        # the encrypted reasoning replay with store:false).
        call_kwargs["store"] = False
        # Request the model's declared optional output fields (e.g. Meta's
        # "reasoning.encrypted_content") so the reasoning output items carry
        # the encrypted chain of thought needed for stateless replay.
        include = _responses_include(provider, model)
        if include:
            call_kwargs["include"] = include

    # Add max_output_tokens if max output tokens is set in config
    if max_output_tokens is not None:
        call_kwargs["max_output_tokens"] = max_output_tokens

    reasoning = _reasoning_param(model, reasoning_effort, provider)
    if reasoning is not None:
        call_kwargs["reasoning"] = reasoning

    # Pass preserve_thinking in extra_body if defined in config
    if preserve_thinking is not None:
        call_kwargs.setdefault("extra_body", {})["preserve_thinking"] = preserve_thinking

    # Pass the thinking mode in extra_body: enable_thinking for flag-style
    # defaults, or the raw dict for providers with a structured thinking
    # parameter (e.g. MiniMax-M3's {"type": "adaptive"}).  Gemini-flavored
    # providers (google) skip enable_thinking -- the field does not exist on
    # their OpenAI-compatibility API.
    apply_thinking_to_extra_body(call_kwargs, thinking, provider=provider)

    # Stream the response. Token usage arrives on the final
    # response.completed event by default (part of the Response object);
    # "usage" is no longer a valid value for include.
    call_kwargs["stream"] = True

    # Chain to the previous server-side response when continuing a
    # server-side conversation (multi-turn or tool-call round). Stateless
    # providers never chain: the full history is already in ``input``.
    if response_id is not None:
        call_kwargs["previous_response_id"] = response_id

    # System instructions: stateless providers fold them into the client-side
    # items history (sent with every request), so no separate parameter is
    # needed.  Server-side providers always send them -- some (e.g. Meta)
    # do not persist ``instructions`` across ``previous_response_id`` turns
    # and require it on every request; re-sending is also correct for those
    # that do fold it into the stored conversation (OpenAI).
    if not stateless_mode and instructions:
        call_kwargs["instructions"] = instructions

    # The effective model's built-in (native) tools (e.g. Alibaba/Qwen's
    # code_interpreter / web_search / web_extractor) are model
    # capabilities enabled through the Responses ``tools`` array.  They
    # are carried in call_kwargs under the reserved "_builtin_tools" key
    # and merged with the converted function-tool schemas by
    # ``_stream_response``, which pops the key before calling the API.
    # Like the web agent, they are enabled whenever the model declares
    # them for this API type -- even with no_tools / an empty
    # function-tools list.
    if builtin_tools:
        call_kwargs["_builtin_tools"] = list(builtin_tools)
    return call_kwargs
