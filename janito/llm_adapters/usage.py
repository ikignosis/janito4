"""Shared token-usage normalization for the CLI and web agent loops.

Both loops report token usage at the end of a turn, but each API backend
names the counters differently: Chat Completions reports
``prompt_tokens``/``completion_tokens`` (with ``prompt_tokens_details``),
the Responses API reports ``input_tokens``/``output_tokens`` (with
``input_tokens_details``), and the native SDKs (Anthropic / DashScope) build
a ``SimpleNamespace`` with ``input_tokens``/``output_tokens`` and no
cached-token details.  :func:`normalize_usage` maps every shape onto one
dict; the CLI formats it as a Rich summary line
(``janito.ui.usage._display_usage``) and the web loop builds its own
``UsageEvent`` wire format on top of it (``janito.web.backend.events``).
"""

from dataclasses import dataclass
from typing import Any


def normalize_usage(usage: Any) -> dict[str, Any] | None:
    """Normalize any API usage object into ``{total, input, output, cached}``.

    ``None`` values are preserved (they mean "not reported") so callers can
    decide how to display each counter.  Returns ``None`` when ``usage``
    itself is ``None``.
    """
    if usage is None:
        return None
    # Already-normalized TurnInfo (the CLI turn report renders the turn
    # totals after the API call): pass the counters through unchanged.
    if isinstance(usage, TurnInfo):
        return {
            "total": usage.total,
            "input": usage.last_input,
            "output": usage.last_output,
            "cached": usage.last_cached,
        }
    details = getattr(usage, "prompt_tokens_details", None) or getattr(
        usage, "input_tokens_details", None
    )
    input_tokens = getattr(usage, "prompt_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage, "output_tokens", None)
    return {
        "total": getattr(usage, "total_tokens", None),
        "input": input_tokens,
        "output": output_tokens,
        "cached": getattr(details, "cached_tokens", None) if details else None,
    }


def format_tokens(count):
    """Convert a token count to a human-readable format.

    Examples:
        2000 -> "2k"
        4000000 -> "4m"
        150 -> "150"
        12345 -> "12.3k"
    """
    if count is None:
        return None
    try:
        value = float(count)
    except (TypeError, ValueError):
        return count

    def _format(number):
        # Trim trailing ".0" for whole numbers (e.g. "2.0k" -> "2k")
        if number == int(number):
            return str(int(number))
        return f"{number:.1f}"

    if value >= 1_000_000:
        return f"{_format(value / 1_000_000)}m"
    if value >= 1_000:
        return f"{_format(value / 1_000)}k"
    return str(int(value))


def format_elapsed(seconds: float | None) -> str | None:
    """Convert an elapsed duration in seconds to a human-readable format.

    Examples:
        12.34 -> "12.3s"
        125.0 -> "2m 05s"
        3725.0 -> "1h 02m 05s"
        0.0 -> "0.0s"
    """
    if seconds is None:
        return None
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{seconds:.1f}s"


def _add(a: int | None, b: int | None) -> int | None:
    """None-aware sum: ``None`` means "not reported", not zero."""
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


@dataclass
class TurnInfo:
    """Normalized token usage for one turn: final round + cumulative totals.

    ``total`` / ``last_input`` / ``last_output`` / ``last_cached`` mirror the
    **last** request of the turn (the one that produced the final answer),
    preserving the historical per-request usage summary.  ``turn_input`` /
    ``turn_cached`` / ``turn_output`` accumulate those counters across every
    request of the turn (tool-call rounds included), so a multi-round turn
    carries both the final round and the whole-turn picture.

    The object is built by both agent loops: :meth:`from_usage` seeds it
    from the first round that reports usage and :meth:`add_round` folds each
    following round into it.  The cumulative counters are surfaced on the
    final :class:`~janito.web.backend.events.UsageEvent` (web loop) and feed
    the CLI turn report's ``Cost`` estimate
    (``janito.ui.usage._display_usage``), which bills
    the turn-wide totals so tool-call rounds are included.

    The counters carry no provider/model/max-token metadata: ``Client.run_turn``
    pairs the populated instance with the resolved
    :class:`~janito.llm_clients.api_config.APIConfig` (provider / model /
    max tokens) when it hands both to the observer's ``on_turn_complete``,
    so the report's display metadata always comes from the session config.
    """

    total: int | None = None
    last_input: int | None = None
    last_output: int | None = None
    last_cached: int | None = None
    turn_input: int | None = None
    turn_cached: int | None = None
    turn_output: int | None = None
    elapsed_time: float | None = None

    @classmethod
    def from_usage(cls, usage: Any) -> "TurnInfo | None":
        """Build from one round's raw usage object (the first round of a turn).

        Returns ``None`` when the round reported no usage, so callers can
        keep the accumulator ``None`` until the first usable round arrives.
        """
        stats = normalize_usage(usage)
        if stats is None:
            return None
        return cls(
            total=stats["total"],
            last_input=stats["input"],
            last_output=stats["output"],
            last_cached=stats["cached"],
            turn_input=stats["input"],
            turn_cached=stats["cached"],
            turn_output=stats["output"],
        )

    def add_round(self, usage: Any) -> None:
        """Overwrite the last-round stats and accumulate the turn totals."""
        stats = normalize_usage(usage)
        if stats is None:
            return
        self.total = stats["total"]
        self.last_input = stats["input"]
        self.last_output = stats["output"]
        self.last_cached = stats["cached"]
        self.turn_input = _add(self.turn_input, stats["input"])
        self.turn_cached = _add(self.turn_cached, stats["cached"])
        self.turn_output = _add(self.turn_output, stats["output"])
