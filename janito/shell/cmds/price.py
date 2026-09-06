"""
/price command handler - displays per-model pricing for every built-in model.

Usage:
    /price

Renders a table with one row per built-in model: the provider, the model
name, and four cost columns for a notional request of **1M input tokens
(cache miss) + 1M cached input tokens + 1M output tokens** -- ``1M in``,
``1M cache``, ``1M output`` and their ``Total`` -- sorted by total cost from
max to min.  Each component column is computed by the provider's cost module
(``janito.providers.<name>.cost``, the ``cost_*`` rate tables) via
:func:`janito.providers.costing.get_provider_cost` with ``is_reference=True``,
so reference (e.g. peak) rates apply and the returned string carries no
rate-band suffix (e.g. DeepSeek's ``(off-peak)``/``(peak)`` annotation).  The
``Total`` column is the exact dollar sum of the three components (rendered
with :func:`janito.providers.costing.format_cost`), so it always equals the
column sum.  Providers/models without a cost module show ``N/A`` in every
cost column.
"""

from rich.console import Console
from rich.table import Table

from .base import CmdHandler
from .registry import register_command

#: The notional usage behind each price column: 1M input tokens (cache miss),
#: 1M cached input tokens and 1M output tokens.
_MILLION = 1_000_000


def _parse_cost(cost_str: str) -> float:
    """Parse a cost string into dollars for sorting; returns -inf for N/A or invalid.

    Handles both the adaptive display formats (``X.a$``, ``X.a\u00a2``,
    ``0.abc\u00a2``, ``X$``) and the legacy raw dollar strings, optionally
    followed by a rate-band annotation such as ``(off-peak)``.
    """
    try:
        cleaned = cost_str.split()[0]
        if cleaned.endswith("\u00a2"):
            return float(cleaned[:-1]) / 100
        return float(cleaned.rstrip("$"))
    except (ValueError, IndexError, AttributeError):
        return float("-inf")


class PriceCmdHandler(CmdHandler):
    """Command handler for /price command."""

    @property
    def name(self) -> str:
        return "/price"

    @property
    def description(self) -> str:
        return "Show per-model pricing for every built-in model"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /price command."""
        if user_input.strip().lower() == self.name.lower():
            self._show_prices()
            return True
        return False

    @staticmethod
    def _show_prices() -> None:
        """Print a per-model pricing table for every built-in model."""
        from janito.providers.costing import (
            format_cost,
            get_provider_cost,
            get_provider_cost_value,
        )
        from janito.providers.registry import get_provider
        from janito.providers.validation import list_supported_providers

        table = Table(
            title="Model Pricing (per 1M tokens)",
            title_style="bold",
            header_style="bold cyan",
            box=None,
            pad_edge=False,
        )
        table.add_column("Provider", style="green", no_wrap=True)
        table.add_column("Model", style="cyan", no_wrap=True)
        table.add_column("1M in", justify="right", no_wrap=True)
        table.add_column("1M cache", justify="right", no_wrap=True)
        table.add_column("1M output", justify="right", no_wrap=True)
        table.add_column("Total", justify="right", no_wrap=True)

        rows: list[tuple[str, str, str, str, str, str]] = []
        for provider in list_supported_providers():
            found = get_provider(provider)
            if found is None:
                continue
            for model in sorted(found.model_names()):
                cost_in = get_provider_cost(provider, model, _MILLION, 0, 0, is_reference=True)
                cost_cache = get_provider_cost(provider, model, _MILLION, 0, _MILLION, is_reference=True)
                cost_output = get_provider_cost(provider, model, 0, _MILLION, 0, is_reference=True)
                values = [
                    get_provider_cost_value(provider, model, _MILLION, 0, 0, is_reference=True),
                    get_provider_cost_value(provider, model, _MILLION, 0, _MILLION, is_reference=True),
                    get_provider_cost_value(provider, model, 0, _MILLION, 0, is_reference=True),
                ]
                if all(value is not None for value in values):
                    total = format_cost(sum(values))
                else:
                    total = "N/A"
                rows.append((provider, model, cost_in, cost_cache, cost_output, total))

        rows.sort(key=lambda item: _parse_cost(item[5]), reverse=True)

        for provider, model, cost_in, cost_cache, cost_output, total in rows:
            table.add_row(provider, model, cost_in, cost_cache, cost_output, total)
        Console(markup=False).print(table)


# Register this handler
_handler = PriceCmdHandler()
register_command(_handler)
