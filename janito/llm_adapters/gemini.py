"""Shared native Gemini SDK adapter: kwargs, conversion, accumulation.

The native Gemini ``generateContent`` turn pipeline used to be CLI-only; the
pure parts -- call-kwargs building, history conversion and stream
accumulation -- live here so both agent loops share them.  The web shim
(``janito.web.backend.agent.gemini``) keeps the async glue
(:func:`create_client` and :func:`stream_turn_events`), which consumes the
**sync** ``google-genai`` stream chunk-by-chunk through ``asyncio.to_thread``.

The ``google-genai`` package is **optional** (see
``janito.providers.REQUIRES_BY_API_TYPE``); importing it happens lazily
inside :func:`create_client` (kept in the web shim), so importing this
module never requires it.

The CLI's :mod:`janito.llm_clients.gemini` modules depend on this shared
adapter layer one-way (issue #90): ``gemini_helpers`` re-exports the
wire-format conversions and ``gemini_stream`` re-exports
:class:`GeminiStreamConsumer` for its sync stream driver.

**Conversation model.** The ``generateContent`` API is stateless, so every
round re-sends the full history.  The session stores the conversation in the
portable OpenAI chat format; :func:`_messages_to_contents` converts it on
the fly: system messages are folded into the top-level
``system_instruction``, ``assistant`` ``tool_calls`` become ``function_call``
parts (with the model's ``thought_signature`` re-attached), and ``tool``
messages become ``function_response`` parts in a ``user`` content.  Gemini
3.x thought blocks are kept verbatim (``thought_parts``) so stateless
multi-turn requests resend them, preserving reasoning continuity.
"""

import json
import logging
from types import SimpleNamespace
from typing import Any

from .sdk import _extract_raw_attrs

logger = logging.getLogger(__name__)


