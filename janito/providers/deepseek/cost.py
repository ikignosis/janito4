"""Cost estimation for the DeepSeek provider.

Rates source
------------
The per-1M-token rates below were taken from the official DeepSeek rate
card at https://api-docs.deepseek.com/quick_start/pricing (last verified
2026-08-16) and apply as of the verification date.  DeepSeek adjusts
figures frequently, so cross-check that page before relying on them.

Peak/off-peak
-------------
DeepSeek bills requests made during peak hours at exactly double the
off-peak rates.  Peak hours are 01:00-04:00 and 06:00-10:00 UTC (all
other hours are off-peak), so the estimate applies the off-peak rates
outside those windows and double rates inside them.

The peak/off-peak split only applies on weekdays (Monday to Friday in
Beijing Time).  On weekends (Saturday and Sunday in Beijing Time) the
peak-hour divisions no longer apply: every call is charged uniformly at
the off-peak rate for the whole day.

Reference requests
------------------
Reference requests (``is_reference=True``, e.g. tokens from attached
reference documents) are billed at the peak rates regardless of the
request time and day, and the returned cost string does not carry the
peak/off-peak suffix.
"""

from datetime import datetime, time, timedelta, timezone

#: Off-peak per-1M-token rates (USD) keyed by model name:
#: ``(input cache miss, input cache hit, output)``.  Peak rates are
#: exactly double these (per the official rate card, off-peak rates are
#: half of the peak rates).
#:
#: DeepSeek applies automatic prefix caching: repeated input tokens (a
#: stable system prompt, a long document, few-shot examples) are billed at
#: the much lower cache-hit rate instead of the cache-miss rate.
_MODEL_RATES: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.22, 0.007, 0.66),
    "deepseek-v4-pro": (0.66, 0.022, 1.98),
}

#: Peak-hour windows in UTC as half-open intervals (``[start, end)``).
_PEAK_WINDOWS: tuple[tuple[time, time], ...] = (
    (time(hour=1), time(hour=4)),
    (time(hour=6), time(hour=10)),
)

#: Beijing timezone (UTC+8): the weekday/weekend split is defined in
#: Beijing Time (no DST).
_BEIJING_TZ = timezone(timedelta(hours=8))


def _utcnow() -> datetime:
    """Return the current UTC time (separable so tests can pin it)."""
    return datetime.now(timezone.utc)


def _is_peak_hour(now: datetime) -> bool:
    """True when ``now`` falls inside a DeepSeek peak-hour window (UTC)."""
    current = now.astimezone(timezone.utc).time()
    return any(start <= current < end for start, end in _PEAK_WINDOWS)


def _is_weekend(now: datetime) -> bool:
    """True when ``now`` falls on a weekend (Saturday/Sunday) in Beijing Time.

    The request is converted to Beijing Time (UTC+8) before checking the
    day of the week, so the weekend window follows the Beijing calendar.
    """
    beijing = now.astimezone(timezone.utc).astimezone(_BEIJING_TZ)
    return beijing.weekday() >= 5  # weekday(): 5=Saturday, 6=Sunday


def get_cost(
    model: str,
    input: int,
    output: int,
    cached: int,
    now: datetime | None = None,
    is_reference: bool = False,
) -> str:
    """Estimate the monetary cost of a request in dollars.

    Args:
        model: The model name used for the request.
        input: The number of input tokens.
        output: The number of output tokens.
        cached: The number of cached input tokens.
        now: The request time used to pick the peak/off-peak rates; when
            omitted the current UTC time is used.  On weekends (Saturday
            and Sunday in Beijing Time) the peak-hour divisions do not
            apply and the off-peak rates are used for the whole day.
        is_reference: Marks the request as a reference request (e.g. tokens
            from attached reference documents).  Reference requests are
            billed at the peak rates regardless of the request time and
            day, and the returned string does not carry the rate-band
            suffix.

    Returns:
        The estimated cost formatted as a dollar string with six decimal
        digits followed by the applied rate band for regular requests, e.g.
        ``"0.880000$ (off-peak)"`` or ``"1.760000$ (peak)"``.  Reference
        requests omit the rate band, e.g. ``"1.760000$"``.  ``"N/A"`` for an
        unknown model.
    """
    rates = _MODEL_RATES.get(model)
    if rates is None:
        return "N/A"
    input_miss, input_hit, output_rate = rates
    now = _utcnow() if now is None else now
    peak = is_reference or (not _is_weekend(now) and _is_peak_hour(now))
    multiplier = 2.0 if peak else 1.0
    cost = (
        ((input - cached) * input_miss + cached * input_hit + output * output_rate)
        / 1_000_000
        * multiplier
    )
    if is_reference:
        return f"{cost:.6f}$"
    return f"{cost:.6f}$ ({'peak' if peak else 'off-peak'})"
