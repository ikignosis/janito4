"""
Stream consumption for the Chat Completions API.

These helpers are shared by the OpenAI-compatible clients that talk to
``client.chat.completions.create`` with streaming enabled.  They assemble the
streamed deltas (content, reasoning/thinking text and tool-call arguments,
which arrive split across many chunks) into a single response.

The per-chunk folding lives in the shared
:class:`~janito.llm_adapters.completions.CompletionsTurnAccumulator` (used directly by
the web loop); :class:`CompletionsStreamConsumer` adds
the CLI-specific driver — :meth:`consume` walks a sync stream with
Enter-to-cancel support and returns the response parts.  The module-level
``_consume_stream`` function is a thin delegator used by
``_stream_response`` and its tests.
"""

import logging

from janito.llm_adapters.completions import CompletionsTurnAccumulator
from janito.llm_adapters.sdk import _extract_raw_attrs

# Configure logger for this module
logger = logging.getLogger(__name__)


class CompletionsStreamConsumer(CompletionsTurnAccumulator):
    """Assemble Chat Completions stream chunks into a single response (CLI).

    The consumer owns the accumulated content / reasoning text and the
    per-index tool-call map (arguments arrive split across many chunks, so
    they are accumulated by ``index``).  :meth:`consume` drives the stream
    and returns the response parts; the ``handle_*`` methods apply individual
    chunks/deltas.
    """

    def handle(self, chunk) -> tuple[str | None, str | None]:
        """Process one chunk, also capturing the raw response metadata.

        Besides folding content/reasoning/tool-call deltas (see the shared
        base), the chunk's top-level scalar attributes (``id``, ``model``,
        ``created``, ``system_fingerprint``, ...) and the terminal
        ``finish_reason`` are kept in ``raw_attrs`` for the verbose response
        dump.  ``content``/``usage``/``choices`` are surfaced elsewhere, so
        they are skipped here.
        """
        result = super().handle(chunk)
        self.raw_attrs.update(_extract_raw_attrs(chunk, skip=("choices", "usage")))
        if chunk.choices:
            finish = getattr(chunk.choices[0], "finish_reason", None)
            if finish:
                self.raw_attrs["finish_reason"] = finish
        return result

    @property
    def full_content(self) -> str:
        """The assembled assistant text."""
        return "".join(self.content)

    @property
    def reasoning_content(self) -> str | None:
        """The assembled reasoning text, or ``None`` when none was streamed."""
        return "".join(self.reasoning) if self.reasoning else None

    def handle_chunk(self, delta) -> None:
        """Accumulate content, reasoning and tool-call deltas from one delta.

        Legacy per-delta entry point: the CLI consumed one raw ``delta`` at a
        time before the chunk-level ``handle`` API existed (the web loop and
        the shared ``consume`` driver call ``handle(chunk)`` instead).
        """
        # Collect reasoning / thinking content (DeepSeek R1, OpenAI o1/o3, ...)
        self._handle_reasoning_delta(delta)

        # Accumulate main content silently
        if delta.content:
            self.content.append(delta.content)

        # Accumulate tool-call deltas (split across many chunks)
        self._handle_tool_call_delta(delta)

    # ------------------------------------------------------------------
    # Stream driving
    # ------------------------------------------------------------------

    def consume(self, stream, cancel_event=None):
        """Consume a streaming completion and assemble the response parts.

        Returns ``(full_content, reasoning_content, tool_calls_map,
        usage_info, raw_attrs)`` where ``tool_calls_map`` maps call index ->
        ``{id, name, arguments}`` and ``raw_attrs`` holds the chunk's raw
        top-level response metadata (id, model, created, finish_reason, ...).

        When ``cancel_event`` is set (user pressed Enter while waiting), the
        stream is abandoned as soon as the next chunk arrives.
        """
        for chunk in stream:
            # Honour an Enter-to-cancel request: stop consuming as soon as the
            # next chunk arrives so the worker can close the connection.
            if cancel_event is not None and cancel_event.is_set():
                break
            self.handle(chunk)

        return (
            self.full_content,
            self.reasoning_content,
            self.tool_calls,
            self.usage_info,
            self.raw_attrs,
        )


# ---------------------------------------------------------------------------
# Module-level delegator (thin wrapper over CompletionsStreamConsumer).
# ---------------------------------------------------------------------------


def _consume_stream(stream, cancel_event=None):
    """Consume a streaming completion and assemble the response parts.

    Returns ``(full_content, reasoning_content, tool_calls_map, usage_info,
    raw_attrs)``.  See :meth:`CompletionsStreamConsumer.consume`.
    """
    return CompletionsStreamConsumer().consume(stream, cancel_event=cancel_event)


def _stream_response(client, call_kwargs, tools_schemas, cancel_event=None):
    """Open a streaming completion and fully consume it.

    Returns ``(full_content, reasoning_content, tool_calls_map, usage_info,
    raw_attrs)``.

    When ``cancel_event`` is set (user pressed Enter while waiting), the
    stream is abandoned and the underlying connection is closed.
    """
    if tools_schemas:
        logger.debug(f"Calling API (streaming) with {len(tools_schemas)} tools")
        stream = client.chat.completions.create(
            **call_kwargs,
            tools=tools_schemas,
            tool_choice="auto",
        )
    else:
        logger.debug("Calling API (streaming) without tools")
        stream = client.chat.completions.create(**call_kwargs)

    try:
        return _consume_stream(stream, cancel_event=cancel_event)
    finally:
        # Abort the underlying HTTP stream when the user pressed Enter so the
        # connection is released promptly instead of streaming to completion.
        if cancel_event is not None and cancel_event.is_set():
            stream.close()
