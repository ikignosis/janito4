"""Responses API runner for the web agentic loop.

The per-API adapter (call-kwargs building, history conversion, stream
accumulation) lives in :mod:`janito.llm_adapters.responses` — the shared adapter
layer used by both agent loops.  This module keeps the web-only glue:
:func:`create_client` (async SDK client) and :func:`stream_turn_events`
(which drives the stream and yields reasoning/token/image events to the
browser).  The loop builds call kwargs and accumulators directly from the
shared adapters in :mod:`janito.llm_adapters.responses`.
"""

import logging

from janito.llm_adapters.responses import ResponsesTurnAccumulator

from .stream_utils import emit_stream_events

logger = logging.getLogger(__name__)


def create_client(base_url, api_key):
    """Create the async OpenAI SDK client (base_url may be ``None``)."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=api_key, base_url=base_url)


async def stream_turn_events(client, call_kwargs: dict, acc: ResponsesTurnAccumulator):
    """Stream one Responses turn, yielding reasoning/token/image events.

    The caller owns ``acc``; on completion it holds the full turn state for
    end-of-turn assembly (``run_tool_turn`` / ``DoneEvent``).  Images
    generated natively by the ``image_generation`` tool are saved to temp
    PNG files by the accumulator and surfaced here as ``ImageEvent``s the
    moment their output item completes (``emit_stream_events`` tracks the
    ones already yielded so each image is emitted exactly once).
    """
    stream = await client.responses.create(**call_kwargs)
    async for ev in emit_stream_events(stream, acc, emit_images=True):
        yield ev


__all__ = [
    "create_client",
    "stream_turn_events",
]
