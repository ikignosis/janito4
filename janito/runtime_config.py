"""Runtime configuration resolution (provider, api key, endpoint, model).

Hosts :func:`resolve_runtime_config`, the single place that resolves the
runtime ``(base_url, api_key, model)`` triple from the auth store
(``~/.janito/auth.json``) and the config file (``~/.janito/config.json``)
without relying on ``OPENAI_*`` environment variables.

This is a **config-layer** module: it reads the auth/config stores and the
provider registry, and it must never be imported by ``janito.llm_clients``
-- the clients consume the already-resolved values through the frozen
:class:`~janito.llm_clients.api_config.APIConfig` (built once per session by
``build_api_config``, which delegates its config/auth-store reads to this
module).  Other early-validation callers (the CLI setup check, the chat
model display, the web agent loop) resolve the triple here too.
"""

import logging

from .auth_config import get_api_key
from .config_loaders import load_endpoint_from_config, load_model_from_config
from .general_config import load_provider_from_config
from .provider_accessors import get_default_model_from_provider, requires_explicit_model
from .provider_validation import is_custom_provider

# Configure logger for this module
logger = logging.getLogger(__name__)


def resolve_runtime_config(
    cli_model: str | None = None,
    cli_provider: str | None = None,
    cli_api_type: str | None = None,
) -> tuple[str | None, str, str]:
    """
    Resolve the runtime configuration (base_url, api_key, model) without
    relying on OPENAI_* environment variables.

    Resolution rules:
      - api_key:  taken from the auth store (~/.janito/auth.json) for the
                  active provider (see ``auth_config.get_api_key``).
      - base_url: the endpoint configured for the provider (``--set endpoint``)
                  or, when none is set, the provider's built-in default base
                  URL resolved for the effective API type (see
                  ``provider_accessors.get_endpoint_for_api_type``, honoring the
                  provider's ``endpoint_by_api_type`` map). ``None`` means the
                  standard OpenAI endpoint.
      - model:    ``--model`` (``cli_model``) when given, otherwise the model
                  configured for the active provider (``<provider>.model``),
                  and finally the provider's built-in default model.  A
                  provider whose built-in default is the ``"custom"``
                  placeholder (e.g. ``openrouter``) has no usable default --
                  the placeholder only carries built-in defaults such as the
                  default API type -- so the user must supply the model
                  explicitly (``--model`` or ``<provider>.model``) and an
                  unresolvable model is reported as an error.

    Args:
        cli_model: Model passed via ``--model`` (highest priority). May be None.
        cli_provider: Provider passed via ``--provider``. May be None.
        cli_api_type: API type passed via ``--api-type`` (or implied by the
            selected client, e.g. ``"Anthropic"`` for the native Anthropic
            SDK). Used to pick the built-in default endpoint when the provider
            declares ``endpoint_by_api_type``. May be None.

    Returns:
        Tuple of (base_url, api_key, model). ``base_url`` may be None for the
        standard OpenAI API.

    Raises:
        ValueError: If the API key or model cannot be resolved, or if a custom
            provider has no endpoint configured.
    """
    # Provider: --provider CLI arg, then config.json.  The default provider
    # is stored under the ``provider`` key in config.json -- never in
    # auth.json.  If none of these is set, report that no provider is
    # configured rather than silently assuming "openai".
    provider = cli_provider or load_provider_from_config()
    if not provider:
        logger.error("No provider configured")
        raise ValueError(
            "No provider is configured. "
            "Set one with: janito --set provider=<name> (e.g. janito --set provider=alibaba) "
            "or pass --provider <name>."
        )
    logger.debug(f"Resolving runtime config for provider: {provider}")

    # API key from the auth store (no environment variables).
    api_key = get_api_key(provider)
    if not api_key:
        logger.error(f"No API key configured for provider '{provider}'")
        raise ValueError(
            f"No API key configured for provider '{provider}'. "
            f"Set one with: janito --set-api-key <key> --provider {provider}"
        )

    # Model: --model, then the provider's configured model, and finally the
    # provider's built-in default model (from the provider config).  A
    # provider whose built-in default is the "custom" placeholder (e.g.
    # "openrouter") has no usable default: the placeholder "custom" model
    # entry only carries built-in defaults (the default API type), so the
    # user must supply the model explicitly (--model or <provider>.model in
    # config.json).  When it cannot be resolved, report it here instead of
    # silently sending the placeholder to the API.
    model = cli_model or load_model_from_config(provider)
    if not model:
        model = get_default_model_from_provider(provider)
        if model and requires_explicit_model(provider):
            model = None
    if not model:
        logger.error(f"No model configured for provider '{provider}'")
        raise ValueError(
            f"No model configured for provider '{provider}'. "
            f"Pass --model <name> or set it with: "
            f"janito --provider {provider} --set model=<name>"
        )

    # Base URL: configured endpoint for the provider, otherwise the provider's
    # built-in default resolved for the effective API type (None for standard
    # OpenAI). The effective API type comes from --api-type, then the
    # provider's configured api-type, then its built-in default.
    base_url = load_endpoint_from_config(provider)
    if not base_url:
        if is_custom_provider(provider):
            logger.warning(f"Custom provider '{provider}' has no endpoint configured")
            raise ValueError(
                f"Provider '{provider}' requires an endpoint. "
                f"Set it with: janito --provider {provider} --set endpoint=<url>"
            )
        from .general_config import resolve_api_type
        from .provider_accessors import get_endpoint_for_api_type

        api_type = resolve_api_type(cli_api_type, provider)
        base_url = get_endpoint_for_api_type(provider, api_type)

    logger.debug(f"Runtime config resolved: base_url={base_url}, model={model}")
    return base_url, api_key, model


__all__ = [
    "resolve_runtime_config",
]
