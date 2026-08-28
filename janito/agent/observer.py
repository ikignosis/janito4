"""Pluggable UI observer for one ``send_prompt`` turn.

The API clients (``Client.send`` in ``janito.openai_client.base_client``)
drive the LLM turn loop; every user-visible output they produce -- reasoning
fragments, message fragments, the verbose call/response dumps, the error
explainers and the end-of-turn report -- is routed through a
:class:`TurnObserver` so the API layer itself stays UI-free.

The **default observer is ``None``**, which the clients resolve to the
headless :class:`NullObserver`: ``send_prompt``/``Client.send`` produce no
terminal output at all (the web loop already emits structured events instead
of printing).  The CLI injects the Rich observer
(:class:`janito.openai_client.client_support.RichTurnObserver`) through
``_make_send_prompt_func`` in ``cli/chat.py`` -- the same composition point
that injects the per-round ``stream_runner`` -- so every CLI entry point
(interactive shell, ``/ask``, ``/compact``, one-shot prompt) keeps today's
output.  Non-TUI consumers can implement the protocol to capture or forward
the events.

The event vocabulary mirrors :mod:`janito.agent.events` (the web loop's
WebSocket events): ``on_reasoning`` ~ ``ReasoningEvent``, ``on_message`` ~
``TokenEvent``/``DoneEvent``.  Granularity here is the per-round assembled
fragment the client loop has (after a full stream round), not per-token
deltas.

The end-of-turn report (``on_turn_complete``) is delivered by the *caller*
(the CLI's ``wrap_send_prompt_with_turn_report`` wrapper), because the
conversation turn number is display-only caller knowledge that is never
passed to the API client; the wrapper supplies it at render time.
"""

from __future__ import annotations

from typing import Any, Protocol


class TurnObserver(Protocol):
    """Receives every UI event produced during one ``send_prompt`` turn.

    A single observer object replaces a growing list of loose callbacks:
    adding a new UI event is one new method with a no-op default, instead of
    threading another ``handle_*`` parameter through every client module.
    """

    def on_reasoning(self, content: str) -> None:
        """A thinking/reasoning fragment finished streaming (once per round)."""
        ...

    def on_message(self, content: str) -> None:
        """A content fragment finished streaming (once per round)."""
        ...

    def on_verbose_info(
        self,
        *,
        base_url: str | None,
        model: str,
        mcp_manager: Any,
        backend_default: str,
    ) -> None:
        """Verbose banner: model, backend and connected MCP services."""
        ...

    def on_verbose_call(
        self,
        call_kwargs: dict[str, Any],
        tools_schemas: list[dict[str, Any]] | None,
    ) -> None:
        """Verbose request dump just before the API call (once per round)."""
        ...

    def on_verbose_response(
        self,
        full_content: str,
        reasoning_content: str | None,
        tool_calls: list[dict[str, Any]] | None,
        usage_info: Any,
        response_id: str | None,
        raw_attrs: dict[str, Any] | None = None,
    ) -> None:
        """Verbose response summary just after the API call (once per round)."""
        ...

    def on_error(
        self,
        e: Exception,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        response_id: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        """Render an error explainer (auth failure, unknown model, ...).

        ``error_kind`` is the explicit classification of the failure --
        ``"not_found"`` (unknown model / stale previous response) or
        ``"auth"`` (invalid API key).  The OpenAI SDK clients pass it from
        their typed ``except`` blocks; the native-SDK clients (Anthropic /
        DashScope / Gemini) derive it with
        :func:`janito.openai_client.client_support._classify_error`, so the
        observer holds no message-matching heuristics.  ``None``/``"unknown"``
        render nothing.  The observer only *renders* the explainer; the
        client always re-raises the exception after the call, so error
        handling flow is unchanged.
        """
        ...

    def on_turn_complete(self, usage_out: Any, *, turn: int | None = None) -> None:
        """End-of-turn report (used files + token-usage summary).

        Delivered by the caller after ``send_prompt`` returns (the CLI's
        ``wrap_send_prompt_with_turn_report`` wrapper), which knows the
        display-only conversation turn number.
        """
        ...


class NullObserver:
    """Headless observer: drops every event.

    The default when no observer is injected, so the API clients produce no
    terminal output (purely API-side).  Subclass it to build observers with
    only a few handlers overridden (the Rich observer does this).
    """

    def on_reasoning(self, content: str) -> None:
        pass

    def on_message(self, content: str) -> None:
        pass

    def on_verbose_info(
        self,
        *,
        base_url: str | None,
        model: str,
        mcp_manager: Any,
        backend_default: str,
    ) -> None:
        pass

    def on_verbose_call(
        self,
        call_kwargs: dict[str, Any],
        tools_schemas: list[dict[str, Any]] | None,
    ) -> None:
        pass

    def on_verbose_response(
        self,
        full_content: str,
        reasoning_content: str | None,
        tool_calls: list[dict[str, Any]] | None,
        usage_info: Any,
        response_id: str | None,
        raw_attrs: dict[str, Any] | None = None,
    ) -> None:
        pass

    def on_error(
        self,
        e: Exception,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        response_id: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        pass

    def on_turn_complete(self, usage_out: Any, *, turn: int | None = None) -> None:
        pass


__all__ = [
    "NullObserver",
    "TurnObserver",
]
