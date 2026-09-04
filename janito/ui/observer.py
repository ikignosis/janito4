"""The CLI's default Rich turn observer (and its end-of-turn accounting).

Implements the :class:`~janito.llm_adapters.observer.TurnObserver` protocol by
delegating to this package's display helpers, so the rendered output is
byte-for-byte today's behaviour while ``Client.run_turn`` itself stays
UI-free.  The observer owns its ``Console``; tests can inject
``Console(file=...)`` to capture the output.
"""

import logging
import time
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from janito.llm_adapters.observer import NullObserver
from janito.llm_adapters.usage import TurnInfo
from janito.providers.costing import get_provider_cost_value
from janito.tooling.accounting import record_turn

from ..llm_clients.api_config import APIConfig
from .display import (
    _display_content,
    _display_reasoning,
    _print_verbose_api_call,
    _print_verbose_api_response,
    _print_verbose_info,
)
from .errors import _handle_auth_error, _handle_not_found_error
from .usage import display_turn_usage

logger = logging.getLogger(__name__)


class RichTurnObserver(NullObserver):
    """Render turn events to a Rich console (the CLI's default observer).

    Implements the :class:`~janito.llm_adapters.observer.TurnObserver` protocol by
    delegating to this package's display helpers (``_display_reasoning``,
    ``_display_content``, the verbose printers, the error explainers and
    ``display_turn_usage``), so the rendered output is byte-for-byte today's
    behaviour while ``Client.run_turn`` itself stays UI-free.  Its
    ``on_turn_complete`` additionally records the overall-use accounting row
    (:func:`_record_accounting`) -- the end-of-turn bookkeeping lives in the
    observer.  The observer owns its ``Console``; tests can inject
    ``Console(file=...)`` to capture the output.
    """

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def on_reasoning(self, content: str) -> None:
        _display_reasoning(content, self.console)

    def on_message(self, content: str) -> None:
        _display_content(content, self.console)

    def on_verbose_info(
        self,
        *,
        base_url: str | None,
        model: str,
        mcp_manager,
        backend_default: str,
    ) -> None:
        _print_verbose_info(self.console, base_url, model, mcp_manager, backend_default)

    def on_verbose_call(
        self,
        call_kwargs: dict[str, Any],
        tools_schemas: list[dict[str, Any]] | None,
    ) -> None:
        _print_verbose_api_call(self.console, call_kwargs, tools_schemas)

    def on_verbose_response(
        self,
        full_content: str,
        reasoning_content: str | None,
        tool_calls: list[dict[str, Any]] | None,
        usage_info: Any,
        response_id: str | None,
        raw_attrs: dict[str, Any] | None = None,
    ) -> None:
        _print_verbose_api_response(
            self.console,
            full_content,
            reasoning_content,
            tool_calls,
            usage_info,
            response_id,
            raw_attrs=raw_attrs,
        )

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
        """Render an error explainer for a classified failure.

        ``error_kind`` is explicit -- ``"not_found"`` or ``"auth"`` -- so
        the observer holds no message-matching heuristics: the OpenAI SDK
        clients pass it from their typed ``except`` blocks (NotFoundError /
        AuthenticationError), and the native-SDK clients (Anthropic,
        DashScope, Gemini) pass :func:`janito.llm_clients.client_support._classify_error`'s
        result from their generic handler.  ``None`` / ``"unknown"`` renders
        nothing (the caller always re-raises).
        """
        if error_kind == "not_found":
            _handle_not_found_error(
                e, base_url, model, self.console, response_id=response_id
            )
        elif error_kind == "auth":
            _handle_auth_error(e, provider, api_key, base_url, model, self.console)
        # else: unknown failure -- nothing to explain; the caller re-raises.

    def on_limits(self, http_error_msg: str, retry_interval: float) -> None:
        """Rate-limit wait (issue #116): spinner, sleep, return to retry.

        The limit dimension comes from the error text: ``"requests"`` ->
        Requests limit, ``"tokens"`` -> Tokens limit, else a generic
        rate-limit message. Shows ``<Kind> limit was reached, retrying in
        (n)s.`` with a spinner, then blocks for ``retry_interval`` so the
        caller retries afterwards.
        """
        lowered = (http_error_msg or "").lower()
        if "requests" in lowered:
            kind = "Requests"
        elif "tokens" in lowered:
            kind = "Tokens"
        else:
            kind = "Rate"
        message = f"{kind} limit was reached, retrying in ({retry_interval:g})s."
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=self.console,
        ) as progress:
            progress.add_task(message, total=None)
            time.sleep(retry_interval)

    def on_turn_complete(self, token_stats, api_config) -> None:
        """End-of-turn report: record accounting, then render the usage summary.

        Invoked by ``Client.run_turn`` at the end of every turn that reported
        token usage, with the client-built
        :class:`~janito.llm_adapters.usage.TurnInfo` and the resolved
        :class:`~janito.llm_clients.api_config.APIConfig` for the turn
        (provider / model / max tokens come from the api_config).
        ``elapsed_time`` is the turn's wall-clock duration in seconds
        (measured by ``Client.run_turn`` from its entry, issue #99); it is
        rendered as the ``Time:`` part of the usage summary line.  The
        overall-use accounting row (:func:`_record_accounting`, best effort,
        never raises) is written here -- from the observer -- so neither the
        API clients nor the CLI carry it; the rendered report (used files +
        token-usage summary) is delegated to :func:`display_turn_usage`.
        """
        _record_accounting(token_stats, api_config)
        display_turn_usage(token_stats, api_config, console=self.console)


