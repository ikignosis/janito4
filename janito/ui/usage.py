"""Token-usage summary line and the end-of-turn report (used files + usage).

Rendered by the CLI's ``RichTurnObserver.on_turn_complete`` with the
client-built :class:`~janito.llm_adapters.usage.TokenStats` and the turn's resolved
:class:`~janito.llm_clients.api_config.APIConfig`.  Pure presentation: the
token counters are normalized by :func:`janito.llm_adapters.usage.normalize_usage`
and the cost estimate comes from ``janito.providers.costing``.
"""

import logging
from typing import Any

from rich.console import Console
from rich.text import Text

from janito.config_loaders import load_used_files_enabled
from janito.llm_adapters.usage import TokenStats, format_tokens, normalize_usage
from janito.providers.costing import get_provider_cost
from janito.tooling.used_files import format_used_files

from ..llm_clients.api_config import APIConfig

logger = logging.getLogger(__name__)


def _print_input_capacity_warning(
    max_input_tokens: int | None,
    input_tokens: int | None,
    console: Console,
) -> None:
    """Warn (bold yellow) when input tokens exceed 80% of the model capacity."""
    if (
        max_input_tokens is not None
        and input_tokens is not None
        and input_tokens > 0.8 * max_input_tokens
    ):
        console.print(
            "Reached 80% of input capacity, consider running /compact or /clear",
            style="bold yellow",
            highlight=False,
        )


def _cost_counters(
    usage_info: Any,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None,
) -> tuple[int | None, int | None, int | None]:
    """Return the token counters billed for the ``Cost`` estimate.

    A :class:`~janito.llm_adapters.usage.TokenStats` (the turn report) bills the
    turn-wide cumulative counters (``turn_input`` / ``turn_output`` /
    ``turn_cached``) so tool-call rounds are included; any other usage shape
    falls back to the final round's counters.
    """
    if isinstance(usage_info, TokenStats):
        return (
            usage_info.turn_input,
            usage_info.turn_output,
            usage_info.turn_cached,
        )
    return input_tokens, output_tokens, cached_tokens


def _display_usage(
    usage_info: Any,
    max_input_tokens: int | None,
    max_output_tokens: int | None,
    console: Console,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Print the token usage summary line.

    The token attribute names differ per API (Chat Completions reports
    ``prompt_tokens``/``completion_tokens`` with ``prompt_tokens_details``,
    the Responses API reports ``input_tokens``/``output_tokens`` with
    ``input_tokens_details``, and the native SDKs build a ``SimpleNamespace``
    with ``input_tokens``/``output_tokens`` and no cached-token details).
    The shared :func:`normalize_usage` maps every shape onto one dict -- the
    ``cached`` counter is ``None`` for APIs that do not report cached-token
    details, so the cached part is shown only when the API actually reports
    it.

    ``Cost: <cost>`` is computed through
    :func:`janito.providers.costing.get_provider_cost` from the provider /
    model and the token counts (cached input tokens are billed at the
    provider's cache-hit rate); it falls back to ``N/A`` when the provider
    or model is unknown, or when no cost module exists for the provider.
    The estimate is rendered with an adaptive, magnitude-aware format
    (issue #67), e.g. ``88.0¢ (off-peak)`` / ``1.2$`` / ``0.012¢``.
    When the usage is a :class:`~janito.llm_adapters.usage.TokenStats` (the turn
    report), the cost is billed against the turn-wide cumulative counters
    (``turn_input`` / ``turn_output`` / ``turn_cached``) so tool-call
    rounds are included; otherwise the final round's counters are used.

    When the input tokens exceed 80% of ``max_input_tokens`` a warning in
    the warning color (``bold yellow``) is printed just before the summary
    line, nudging the user to run ``/compact`` or ``/clear``.
    """
    stats = normalize_usage(usage_info)
    if stats is None:
        return
    total_tokens = stats["total"]
    input_tokens = stats["input"]
    output_tokens = stats["output"]
    cached_tokens = stats["cached"]
    cost_input, cost_output, cost_cached = _cost_counters(
        usage_info, input_tokens, output_tokens, cached_tokens
    )

    parts = []
    if total_tokens is not None:
        parts.append(f"Total: {format_tokens(total_tokens)}")
    if input_tokens is not None:
        if max_input_tokens is not None:
            parts.append(
                f"In: {format_tokens(input_tokens)}/{format_tokens(max_input_tokens)}"
            )
        else:
            parts.append(f"In: {format_tokens(input_tokens)}")
    if output_tokens is not None:
        if max_output_tokens is not None:
            parts.append(
                f"Out: {format_tokens(output_tokens)}/{format_tokens(max_output_tokens)}"
            )
        else:
            parts.append(f"Out: {format_tokens(output_tokens)}")
    if cached_tokens is not None:
        parts.append(f"Cached: {format_tokens(cached_tokens)}")
    if provider is not None and model is not None:
        cost = get_provider_cost(
            provider,
            model,
            cost_input if cost_input is not None else 0,
            cost_output if cost_output is not None else 0,
            cost_cached if cost_cached is not None else 0,
        )
    else:
        cost = "N/A"
    parts.append(f"Cost: {cost}")

    _print_input_capacity_warning(max_input_tokens, input_tokens, console)

    token_text = Text(f"=== {' | '.join(parts)} ===")
    token_text.stylize("bright_white on magenta")
    console.print(token_text, highlight=False)
    logger.info(
        f"Request completed: total={total_tokens} tokens "
        f"(in={input_tokens}, out={output_tokens}, "
        f"cached={cached_tokens}, max={max_output_tokens})"
    )


def display_turn_usage(
    usage_out: TokenStats | None,
    api_config: APIConfig,
    *,
    console: Console | None = None,
) -> None:
    """Print the end-of-turn reports (used files + token usage summary).

    Rendered by the CLI's ``RichTurnObserver.on_turn_complete`` -- which
    ``Client.run_turn`` invokes at the end of every turn -- with the
    client-built :class:`~janito.llm_adapters.usage.TokenStats` (every round's usage
    folded into it) and the turn's resolved
    :class:`~janito.llm_clients.api_config.APIConfig`, whose
    ``provider`` / ``model`` / ``max_input_tokens`` / ``max_output_tokens``
    feed the summary line.  Replaces the reports the per-client ``_finalize``
    helpers used to print inline: the tracked used files first, then the
    magenta token-usage summary line.  Nothing is printed when no usage was
    reported (``usage_out`` is ``None``).
    """
    console = console or Console()

    # Display the tracked used files before the token usage summary (only
    # when the ``used-files`` config flag is enabled -- default False, so the
    # report is opt-in, issue #74). Nothing is printed when no files were
    # tracked (empty Text) either.
    if load_used_files_enabled():
        used_files_report = format_used_files()
        if used_files_report:
            console.print(used_files_report, highlight=False)

    if usage_out is None:
        return

    _display_usage(
        usage_out,
        api_config.max_input_tokens,
        api_config.max_output_tokens,
        console,
        provider=api_config.provider,
        model=api_config.model,
    )
