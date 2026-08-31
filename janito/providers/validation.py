"""
Provider name validation helpers.

Module-level functions for validating provider names, checking variants and
listing supported providers.  Part of the split provider-config module
family.
"""

from .registry import _registry


def canonical_provider_name(provider: str) -> str | None:
    """
    Return the canonical (correctly cased) name for a supported provider.

    Args:
        provider: The provider name (case-insensitive)

    Returns:
        The canonical provider name as used in the provider registry
        (``janito.providers._PROVIDER_CONFIGS``) if the provider is supported,
        otherwise ``None``.
    """
    return _registry.canonical_name(provider)


def is_supported_provider(provider: str) -> bool:
    """
    Check if a provider name is a supported provider (i.e. it maps to an entry
    in :data:`janito.providers._PROVIDER_CONFIGS`).

    Args:
        provider: The provider name (case-insensitive)

    Returns:
        True if the provider is supported, False otherwise
    """
    return _registry.canonical_name(provider) is not None


def list_variants() -> list:
    """List all registered provider variant names, sorted.

    Returns:
        Sorted list of registered variant names (e.g. ``["alibaba-tokenplan"]``).
    """
    # Accepted lazy cycle with the root config layer (issue #90): variant
    # listing reads the config store while the config layer validates
    # variant-style names through this module; the imports are lazy on both
    # sides, keeping the cycle contained.
    from ..config_variants import load_variants

    return sorted(load_variants().keys())


def is_custom_provider(provider: str) -> bool:
    """
    Check if a provider is the special "custom" provider.

    A provider variant of "custom" (e.g. ``custom-local``, created with
    ``--create-variant custom-local``) counts as custom too: it inherits the
    "custom" provider's built-in defaults.

    Args:
        provider: The provider name (case-insensitive)

    Returns:
        True if the provider is "custom" (or a variant of it), False otherwise
    """
    if not provider:
        return False
    if provider.strip().lower() == "custom":
        return True
    found = _registry.get(provider)
    return found.is_custom if found is not None else False


def validate_provider_name(provider: str) -> str:
    """
    Validate a provider name against the supported providers and return its
    canonical form.

    A provider is considered valid only if it maps to an entry in
    :data:`janito.providers._PROVIDER_CONFIGS`.

    Args:
        provider: The provider name to validate (case-insensitive)

    Returns:
        The canonical (correctly cased) provider name.

    Raises:
        ValueError: If the provider is not supported. The message enumerates
            the supported providers.
    """
    return _registry.require(provider).name


def validate_model_name(provider: str, model: str) -> str:
    """
    Validate a model name against the provider's built-in models and return
    its canonical form.

    The provider may be a registered provider variant (``<provider>-<word>``,
    e.g. ``alibaba-tokenplan``): the **base** provider's built-in models apply
    (the variant inherits them).  The ``custom`` and ``openrouter`` providers
    accept any model name -- they have no usable built-in model list to
    restrict selection to (see
    :meth:`janito.providers.models.Provider.has_usable_builtin_models`).

    A model matching a built-in entry -- or an already-configured per-model
    entry under ``providers.<provider>.models`` in config.json, the same set
    ``janito --list-models`` shows -- is returned in its canonical casing;
    anything else raises ``ValueError`` naming the provider and its available
    models.

    Args:
        provider: The provider (or variant) name (case-insensitive).
        model: The model name to validate.

    Returns:
        The canonical model name (the built-in/config entry's casing), or the
        name as typed for ``custom``/``openrouter``/unknown providers.

    Raises:
        ValueError: If the provider has usable built-in models and ``model``
            is not one of them (or a configured per-model entry).
    """
    # Accepted lazy cycle with the root config layer (issue #90): same
    # contained lazy cycle as list_variants -- the config layer validates
    # provider/model names through this module while this module reads the
    # config store for the configured entries.
    from ..config_keys import normalize_provider
    from ..config_store import get_config_value

    found = _registry.get(provider)
    if found is None or not found.has_usable_builtin_models():
        # Unknown provider, or custom/openrouter (no usable built-in list):
        # any model name is accepted.
        return model

    lowered = model.strip().lower()
    # Built-in models (the base provider's for variants).
    for name in found.model_names():
        if name.lower() == lowered:
            return name

    # Configured per-model entries (custom models with model-scoped settings
    # in config.json, e.g. --set max-output-tokens=...); these are the same
    # names --list-models shows, so they are accepted too.
    providers = get_config_value("providers")
    if isinstance(providers, dict):
        provider_config = providers.get(normalize_provider(provider))
        if isinstance(provider_config, dict):
            models = provider_config.get("models")
            if isinstance(models, dict):
                for name in models:
                    if name.lower() == lowered:
                        return name

    available = ", ".join(sorted(found.model_names(), key=str.lower))
    raise ValueError(
        f"Unknown model '{model}' for provider '{found.name}'. "
        f"Available models: {available}"
    )


