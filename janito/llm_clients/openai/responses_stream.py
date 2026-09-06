"""
Stream consumption for the Responses API.

These helpers are used by :mod:`janito.llm_clients.openai.conversations_api`, which
talks to ``client.responses.create`` with streaming enabled.  The Responses
API emits typed SSE events (``response.output_text.delta``,
``response.function_call_arguments.delta``, ``response.output_item.done``,
...), so each finished output item carries a stable ``call_id`` (unlike Chat
Completions, which splits tool calls across chunks indexed by position).

:class:`ResponsesStreamConsumer` is the real implementation: it holds the
assembled response parts as instance attributes (no ``state`` dict plumbing)
and drives the per-event handlers.  The module-level
``_consume_response_stream`` function is a thin delegator used by the
module's own ``_stream_response`` and by the client tests.
"""

import logging
from typing import Any

from janito.llm_adapters.sdk import _extract_raw_attrs

# Configure logger for this module
logger = logging.getLogger(__name__)


class ResponsesStreamConsumer:
    """Assemble Responses API stream events into a single response.

    The consumer owns the accumulated content / reasoning text, the tool-call
    list (with stable ``call_id`` per finished output item), the per-item
    partial-arguments buffer, the usage info, the server-side response id and
    the raw top-level response metadata.  :meth:`consume` drives the stream
    and returns the response parts; the ``handle_*`` methods apply individual
    events.
    """

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.partial_arguments: dict[str, str] = {}
        self.usage_info: Any = None
        self.response_id: str | None = None
        self.raw_attrs: dict[str, Any] = {}
        # Finished ``reasoning`` output items (Meta's Muse Spark emits these
        # carrying the encrypted chain of thought for stateless replay):
        # raw item dicts in stream order, replayed verbatim in the next
        # round's ``input`` by the stateless client.
        self.reasoning_items: list[dict[str, Any]] = []
        # Search grounding (issue #131): finished web_search_call items
        # (one per performed search) and url_citation annotations collected
        # from the completed response's message output.
        self.web_search_calls: list[dict[str, Any]] = []
        self.web_search_citations: list[dict[str, Any]] = []
        self._events_seen = 0

    # ------------------------------------------------------------------
    # Result accessors
    # ------------------------------------------------------------------

    @property
    def full_content(self) -> str:
        """The assembled assistant text."""
        return "".join(self.content)

    @property
    def reasoning_content(self) -> str | None:
        """The assembled reasoning text, or ``None`` when none was streamed."""
        return "".join(self.reasoning) if self.reasoning else None

    # ------------------------------------------------------------------
    # Stream driving
    # ------------------------------------------------------------------

    def consume(self, stream, cancel_event=None):
        """Consume a streaming Responses API response and assemble its parts.

        Returns ``(full_content, reasoning_content, tool_calls, usage_info,
        response_id, raw_attrs, reasoning_items)`` where ``tool_calls`` is a
        list of ``{"call_id", "name", "arguments"}`` dicts, ``raw_attrs``
        holds the raw top-level response metadata (id, model, created_at,
        status, ...) and ``reasoning_items`` the finished ``reasoning``
        output items (raw replayable dicts) in stream order.

        When ``cancel_event`` is set (user pressed Enter while waiting), the
        stream is abandoned as soon as the next event arrives.
        """
        for event in stream:
            self._events_seen += 1
            # Honour an Enter-to-cancel request: stop consuming as soon as the
            # next event arrives so the worker can close the connection.
            if cancel_event is not None and cancel_event.is_set():
                break
            self.handle_event(event)

        # A healthy stream always yields at least a response.created/completed
        # event; a stream with zero events means the provider failed to
        # produce a response (e.g. an error that was never surfaced). Fail
        # loudly instead of returning an empty answer. An Enter-to-cancel
        # short-circuit must not be treated as an empty stream.
        if self._events_seen == 0 and (
            cancel_event is None or not cancel_event.is_set()
        ):
            raise RuntimeError(
                "The Responses API returned no stream events (empty response)."
            )
        return (
            self.full_content,
            self.reasoning_content,
            self.tool_calls,
            self.usage_info,
            self.response_id,
            self.raw_attrs,
            self.reasoning_items,
            self.web_search_calls,
            self.web_search_citations,
        )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def handle_event(self, event) -> None:
        """Dispatch a single stream event to the matching handler."""
        event_type = event.type

        # Some OpenAI-compatible providers stream API errors as SSE events the
        # SDK cannot type (``event.type`` is ``None``) but which carry the
        # error payload as ``code``/``message`` attributes. Alibaba DashScope,
        # for example, rejects a model its /responses endpoint does not
        # support with ``code='InvalidParameter'``, ``message="Unsupported
        # model: 'qwen3.8-max'."``.  Surface the message instead of silently
        # returning an empty response.
        if event_type is None:
            _handle_untyped_error(event)
            return

        if event_type in ("response.created", "response.completed"):
            self.handle_completion_event(event)
        elif event_type == "response.failed":
            _raise_failed_error(event)
        elif event_type in (
            "response.output_text.delta",
            "response.reasoning_text.delta",
            "response.reasoning_summary_text.delta",
        ):
            self.handle_text_delta(event)
        elif event_type in (
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        ):
            self.handle_call_arguments(event)
        elif event_type == "response.output_item.done":
            self.handle_output_item(event)

    def handle_completion_event(self, event) -> None:
        """Record the response id (usage + citations on the completed event)."""
        # The response id is the handle used to chain the next turn; it is
        # known as soon as the server creates (or completes) the response.
        self.response_id = event.response.id
        # Keep the raw top-level response metadata for the verbose dump;
        # output (content/function calls) and usage are surfaced elsewhere.
        self.raw_attrs.update(
            _extract_raw_attrs(event.response, skip=("output", "usage"))
        )
        if event.type == "response.completed":
            if event.response.usage:
                # Usage is delivered on the final event by default (it is part of
                # the Response object; "usage" is no longer a valid include value).
                self.usage_info = event.response.usage
            # Search grounding (issue #131): url_citation annotations live
            # on the assembled message output, not in stream deltas.
            self.web_search_citations.extend(
                _citations_from_output(getattr(event.response, "output", None))
            )

    def handle_text_delta(self, event) -> None:
        """Collect assistant text and reasoning deltas."""
        if not event.delta:
            return
        if event.type == "response.output_text.delta":
            self.content.append(event.delta)
        else:
            self.reasoning.append(event.delta)

    def handle_call_arguments(self, event) -> None:
        """Assemble per-item function_call arguments (split across deltas)."""
        if event.type == "response.function_call_arguments.done":
            self.partial_arguments[event.item_id] = event.arguments or ""
            return
        item_id = event.item_id
        self.partial_arguments[item_id] = self.partial_arguments.get(item_id, "") + (
            event.delta or ""
        )

    def handle_output_item(self, event) -> None:
        """Append a finished output item to the tool calls / reasoning items.

        ``function_call`` items become tool calls; ``reasoning`` items (the
        encrypted chain of thought Meta's Muse Spark returns when
        ``reasoning.encrypted_content`` is included) are kept as raw item
        dicts so the stateless client can replay them verbatim in the next
        round's ``input``.
        """
        item = event.item
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            self.tool_calls.append(
                {
                    "call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments
                    or self.partial_arguments.get(item.id, ""),
                }
            )
        elif item_type == "reasoning":
            self.reasoning_items.append(_reasoning_item_dict(item))
        elif item_type == "web_search_call":
            self.web_search_calls.append(_web_search_call_dict(item))


