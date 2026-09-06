"""Shared streaming helpers for the web agentic-loop runners.

The per-API runner modules (``completions`` / ``responses`` / ``anthropic`` /
``gemini`` / ``dashscope``) all consume their SDK stream the same way:
``acc.handle(item)`` produces reasoning/content deltas that become
``ReasoningEvent`` / ``TokenEvent``s (plus ``ImageEvent``s for the Responses
image tool), stopping early when the accumulator reports ``done``.  The sync
SDK runners (Gemini / DashScope) additionally pull each chunk through
``asyncio.to_thread`` via ``_next_or_none``.  Both helpers used to be
duplicated per runner; this module is their single home.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from ..events import ImageEvent, ReasoningEvent, SourcesEvent, TokenEvent, WebSearchEvent


def _next_or_none(gen):
    """``next(gen)`` that returns ``None`` at exhaustion (for ``to_thread``)."""
    try:
        return next(gen)
    except StopIteration:
        return None


async def emit_stream_events(
    stream, acc, *, break_on_done: bool = False, emit_images: bool = False
) -> AsyncGenerator[Any, None]:
    """Yield reasoning/token (and optionally image) events from one stream.

    Args:
        stream: The async iterable of SDK stream items.
        acc: The per-API turn accumulator with ``handle(item)`` returning
            ``(reasoning_delta, content_delta)``, plus ``done`` and (for
            Responses) ``image_results``.
        break_on_done: Stop consuming once ``acc.done`` is set (native-SDK
            runners).
        emit_images: Also yield ``ImageEvent``s for newly completed native
            image-generation results (Responses runner).
    """
    emitted_images = 0
    emitted_searches = 0
    async for item in stream:
        reasoning_delta, content_delta = acc.handle(item)
        if reasoning_delta:
            yield ReasoningEvent(content=reasoning_delta)
        if content_delta:
            yield TokenEvent(content=content_delta)
        if emit_images:
            for img in acc.image_results[emitted_images:]:
                yield ImageEvent(
                    path=img["path"],
                    revised_prompt=img.get("revised_prompt", ""),
                )
                emitted_images += 1
        for call in getattr(acc, "web_search_calls", [])[emitted_searches:]:
            yield WebSearchEvent(status=call.get("status") or "completed")
            emitted_searches += 1
        if break_on_done and acc.done:
            break
    citations = list(getattr(acc, "web_search_citations", []) or [])
    if citations:
        yield SourcesEvent(sources=citations)


__all__ = [
    "emit_stream_events",
    "_next_or_none",
]
