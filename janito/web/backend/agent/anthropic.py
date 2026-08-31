"""Native Anthropic SDK runner for the web agentic loop.

The per-API adapter (call-kwargs building, history conversion, stream
accumulation) lives in :mod:`janito.llm_adapters.anthropic` — the shared adapter
layer used by both agent loops.  This module keeps the web-only glue:
:func:`create_client` (async Anthropic SDK client, lazily importing the
optional ``anthropic`` package) and :func:`stream_turn_events` (which
drives the stream and yields reasoning/token events to the browser).  The
loop builds call kwargs and accumulators directly from the shared adapters
in :mod:`janito.llm_adapters.anthropic`.
"""

import logging

from janito.llm_adapters.anthropic import AnthropicTurnAccumulator
from janito.optional_packages import require_optional_package

from .stream_utils import emit_stream_events

logger = logging.getLogger(__name__)


def create_client(base_url, api_key):
    """Create the async native Anthropic SDK client, guarding the optional package."""
    require_optional_package("anthropic", "Anthropic", "anthropic")
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=api_key, base_url=base_url)


async def stream_turn_events(client, call_kwargs: dict, acc: AnthropicTurnAccumulator):
    """Stream one Anthropic Messages turn, yielding reasoning/token events.

    The caller owns ``acc``; on completion it holds the full turn state for
    end-of-turn assembly (``run_tool_turn`` / ``DoneEvent``).
    """
    stream = await client.messages.create(**call_kwargs)
    async for ev in emit_stream_events(stream, acc, break_on_done=True):
        yield ev


__all__ = [
    "create_client",
    "stream_turn_events",
]