def _reasoning_item_dict(item) -> dict[str, Any]:
    """Build the replayable input dict for a finished ``reasoning`` item.

    The item is replayed verbatim (Meta's docs: encrypted reasoning items
    are opaque -- keep them whole or drop them).  ``id`` is optional on
    replay and ``summary`` is required (an empty list when the item carries
    none), so both are normalized defensively.
    """
    item_id = getattr(item, "id", None)
    summary = getattr(item, "summary", None)
    if summary is None:
        summary = []
    replay: dict[str, Any] = {
        "type": "reasoning",
        "summary": list(summary),
    }
    if item_id:
        replay["id"] = item_id
    encrypted = getattr(item, "encrypted_content", None)
    if encrypted:
        replay["encrypted_content"] = encrypted
    return replay


def _web_search_call_dict(item) -> dict[str, Any]:
    """Normalized dict for a finished ``web_search_call`` item (issue #131)."""
    return {
        "id": getattr(item, "id", None),
        "status": getattr(item, "status", None),
    }


def _citations_from_output(output) -> list[dict[str, Any]]:
    """Collect ``url_citation`` annotations from a completed response output."""
    citations: list[dict[str, Any]] = []
    for entry in output or []:
        content = (
            entry.get("content")
            if isinstance(entry, dict)
            else getattr(entry, "content", None)
        )
        for block in content or []:
            anns = (
                block.get("annotations")
                if isinstance(block, dict)
                else getattr(block, "annotations", None)
            )
            for ann in anns or []:
                atype = (
                    ann.get("type")
                    if isinstance(ann, dict)
                    else getattr(ann, "type", None)
                )
                if atype != "url_citation":
                    continue
                if isinstance(ann, dict):
                    citations.append(
                        {
                            "url": ann.get("url", ""),
                            "title": ann.get("title", ""),
                            "start_index": ann.get("start_index"),
                            "end_index": ann.get("end_index"),
                        }
                    )
                else:
                    citations.append(
                        {
                            "url": getattr(ann, "url", ""),
                            "title": getattr(ann, "title", ""),
                            "start_index": getattr(ann, "start_index", None),
                            "end_index": getattr(ann, "end_index", None),
                        }
                    )
    return citations


