"""
Stream consumption for the native Gemini API (``generate_content_stream``).

These helpers are used by the native Gemini client module (the CLI
``janito.llm_clients.gemini.gemini_api``).  They assemble the streamed
``GenerateContentResponse`` chunks -- whose ``candidates[0].content.parts``
carry thought text, final text and ``function_call`` parts -- into a single
response.

The per-chunk folding lives in
:class:`janito.agent.gemini.GeminiStreamConsumer` (the shared adapter layer,
issue #90); the module-level ``_consume_stream`` / ``_consume_chunk``
functions are thin delegators used by the module's own ``_stream_response``
and by the client tests.
"""

import logging

from janito.agent.gemini import GeminiStreamConsumer

# Configure logger for this module
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level delegators (thin wrappers over GeminiStreamConsumer).
# ---------------------------------------------------------------------------


def _consume_stream(stream, cancel_event=None):
    """Consume a Gemini stream and assemble the response parts.

    Returns ``(full_content, reasoning_content, tool_calls_list, usage_info,
    raw_attrs, thought_parts)``.  See :meth:`GeminiStreamConsumer.consume`.
    """
    return GeminiStreamConsumer().consume(stream, cancel_event=cancel_event)


def _consume_chunk(chunk, consumer: GeminiStreamConsumer | None = None):
    """Fold one Gemini stream chunk into a consumer (legacy bridge)."""
    consumer = consumer or GeminiStreamConsumer()
    consumer.handle(chunk)
    return consumer


def _stream_response(client, call_kwargs, tools_schemas, cancel_event=None):
    """Open a ``generate_content_stream`` and fully consume it.

    Returns ``(full_content, reasoning_content, tool_calls_list, usage_info,
    raw_attrs, thought_parts)``.

    Function-tool schemas are attached **here** (mirroring
    ``completions_api._stream_response`` / ``anthropic_stream._stream_response``):
    the caller's ``call_kwargs`` only carries the provider's native (built-in)
    tools in ``config.tools`` (e.g. Google Search / code execution), so the
    resolved function declarations are appended to ``config.tools`` -- unless
    the config already declares them (the web agent's ``build_call_kwargs``
    converts the schemas up front).  Without this, the Gemini API receives no
    function declarations and the model hallucinates malformed tool calls
    (``MALFORMED_FUNCTION_CALL``, empty answer).

    When ``cancel_event`` is set (user pressed Enter while waiting), the
    stream is abandoned as soon as the next chunk arrives.
    """
    if tools_schemas:
        logger.debug(f"Calling Gemini API (streaming) with {len(tools_schemas)} tools")
        from janito.agent.gemini import _convert_tools_to_gemini_format

        function_tools = _convert_tools_to_gemini_format(tools_schemas)
        if function_tools:
            call_kwargs = dict(call_kwargs)
            config = dict(call_kwargs.get("config") or {})
            existing_tools = list(config.get("tools") or [])
            has_function_declarations = any(
                isinstance(tool, dict) and tool.get("function_declarations")
                for tool in existing_tools
            )
            if not has_function_declarations:
                config["tools"] = existing_tools + function_tools
                call_kwargs["config"] = config
    else:
        logger.debug("Calling Gemini API (streaming) without tools")
    stream = client.models.generate_content_stream(**call_kwargs)
    try:
        return _consume_stream(stream, cancel_event=cancel_event)
    finally:
        # Abort the underlying HTTP stream when the user pressed Enter so the
        # connection is released promptly instead of streaming to completion.
        if cancel_event is not None and cancel_event.is_set():
            close = getattr(stream, "close", None)
            if callable(close):
                close()