def _convert_tools_to_gemini_format(
    tools_schemas: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Convert OpenAI-format tool schemas to Gemini ``function_declarations``.

    The shared schema builders emit the Chat Completions shape (``type``
    ``"function"`` with the declaration nested under ``"function"``).  The
    Gemini API expects ``tools=[{"function_declarations": [{name,
    description, parameters}]}]``; ``parameters`` is passed through as-is
    (the Gemini API accepts the OpenAPI subset natively).

    Returns ``None`` when there are no tools (the caller then omits the
    ``tools`` key from the request).
    """
    if not tools_schemas:
        return None
    declarations = []
    for schema in tools_schemas:
        function = schema.get("function") if isinstance(schema, dict) else None
        if function is None:
            continue
        declaration: dict[str, Any] = {}
        if function.get("name"):
            declaration["name"] = function["name"]
        if function.get("description"):
            declaration["description"] = function["description"]
        parameters = function.get("parameters")
        if parameters:
            declaration["parameters"] = parameters
        if declaration:
            declarations.append(declaration)
    if not declarations:
        return None
    return [{"function_declarations": declarations}]


def _resolve_system_instruction(
    instructions: str | None, messages: list[dict[str, Any]]
) -> str | None:
    """Resolve the Gemini ``system_instruction`` from instructions/history."""
    system = instructions
    system_messages = [
        m for m in messages if m.get("role") == "system" and m.get("content")
    ]
    if system is None and system_messages:
        system = "\n\n".join(str(m.get("content")) for m in system_messages)
    return system


def _assistant_parts(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the Gemini ``model`` parts for an assistant message.

    The model's thought blocks (text + signature) are resent verbatim, then
    the final text and any ``function_call`` parts (each carrying the model's
    ``thought_signature`` when one was attached).
    """
    parts: list[dict[str, Any]] = []
    for thought in msg.get("thought_parts") or []:
        thought_part: dict[str, Any] = {"text": thought.get("text") or ""}
        if thought.get("thought_signature"):
            thought_part["thought_signature"] = thought["thought_signature"]
        parts.append(thought_part)
    content = msg.get("content")
    if content:
        parts.append({"text": content})
    for tc in msg.get("tool_calls") or []:
        function = tc.get("function") or {}
        call_part: dict[str, Any] = {
            "function_call": {
                "id": tc.get("id") or "",
                "name": function.get("name") or "",
                "args": _parse_arguments(function.get("arguments")),
            }
        }
        if tc.get("thought_signature"):
            call_part["thought_signature"] = tc["thought_signature"]
        parts.append(call_part)
    return parts


def _tool_result_part(msg: dict[str, Any]) -> dict[str, Any]:
    """Build the Gemini ``user`` part carrying a ``function_response``.

    The Gemini ``function_response.response`` field must be a JSON object
    (``dict``): the ``google-genai`` SDK rejects plain-text tool results
    (markdown, logs, ...) and non-object JSON values (lists, scalars) with a
    client-side pydantic error, so they are wrapped under a ``result`` key
    (the documented convention for free-form tool output).
    """
    response = msg.get("content")
    try:
        response = json.loads(response) if isinstance(response, str) else response
    except (ValueError, TypeError):
        pass
    if response is not None and not isinstance(response, dict):
        response = {"result": response}
    response_part: dict[str, Any] = {
        "function_response": {
            "id": msg.get("tool_call_id") or "",
            "name": msg.get("name") or "",
            "response": response,
        }
    }
    if msg.get("thought_signature"):
        response_part["thought_signature"] = msg["thought_signature"]
    return response_part


def _messages_to_contents(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert OpenAI-format chat messages to Gemini ``contents`` (dict shape).

    The Gemini API takes ``contents`` as a list of ``{"role": "user" |
    "model", "parts": [...]}`` parts.  ``system``-role messages are skipped
    (they were folded into the top-level ``system_instruction`` by
    :func:`_resolve_system_instruction`); ``tool``-role messages become
    ``user`` parts carrying a ``function_response``.

    Gemini 3.x attaches thought signatures to parts; stateless multi-turn
    requests must resend the model's thought blocks verbatim (see the
    generateContent thinking guide).  The assistant messages recorded by
    the client keep the raw thought parts (``thought_parts``) and per-call
    signatures (``tool_calls[*].thought_signature``), which are re-attached
    here so reasoning continuity is preserved across turns.
    """
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        if role == "assistant":
            parts = _assistant_parts(msg)
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            # Tool results go back as user parts with a function_response.
            contents.append({"role": "user", "parts": [_tool_result_part(msg)]})
        else:
            # Plain user turn.  A leading system-role message was already
            # folded into system_instruction, so this is the user's text.
            content = msg.get("content")
            contents.append({"role": "user", "parts": [{"text": str(content)}]})
    return contents


def _parse_arguments(arguments: Any) -> dict[str, Any]:
    """Parse a tool-call arguments JSON string into a dict (best effort)."""
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments) if arguments else {}
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _build_usage_info(usage_metadata: Any) -> SimpleNamespace | None:
    """Map Gemini ``usage_metadata`` onto the shared token-usage shape.

    The shared usage display / normalization reads ``total_tokens`` /
    ``input_tokens`` / ``output_tokens`` attributes; Gemini reports
    ``total_token_count`` / ``prompt_token_count`` / ``response_token_count``
    (plus ``thoughts_token_count``), so they are mapped onto a
    ``SimpleNamespace`` exactly like the Anthropic / DashScope native
    clients do.
    """
    if usage_metadata is None:
        return None
    return SimpleNamespace(
        total_tokens=getattr(usage_metadata, "total_token_count", None),
        input_tokens=getattr(usage_metadata, "prompt_token_count", None),
        output_tokens=getattr(usage_metadata, "response_token_count", None),
    )


def _build_call_kwargs(
    model: str,
    messages: list[dict[str, Any]],
    max_output_tokens: int,
    system: str | None,
    reasoning_effort: str | None,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build the native Gemini ``generate_content`` call parameters.

    The OpenAI-format history is converted to Gemini ``contents`` and the
    tool schemas to ``function_declarations``; ``max_output_tokens`` and the
    resolved reasoning level (sent as ``thinking_config.thinking_level``,
    which the Gemini API maps to the model's thinking depth) ride in the
    ``config`` dict.  ``tools`` is the effective model's built-in (native)
    tools list from the provider config; built-in tools are not function
    tools, so they are not sent here (the native Gemini API enables Google
    Search / code execution through their own ``Tool`` entries, which are
    not wired for this API type yet).
    """
    config: dict[str, Any] = {"max_output_tokens": max_output_tokens}
    if system:
        config["system_instruction"] = system
    if reasoning_effort:
        config["thinking_config"] = {"thinking_level": reasoning_effort}
    function_tools = _convert_tools_to_gemini_format(tools)
    if function_tools:
        config["tools"] = function_tools
    return {
        "model": model,
        "contents": _messages_to_contents(messages),
        "config": config,
    }


def build_call_kwargs(
    model: str,
    messages: list[dict],
    tools_schemas: list[dict] | None,
    config,
    max_output_tokens: int | None,
    preserve_thinking,
    reasoning_effort: str | None,
) -> dict:
    """Build the ``generate_content_stream`` kwargs for one turn.

    Mirrors ``janito.llm_clients.gemini.gemini_api._build_call_kwargs``: the OpenAI-format
    history is converted to Gemini ``contents`` (with the leading
    ``system``-role message folded into ``system_instruction``) and the
    function-tool schemas to ``function_declarations``.  The resolved
    reasoning level rides in ``thinking_config.thinking_level``, which the
    Gemini API maps to the model's thinking depth.  ``preserve_thinking``
    is accepted for signature parity but is not used by the native SDK.
    """
    if max_output_tokens is None:
        max_output_tokens = 100000  # default to 100k tokens if not set in config
    system = _resolve_system_instruction(None, messages)
    return _build_call_kwargs(
        model,
        messages,
        max_output_tokens,
        system,
        reasoning_effort,
        tools_schemas,
    )


class GeminiStreamConsumer:
    """Assemble Gemini ``generate_content_stream`` chunks.

    The consumer owns the accumulated content / reasoning text, the raw
    thought blocks (text + signature, resent verbatim on the next turn) and
    the per-call tool-call map keyed by function-call id.  The CLI stream
    driver (:mod:`janito.llm_clients.gemini.gemini_stream`) drives it with
    :meth:`consume`; the web loop subclasses it
    (:class:`GeminiTurnAccumulator`).
    """

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.thought_parts: list[dict[str, Any]] = []
        self.tool_calls: dict[str, dict[str, Any]] = {}
        self.usage: Any = None
        self.raw_attrs: dict[str, Any] = {}
        self.done: bool = False

    # ------------------------------------------------------------------
    # Chunk folding
    # ------------------------------------------------------------------

    def handle(self, chunk) -> tuple[str | None, str | None]:
        """Process one chunk; returns ``(reasoning_delta, content_delta)``.

        Also captures the chunk's raw top-level metadata (``model_version``,
        ``response_id``, ``create_time``, ...) and the terminal
        ``finish_reason`` for the verbose response dump.
        """
        usage = getattr(chunk, "usage_metadata", None)
        if usage is not None:
            self.usage = usage

        self.raw_attrs.update(
            _extract_raw_attrs(chunk, skip=("candidates", "usage_metadata"))
        )

        reasoning_delta: str | None = None
        content_delta: str | None = None
        for candidate in getattr(chunk, "candidates", None) or []:
            parts = getattr(getattr(candidate, "content", None), "parts", None) or []
            for part in parts:
                delta = self._fold_part(part)
                if delta:
                    text, kind = delta
                    if kind == "reasoning":
                        reasoning_delta = text
                    elif kind == "content":
                        content_delta = text
            finish = getattr(candidate, "finish_reason", None)
            if finish is not None:
                finish_name = getattr(finish, "name", None) or str(finish)
                self.raw_attrs["finish_reason"] = finish_name
                if finish_name != "FINISH_REASON_UNSPECIFIED":
                    self.done = True
        return reasoning_delta, content_delta

    def _fold_part(self, part) -> tuple[Any, str] | None:
        """Fold one ``Part``; returns ``(text, "reasoning" | "content")`` or None."""
        # Thought blocks: reasoning text plus the signature that must be
        # resent verbatim on the next turn.
        if getattr(part, "thought", False):
            text = getattr(part, "text", None) or ""
            if text:
                self.reasoning.append(text)
                self.thought_parts.append(
                    {
                        "text": text,
                        "thought_signature": getattr(part, "thought_signature", None),
                    }
                )
                return text, "reasoning"
            return None

        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            self._fold_function_call(
                function_call, getattr(part, "thought_signature", None)
            )
            return None

        text = getattr(part, "text", None)
        if text:
            self.content.append(text)
            return text, "content"
        return None

    def _fold_function_call(self, function_call, thought_signature: str | None) -> None:
        """Merge one ``FunctionCall`` part into the per-id tool-call map.

        Gemini 3 generates a unique ``id`` for every function call; the
        call's ``args`` arrive as a complete dict (streamed argument
        fragments are a Vertex-Enterprise-only feature).  The signature the
        model attached to the call part is kept so the next round can resend
        it (dropping it makes Gemini 3.x reject the follow-up request).
        """
        call_id = getattr(function_call, "id", None) or getattr(
            function_call, "name", None
        )
        key = call_id or f"call_{len(self.tool_calls)}"
        entry = self.tool_calls.setdefault(
            key,
            {
                "id": call_id or "",
                "name": getattr(function_call, "name", None) or "",
                "args": {},
                "arguments": "",
                "thought_signature": thought_signature or "",
            },
        )
        if getattr(function_call, "id", None):
            entry["id"] = function_call.id
        name = getattr(function_call, "name", None)
        if name:
            entry["name"] = name
        args = getattr(function_call, "args", None)
        if isinstance(args, dict):
            entry["args"].update(args)
        elif isinstance(args, str):
            # Rare fallback: fragment-style arguments accumulate as text.
            entry["arguments"] += args

    # ------------------------------------------------------------------
    # End-of-turn assembly
    # ------------------------------------------------------------------

    def full_content(self) -> str:
        return "".join(self.content)

    def reasoning_content(self) -> str | None:
        return "".join(self.reasoning) if self.reasoning else None

    def tool_calls_list(self) -> list[dict[str, Any]]:
        """Assembled tool calls in the CLI wire format.

        Each entry carries ``id`` / ``name`` / ``arguments`` (JSON string)
        plus the model's ``thought_signature`` when one was attached, so the
        caller can echo it back on the next round.
        """
        calls = []
        for entry in self.tool_calls.values():
            call: dict[str, Any] = {
                "id": entry["id"],
                "name": entry["name"],
                "arguments": entry["arguments"]
                or (json.dumps(entry["args"]) if entry["args"] else "{}"),
            }
            if entry.get("thought_signature"):
                call["thought_signature"] = entry["thought_signature"]
            calls.append(call)
        return calls

    def usage_info(self):
        """Map the last ``usage_metadata`` onto the shared usage shape."""
        return _build_usage_info(self.usage)

    # ------------------------------------------------------------------
    # Stream driving
    # ------------------------------------------------------------------

    def consume(self, stream, cancel_event=None):
        """Consume a Gemini stream and assemble the response parts.

        Returns ``(full_content, reasoning_content, tool_calls_list,
        usage_info, raw_attrs, thought_parts)`` where ``thought_parts`` holds
        the raw thought blocks (text + signature) to resend verbatim on the
        next turn.

        When ``cancel_event`` is set (user pressed Enter while waiting), the
        stream is abandoned as soon as the next chunk arrives.

        Raises:
            RuntimeError: If the stream yields no chunks at all (never an
                empty answer).
        """
        consumed = False
        for chunk in stream:
            consumed = True
            if cancel_event is not None and cancel_event.is_set():
                break
            self.handle(chunk)
        if not consumed:
            raise RuntimeError("Gemini API returned no stream chunks")
        return (
            self.full_content(),
            self.reasoning_content(),
            self.tool_calls_list(),
            self.usage_info(),
            self.raw_attrs,
            self.thought_parts,
        )


class GeminiTurnAccumulator(GeminiStreamConsumer):
    """Fold Gemini ``generate_content_stream`` chunks (web agent loop).

    Reuses :class:`GeminiStreamConsumer` for the per-chunk folding (thought /
    text / ``function_call`` parts, usage metadata, ``done`` flag) and adds
    the web-loop accessors: :meth:`tool_calls_list` returns the OpenAI wire
    format that ``run_tool_turn`` expects (keeping the per-call
    ``thought_signature`` so the assistant message can echo it back); raw
    usage is exposed via :meth:`usage_object` (the web loop builds its own
    ``UsageEvent`` on top of it).
    """

    def tool_calls_list(self) -> list[dict]:
        """Assembled tool calls in OpenAI wire format (for ``run_tool_turn``)."""
        calls = []
        for entry in self.tool_calls.values():
            call = {
                "id": entry["id"],
                "type": "function",
                "function": {
                    "name": entry["name"],
                    "arguments": entry["arguments"]
                    or (json.dumps(entry["args"]) if entry["args"] else "{}"),
                },
            }
            if entry.get("thought_signature"):
                call["thought_signature"] = entry["thought_signature"]
            calls.append(call)
        return calls

    def usage_object(self):
        """The raw usage object of this round, or ``None`` when unreported.

        Uniform accessor used by the web loop to fold each round's usage into
        the turn-level cumulative totals (:class:`TurnInfo`).
        """
        return self.usage_info()


# Uniform runner interface (used by loop.py): the accumulator class is
# exposed as ``accumulator`` so every API-type runner has the same shape.
accumulator = GeminiTurnAccumulator


__all__ = [
    "GeminiStreamConsumer",
    "GeminiTurnAccumulator",
    "accumulator",
    "build_call_kwargs",
    "_build_call_kwargs",
    "_build_usage_info",
    "_convert_tools_to_gemini_format",
    "_messages_to_contents",
    "_parse_arguments",
    "_resolve_system_instruction",
]
