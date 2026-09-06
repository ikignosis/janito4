"""
/api_types command handler - lists the API types supported by each provider/model.

Usage:
    /api_types

Renders a table with one row per built-in model: the provider, the model
name, and the API types the model supports.  The API types come from the
model's ``supported_api_types`` entry (e.g. ``Responses`` / ``Completions``
plus native-SDK types such as ``Anthropic`` / ``DashScope`` / ``Gemini``)
via the typed provider accessor (``Provider.supported_api_types``);
the model's built-in default API type (its ``default_api_type`` entry, via
``Provider.default_api_type``) is
marked ``(default)``.  Models without a built-in entry (e.g. the ``custom``
provider's) show ``(none)``.
"""

from rich.console import Console
from rich.table import Table

from .base import CmdHandler
from .registry import register_command


class ApiTypesCmdHandler(CmdHandler):
    """Command handler for /api_types command."""

    @property
    def name(self) -> str:
        return "/api_types"

    @property
    def description(self) -> str:
        return "List the API types supported by each provider/model"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /api_types command."""
        if user_input.strip().lower() == self.name.lower():
            self._show_api_types()
            return True
        return False

    @staticmethod
    def _show_api_types() -> None:
        """Print a per-model API-type table for every built-in model."""
        from janito.providers.registry import get_provider
        from janito.providers.validation import list_supported_providers

        table = Table(
            title="API Types by Provider/Model",
            title_style="bold",
            header_style="bold cyan",
            box=None,
            pad_edge=False,
        )
        table.add_column("Provider", style="green", no_wrap=True)
        table.add_column("Model", style="cyan", no_wrap=True)
        table.add_column("Supported API Types")

        for provider in list_supported_providers():
            found = get_provider(provider)
            if found is None:
                continue
            for model in sorted(found.model_names()):
                api_types = found.model_config(model).get("supported_api_types") or []
                default_api_type = found.model_config(model).get("default_api_type")
                if api_types:
                    display = ", ".join(
                        f"{api_type} (default)" if api_type == default_api_type else api_type for api_type in api_types
                    )
                else:
                    display = "(none)"
                table.add_row(provider, model, display)

        Console(markup=False).print(table)


# Register this handler
_handler = ApiTypesCmdHandler()
register_command(_handler)