class SilentTurnObserver(NullObserver):
    """Turn observer for side calls that must stay silent (e.g. /compact).

    Drops every render -- reasoning, message content, the verbose
    call/response dumps, the error explainers and the end-of-turn usage
    summary -- but still records the overall-use accounting row on
    ``on_turn_complete`` (:func:`_record_accounting`), so the invisible
    bookkeeping every CLI entry point feeds keeps happening without a single
    line of output.  The /compact compression call uses this observer: its
    raw JSON recap must not be echoed to the terminal, but the turn must
    still count in ``accounting.db`` (the injected per-round stream runner
    is untouched, so the progress bar keeps working).
    """

    def on_turn_complete(self, token_stats, api_config) -> None:
        """Record the accounting row only; never render anything."""
        _record_accounting(token_stats, api_config)

    def on_limits(self, http_error_msg: str, retry_interval: float) -> None:
        """Wait silently for the retry interval (no output)."""
        time.sleep(retry_interval)


def _record_accounting(token_stats: TurnInfo | None, api_config: APIConfig) -> None:
    """Append one overall-use accounting row for a completed turn (best effort).

    Uses the turn-wide cumulative counters (:class:`~janito.llm_adapters.usage.TurnInfo`
    accumulates every round of the turn, tool-call rounds included) so the
    accounting log reflects the billed usage; falls back to the final round's
    counters when the turn-wide ones were not reported.  The provider / model
    (and the numeric dollar cost estimate from
    :func:`janito.providers.costing.get_provider_cost_value`) come from the
    turn's resolved :class:`~janito.llm_clients.api_config.APIConfig`.
    Never raises -- accounting must not be able to break the agent loop
    (issue #72).

    Invoked from the observer's ``on_turn_complete`` (the CLI's
    :class:`RichTurnObserver`), so every CLI entry point (interactive shell,
    ``/ask``, ``/compact``, one-shot ``janito <prompt>``) feeds the
    ``accounting.db`` log, mirroring the web loop's own accounting.
    """
    if token_stats is None:
        return
    input_tokens = (
        token_stats.turn_input
        if token_stats.turn_input is not None
        else token_stats.last_input
    )
    cached_tokens = (
        token_stats.turn_cached
        if token_stats.turn_cached is not None
        else token_stats.last_cached
    )
    output_tokens = (
        token_stats.turn_output
        if token_stats.turn_output is not None
        else token_stats.last_output
    )
    cost = None
    if api_config.provider and api_config.model:
        cost = get_provider_cost_value(
            api_config.provider,
            api_config.model,
            input_tokens or 0,
            output_tokens or 0,
            cached_tokens or 0,
        )
    record_turn(
        api_config.provider,
        api_config.model,
        input_tokens,
        cached_tokens,
        output_tokens,
        cost=cost,
    )
