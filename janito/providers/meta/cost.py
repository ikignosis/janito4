"""Cost estimation for the Meta provider (Meta Model API).

Rates source
------------
The per-1M-token rates below are based on the official Meta Model API
pricing published on the Muse Spark model page
(https://developer.meta.com/ai/models/muse-spark/) and apply as of the
verification date.  Meta bills pay-as-you-go per million tokens, with a
discounted cached-input read rate.

Standard and contributor tiers
------------------------------
``muse-spark-1.3`` is the standard tier; ``muse-spark-1.3-contributor`` is
the cheaper contributor tier (its data is used to improve Meta's
products).  The two tiers bill at very different rates, so both are
declared here:

===========  ============  ==========  ==========
model        input (1M)    cached(1M)  output(1M)
===========  ============  ==========  ==========
muse-spark-1.3           $1.25      $0.15        $4.25
muse-spark-1.3-contributor  $0.10   $0.002       $0.20
===========  ============  ==========  ==========
"""

#: Per-1M-token rates (USD) keyed by model name:
#: ``(input cache miss, input cache hit, output)``.
_MODEL_RATES: dict[str, tuple[float, float, float]] = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
}


def get_cost(
    model: str,
    input: int,
    output: int,
    cached: int,
    is_reference: bool = False,
) -> str:
    """Estimate the monetary cost of a request in dollars.

    Args:
        model: The model name used for the request.
        input: The number of input tokens.
        output: The number of output tokens.
        cached: The number of cached input tokens (cache reads).
        is_reference: Marks the request as a reference request (e.g. tokens
            from attached reference documents). Passed through for future
            reference-token billing; currently the estimate is unchanged.

    Returns:
        The estimated cost formatted as a dollar string with six decimal
        digits (e.g. ``"1.500000$"``, or ``"N/A"`` for an unknown model.
    """
    rates = _MODEL_RATES.get(model)
    if rates is None:
        return "N/A"
    input_miss, input_hit, output_rate = rates
    cost = ((input - cached) * input_miss + cached * input_hit + output * output_rate) / 1_000_000
    return f"{cost:.6f}$"
