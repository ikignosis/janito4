"""Chat Completions runner for the web agentic loop.

The per-API adapter (call-kwargs building, stream accumulation) lives in
:mod:`janito.llm_adapters.completions` \u2014 the shared adapter layer used by both
agent loops.  This module keeps the web-only glue: :func:`create_client`
(async OpenAI SDK client), :func:`build_call_kwargs` (adds the conversation
``messages`` and the function ``tools`` for this round to the shared
adapter's kwargs) and :func:`stream_turn_events` (which drives the stream
and yields reasoning/token events to the browser).  The loop builds call
kwargs and accumulators directly from the shared adapters in
:mod:`janito.llm_adapters.completions`.
"""

import logging

from janito.llm_adapters.completions import CompletionsTurnAccumulator
from janito.llm_adapters.completions import build_call_kwargs as _build_base_call_kwargs

from .stream_utils import emit_stream_events

logger = logging.getLogger(__name__)


def create_client(base_url, api_key):
    """Create the async OpenAI SDK client (base_url may be ``None``)."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def build_call_kwargs(
    model,
    messages,
    tools_schemas,
    config,
    max_output_tokens,
    preserve_thinking,
    reasoning_effort,
) -> dict:
    """Build the ``chat.completions.create`` kwargs for one turn.

    Wraps the shared adapter's builder (thinking / preserve_thinking /
    reasoning_effort / built-in tools / streaming options) and adds the
    conversation ``messages`` plus the function ``tools`` for this round.
    """
    call_kwargs = _build_base_call_kwargs(
        model,
        config,
        max_output_tokens,
        preserve_thinking,
        reasoning_effort,
    )
    call_kwargs["messages"] = messages
    if tools_schemas:
        call_kwargs["tools"] = tools_schemas
        call_kwargs["tool_choice"] = "auto"
    return call_kwargs


async def stream_turn_events(
    client, call_kwargs: dict, acc: CompletionsTurnAccumulator
):
    """Stream one Chat Completions turn, yielding reasoning/token events.

    The caller owns ``acc``; on completion it holds the full turn state for
    end-of-turn assembly (``run_tool_turn`` / ``DoneEvent``).
    """
    stream = await client.chat.completions.create(**call_kwargs)
    async for ev in emit_stream_events(stream, acc):
        yield ev


__all__ = [
    "create_client",
    "build_call_kwargs",
    "stream_turn_events",
]
