"""
Provider name validation helpers.

Module-level functions for validating provider names, checking variants and
listing supported providers.  Part of the split provider-config module
family.
"""

from .provider_registry import _registry


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


def is_registered_provider_variant(name: str) -> bool:
    """Whether ``name`` is a registered provider variant (not a base provider).

    Unlike :func:`is_supported_provider` (which also accepts built-in
    providers), this only returns True for registered variants.

    Args:
        name: The provider name.

    Returns:
        True if the name is a registered variant.
    """
    return _registry._variant_base(name) is not None


def list_variants() -> list:
    """List all registered provider variant names, sorted.

    Returns:
        Sorted list of registered variant names (e.g. ``["alibaba-tokenplan"]``).
    """
    from .config_variants import load_variants

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
    :meth:`janito.provider_models.Provider.has_usable_builtin_models`).

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
    from .config_keys import normalize_provider
    from .config_store import get_config_value

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
