"""
/status command handler - displays current configuration.
"""

from janito.auth_config import get_api_key

# Import general configuration handling
from janito.config_keys import get_masked_api_key
from janito.config_loaders import (
    load_endpoint_from_config,
    load_max_output_tokens,
    load_model_from_config,
    load_reasoning_level,
)
from janito.general_config import get_active_provider, resolve_api_type
from janito.provider_accessors import (
    format_thinking_display,
    get_default_max_output_tokens_from_provider,
    get_default_model_from_provider,
    get_default_reasoning_level_from_provider,
    get_default_thinking_from_provider,
    get_gemini_flavor_from_provider,
    get_responses_in_server_from_provider,
)

from .base import CmdHandler
from .registry import register_command


def _resolve_effective_model(
    provider: str, model: str | None
) -> tuple[str | None, bool]:
    """Resolve the effective model for the session and whether it is a default.

    The session's model (``--model``, ``/model`` or the startup resolution)
    wins; otherwise the provider's configured model; otherwise its built-in
    default model (``None`` e.g. for "custom").

    Returns:
        Tuple ``(model, is_default)``. ``is_default`` is True only when the
        built-in default was used (no session model, nothing configured), so
        the Model row can be marked with ``(default)``.
    """
    if model:
        return model, False
    configured = load_model_from_config(provider)
    if configured:
        return configured, False
    default = get_default_model_from_provider(provider)
    return default, default is not None


def _print_config_info(
    provider: str | None = None,
    thinking: bool = False,
    api_type: str | None = None,
    model: str | None = None,
) -> None:
    """Print current configuration info (provider, model, base_url, masked API key, max output tokens).

    Model-level settings (API type, max output tokens, reasoning level,
    thinking, Responses-in-server) are resolved for the *effective model*:
    the session's model, else the provider's configured model, else its
    built-in default model.

    Args:
        provider: The provider in effect for the current shell session (e.g.
            from ``--provider``). When None, falls back to the configured
            default provider.
        thinking: The ``--thinking`` CLI flag for the session. The effective
            thinking mode also considers the effective model's built-in
            default (True for DeepSeek and Alibaba/Qwen).
        api_type: The ``--api-type`` CLI flag for the session (e.g. ``Gemini``,
            ``Responses``, ``Completions``), or None when it was not given.
            Forwarded to ``resolve_api_type`` so the display reports the API
            type the session actually uses: the CLI flag first, then the
            model-scoped configured value (``--set api-type=...``), then the
            effective model's built-in default.
        model: The session's effective model (from ``--model``, ``/model`` or
            the startup resolution). When None, the provider's configured
            model, else its built-in default model is used and the Model row
            marks the built-in default with ``(default)``.
    """
    if provider is None:
        provider = get_active_provider()
    api_key = get_api_key(provider) or ""
    masked_key = get_masked_api_key(api_key)

    # Effective model for the session: the shell's model when given, else the
    # provider's configured model, else its built-in default model (None e.g.
    # for "custom").  Only the built-in default (no session model, nothing
    # configured) is marked '(default)' in the display.
    model, model_default = _resolve_effective_model(provider, model)

    max_output_tokens = load_max_output_tokens(provider, model)

    # Resolve the effective API type first (--api-type, otherwise the
    # model-scoped configured value --set api-type=..., otherwise the
    # effective model's built-in default -- its default_api_type entry) so
    # the built-in base URL can be resolved per API type
    # (endpoint_by_api_type, e.g. Anthropic's native-SDK URL).
    api_type = resolve_api_type(api_type, provider, model)

    # Determine the actual base URL that will be used: a configured endpoint
    # override first, otherwise the provider's built-in default for the
    # effective API type.
    base_url = load_endpoint_from_config(provider)
    if not base_url:
        from janito.provider_accessors import get_endpoint_for_api_type

        base_url = get_endpoint_for_api_type(provider, api_type)

    if base_url:
        base_url_display = base_url
    else:
        base_url_display = "(default OpenAI URL)"

    # Resolve the effective max output tokens: an explicit configuration
    # value first, otherwise the effective model's built-in default from
    # the provider config.
    if max_output_tokens:
        max_output_tokens_display = str(max_output_tokens)
    else:
        default_max_output_tokens = get_default_max_output_tokens_from_provider(
            provider, model
        )
        max_output_tokens_display = (
            f"{default_max_output_tokens} (default)"
            if default_max_output_tokens
            else "(not set)"
        )

    # Resolve the effective reasoning level: an explicit configuration value
    # first, otherwise the effective model's built-in default from
    # the provider config.
    reasoning_level = load_reasoning_level(provider, model)
    if reasoning_level:
        reasoning_level_display = reasoning_level
    else:
        default_reasoning_level = get_default_reasoning_level_from_provider(
            provider, model
        )
        reasoning_level_display = (
            f"{default_reasoning_level} (default)"
            if default_reasoning_level
            else "(not set)"
        )

    # Resolve the effective thinking mode: the --thinking flag first,
    # otherwise the effective model's built-in default from the provider
    # config
    # (True for DeepSeek/Alibaba-Qwen; a pass-through dict such as
    # {'type': 'adaptive'} for MiniMax-M3).
    effective_thinking = thinking or get_default_thinking_from_provider(provider, model)
    thinking_display = format_thinking_display(effective_thinking, provider=provider)
    if (
        effective_thinking
        and not thinking
        and not (provider and get_gemini_flavor_from_provider(provider))
    ):
        thinking_display += " (model default)"

    # When the effective API type is the Responses API, surface whether the
    # model keeps the conversation state server-side (chained with
    # previous_response_id) or serves a stateless /responses endpoint (the
    # client re-sends the full history on every request, e.g. DeepSeek).
    responses_in_server_display = ""
    if api_type == "Responses":
        if get_responses_in_server_from_provider(provider, model):
            responses_in_server_display = "server-side (previous_response_id)"
        else:
            responses_in_server_display = "stateless (client re-sends history)"

    from rich.console import Console
    from rich.table import Table

    table = Table(
        title="Configuration Info",
        title_style="bold",
        header_style="bold cyan",
        show_header=False,
        box=None,
        pad_edge=False,
    )
    table.add_column("Key", style="green", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_row("Provider", provider)
    table.add_row(
        "Model",
        f"{model} (default)" if model_default else (model or "(not set)"),
    )
    table.add_row("API Type", api_type)
    if responses_in_server_display:
        table.add_row("Responses In Server", responses_in_server_display)
    table.add_row("Base URL", base_url_display)
    table.add_row("API Key", masked_key)
    table.add_row("Max Output Tokens", max_output_tokens_display)
    table.add_row("Reasoning Level", reasoning_level_display)
    table.add_row("Thinking", thinking_display)
    Console(markup=False).print(table)


class StatusCmdHandler(CmdHandler):
    """Command handler for /status command."""

    @property
    def name(self) -> str:
        return "/status"

    @property
    def description(self) -> str:
        return "Show the resolved runtime configuration"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /status command."""
        if user_input.lower() == self.name.lower():
            _print_config_info(
                getattr(shell, "provider", None),
                getattr(shell, "thinking", False),
                getattr(shell, "api_type", None),
                getattr(shell, "model", None),
            )
            return True
        return False


# Register this handler
_handler = StatusCmdHandler()
register_command(_handler)
