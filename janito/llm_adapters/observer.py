"""Pluggable UI observer for one LLM turn.

The API clients (``Client.run_turn`` in ``janito.llm_clients.base_client``)
drive the LLM turn loop; every user-visible output they produce -- reasoning
fragments, message fragments, the verbose call/response dumps, the error
explainers and the end-of-turn report -- is routed through a
:class:`TurnObserver` so the API layer itself stays UI-free.

The **default observer is ``None``**, which the clients resolve to the
headless :class:`NullObserver`: ``run_turn``/``Client.run_turn`` produce no
terminal output at all (the web loop already emits structured events instead
of printing).  The CLI injects the Rich observer
(:class:`janito.ui.observer.RichTurnObserver`) through
``_make_turn_func`` in ``cli/chat.py`` -- the same composition point
that injects the per-round ``stream_runner`` (both carried by the
:class:`~janito.ui.config.UIConfig`) -- so every CLI entry point
(interactive shell, ``/ask``, one-shot prompt) keeps today's output.  The
``/compact`` compression call re-invokes the session's turn factory with
``silent=True``, swapping in the silent variant
(:class:`janito.ui.observer.SilentTurnObserver`) -- the raw recap JSON is
not echoed to the terminal, while the injected stream runner keeps the
spinner and the accounting row is still recorded.  Non-TUI consumers can
implement the protocol to capture or forward the events.

The event vocabulary mirrors :mod:`janito.web.backend.events` (the web loop's
WebSocket events): ``on_reasoning`` ~ ``ReasoningEvent``, ``on_message`` ~
``TokenEvent``/``DoneEvent``.  Granularity here is the per-round assembled
fragment the client loop has (after a full stream round), not per-token
deltas.

The end-of-turn report (``on_turn_complete``) is delivered by
``Client.run_turn`` itself when the turn finishes -- like every other
observer event -- so the caller has nothing to wrap; the CLI's
``RichTurnObserver`` (:mod:`janito.ui.observer`) renders the report *and*
records the overall-use accounting row from that call.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from janito.llm_adapters.usage import TurnInfo


class TurnObserver(Protocol):
    """Receives every UI event produced during one ``run_turn`` turn.

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
        :func:`janito.llm_clients.client_support._classify_error`, so the
        observer holds no message-matching heuristics.  ``None``/``"unknown"``
        render nothing.  The observer only *renders* the explainer; the
        client always re-raises the exception after the call, so error
        handling flow is unchanged.
        """
        ...

    def on_limits(self, http_error_msg: str, retry_interval: float) -> None:
        """Rate-limit wait (issue #116): render, wait, return to retry."""
        ...

    def on_turn_complete(
        self,
        token_stats: TurnInfo | None,
        api_config: Any,
        elapsed_time: float | None = None,
    ) -> None:
        """End-of-turn report (used files + token-usage summary + accounting).

        Invoked by ``Client.run_turn`` at the end of the turn with the
        client-built :class:`~janito.llm_adapters.usage.TurnInfo`
        (``token_stats``, every round's usage folded into it) and the turn's
        resolved :class:`~janito.llm_clients.api_config.APIConfig`
        (``api_config``);
        the report's provider / model / max tokens come from the config
        (issue #82: there is no caller-supplied out-param).
        ``elapsed_time`` is the wall-clock duration of the turn in seconds,
        measured by ``Client.run_turn`` from its entry until the end of the
        turn (issue #99); the CLI's usage line renders it as the ``Time:``
        part.  The CLI's
        ``RichTurnObserver`` (:mod:`janito.ui.observer`) renders the report
        and records the overall-use accounting row from this call; the
        headless ``NullObserver`` drops it
        (the web loop emits its own structured events and records its own
        accounting).
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

    def on_limits(self, http_error_msg: str, retry_interval: float) -> None:
        time.sleep(retry_interval)

    def on_turn_complete(
        self,
        token_stats: Any,
        api_config: Any,
        elapsed_time: float | None = None,
    ) -> None:
        pass


__all__ = [
    "NullObserver",
    "TurnObserver",
]
