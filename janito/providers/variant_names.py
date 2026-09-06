"""Provider-name helpers with no intra-package dependencies.

This is a leaf module: it imports only from the standard library and the
leaf :mod:`janito.config_dir` (config-dir resolution, itself stdlib-only).
Nothing in here may import from ``janito.config_*`` or the rest of
``janito.providers`` -- that is what keeps the config <-> providers import
cycle broken (issue #110).  Both the config layer and the provider registry
import *from* here, never the other way round.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config_dir import get_config_file_paths

logger = logging.getLogger(__name__)


def parse_variant_name(name: str | None) -> tuple[str, str] | None:
    """Split a variant-style name into its ``(base_provider, word)`` parts.

    A variant name follows the syntax ``<provider>-<word>``; the split
    happens on the **first** hyphen, so the word may itself contain hyphens
    (e.g. ``alibaba-token-plan`` -> ``("alibaba", "token-plan")``).

    Args:
        name: The raw name.

    Returns:
        A ``(base, word)`` tuple with both parts non-empty (stripped), or
        ``None`` when the name is not in ``<provider>-<word>`` form.
    """
    if not name:
        return None
    parts = str(name).split("-", 1)
    if len(parts) != 2:
        return None
    base, word = parts[0].strip(), parts[1].strip()
    if not base or not word:
        return None
    return base, word


def is_variant_style_name(name: str | None) -> bool:
    """Whether ``name`` looks like a provider variant (``<provider>-<word>``).

    This only checks the shape; the variant need not be registered.

    Args:
        name: The provider name.

    Returns:
        True if the name matches the ``<provider>-<word>`` syntax.
    """
    return parse_variant_name(name) is not None


def normalize_provider(provider: str | None) -> str | None:
    """Normalize a provider name for use as a config key prefix.

    Args:
        provider: The raw provider name (may be None)

    Returns:
        The lowercased/stripped provider name, or None if empty/None
    """
    if not provider:
        return None
    normalized = provider.strip().lower()
    return normalized or None


def read_providers_map() -> dict[str, Any]:
    """Read the merged ``providers`` map from config.json.

    Merges the resolution chain (project-local over base when ``-l`` /
    ``--local`` is active) the same way :class:`ConfigStore.load` does, but
    only for the top-level ``providers`` keys -- enough for membership
    checks (variant registration, configured per-model entries) without
    importing the config-store layer.

    Returns:
        The merged ``providers`` mapping, or an empty dict when no config
        file exists or is invalid.
    """
    merged: dict[str, Any] = {}
    # get_config_file_paths() returns highest priority first; iterate in
    # reverse so local entries override global ones.
    for path in reversed(get_config_file_paths("config.json")):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            logger.debug("Skipping unreadable config file %s: %s", path, exc)
            continue
        providers = data.get("providers")
        if isinstance(providers, dict):
            merged.update(providers)
    return merged


def registered_variant_names() -> set[str]:
    """Return the registered provider-variant names from config.json.

    Variants are the ``<provider>-<word>`` entries of the ``providers`` map
    (registered with ``janito --create-variant``).

    Returns:
        The set of registered variant names.
    """
    return {
        name for name, entry in read_providers_map().items() if isinstance(entry, dict) and is_variant_style_name(name)
    }