def list_supported_providers() -> list:
    """
    List all supported providers.

    Returns:
        List of provider names
    """
    return _registry.names()


# ---------------------------------------------------------------------------
# API-type availability (optional packages per API type)
# ---------------------------------------------------------------------------


def get_all_api_types() -> list[str]:
    """
    List every canonical API type the CLI understands.

    The two OpenAI-SDK types (``"Responses"`` and ``"Completions"``) plus the
    keys of :data:`janito.providers.REQUIRES_BY_API_TYPE` (e.g. ``"Anthropic"``
    for the native Anthropic SDK). Used by ``normalize_api_type`` /
    ``--api-type`` validation and by the web API-type comboboxes.

    Returns:
        Sorted list of canonical API type names.
    """
    return sorted(set(("Responses", "Completions")) | set(_registry.requires))


def get_required_package_for_api_type(api_type: str) -> str | None:
    """
    Get the optional Python package required by an API type, if any.

    API types served by the OpenAI SDK (``"Responses"`` / ``"Completions"``)
    return ``None``: ``openai`` is a hard dependency. Native-SDK API types
    (e.g. ``"Anthropic"``) return the package that must be installed for them
    to work (see :data:`janito.providers.REQUIRES_BY_API_TYPE`).

    Args:
        api_type: The API type name (case-insensitive)

    Returns:
        The required package name, or ``None`` when the API type has no
        optional-package requirement (or is unknown).
    """
    if not api_type:
        return None
    api_type_lower = api_type.strip().lower()
    for key, package in _registry.requires.items():
        if key.lower() == api_type_lower:
            return package
    return None


def is_api_type_available(api_type: str) -> bool:
    """
    Check whether an API type's required package is installed.

    API types without an optional-package requirement (``Responses`` /
    ``Completions``) are always available.

    Args:
        api_type: The API type name (case-insensitive)

    Returns:
        ``True`` when the API type can be used (its required package is
        installed or it has no requirement), ``False`` otherwise.
    """
    package = get_required_package_for_api_type(api_type)
    if package is None:
        return True
    import importlib.util

    return importlib.util.find_spec(package) is not None


def ensure_api_type_available(api_type: str) -> None:
    """
    Abort with an actionable message when an API type's package is missing.

    Called when the user attempts to *set* an API type (``--set api-type=...``
    or the web Settings drawer). When the API type has no optional-package
    requirement, this is a no-op.

    Args:
        api_type: The canonical API type name (e.g. ``"Anthropic"``)

    Raises:
        ValueError: If the API type requires an optional package that is not
            installed. The message names the package and how to install it.
    """
    package = get_required_package_for_api_type(api_type)
    if package is None:
        return
    import importlib.util

    if importlib.util.find_spec(package) is None:
        raise ValueError(
            f"API type '{api_type}' requires the optional '{package}' package, "
            f"which is not installed. "
            f"Install it with: pip install {package}"
        )