def _handle_untyped_error(event) -> None:
    message = getattr(event, "message", None)
    code = getattr(event, "code", None)
    if message or code:
        raise RuntimeError(f"{code}: {message}" if code else message)
    # Unknown untyped event with no error payload: skip it.


def _raise_failed_error(event) -> None:
    """Raise the provider error carried by a response.failed event."""
    error = event.response.error
    message = error.message if error and error.message else "Response failed"
    raise RuntimeError(message)


# ---------------------------------------------------------------------------
# Module-level delegator (thin wrapper over ResponsesStreamConsumer).
# ---------------------------------------------------------------------------


def _consume_response_stream(stream, cancel_event=None):
    """Consume a streaming Responses API response and assemble its parts.

    Returns ``(full_content, reasoning_content, tool_calls, usage_info,
    response_id, raw_attrs, reasoning_items)`` where ``tool_calls`` is a
    list of ``{"call_id", "name", "arguments"}`` dicts and ``reasoning_items``
    the finished ``reasoning`` output items (raw replayable dicts) in stream
    order.  See :meth:`ResponsesStreamConsumer.consume`.
    """
    return ResponsesStreamConsumer().consume(stream, cancel_event=cancel_event)


def _stream_response(client, call_kwargs, tools_schemas, cancel_event=None):
    """Open a streaming Responses API call and fully consume it.

    Returns ``(full_content, reasoning_content, tool_calls, usage_info,
    response_id, raw_attrs, reasoning_items)``. Tool schemas are attached
    here (mirroring ``completions_api._stream_response``); the caller builds
    the remaining kwargs per round.

    The effective model's built-in (native) tools (e.g. Alibaba/Qwen's
    ``code_interpreter`` / ``web_search`` / ``web_extractor``) are resolved
    per model in ``responses_state._build_call_kwargs`` and carried in
    ``call_kwargs`` under the reserved ``_builtin_tools`` key as Responses
    entries (``{"type": ...}``, already in the Responses format -- they must
    not go through the function-schema conversion).  They are merged after
    the converted function-tool schemas, mirroring the web agent; the key is
    popped before the API call so it never reaches the server.

    When ``cancel_event`` is set (user pressed Enter while waiting), the
    stream is abandoned and the underlying connection is closed.
    """
    builtin_tools = call_kwargs.pop("_builtin_tools", None) or []
    tools = list(tools_schemas or []) + list(builtin_tools)
    if tools:
        logger.debug(f"Calling Responses API (streaming) with {len(tools)} tools")
        stream = client.responses.create(
            **call_kwargs,
            tools=tools,
            tool_choice="auto",
        )
    else:
        logger.debug("Calling Responses API (streaming) without tools")
        stream = client.responses.create(**call_kwargs)

    try:
        return _consume_response_stream(stream, cancel_event=cancel_event)
    finally:
        # Abort the underlying HTTP stream when the user pressed Enter so the
        # connection is released promptly instead of streaming to completion.
        if cancel_event is not None and cancel_event.is_set():
            stream.close()
