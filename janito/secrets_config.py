"""
Secrets configuration management for Janito CLI.

Handles storage and retrieval of secrets in ~/.janito/secrets.json

Structure:
{
    "key1": "value1",
    "key2": "value2"
}

Secrets are stored separately from auth.json (API keys) to allow for
storing arbitrary secret values like tokens, passwords, or other
credentials that aren't provider-specific API keys.
"""

import logging
from pathlib import Path

from .json_store import SecretsConfigStore

# Configure logger for this module
logger = logging.getLogger(__name__)

# Module-level singleton store backing every function below.
_store = SecretsConfigStore()


def get_secrets_file_path() -> Path:
    """Get the path to the secrets configuration file (the write target)."""
    return _store.file_path()


def get_secrets_file_paths() -> list[Path]:
    """Get all secrets.json paths used for resolution, in priority order.

    With ``-l`` / ``--local`` the project-local path (``./.janito/secrets.json``)
    comes first, followed by the base path (``~/.janito/secrets.json`` or the
    ``-c`` / ``--config-dir`` override). Otherwise only the base path is
    returned.

    Returns:
        List of paths, highest priority first.
    """
    return _store.file_paths()


def set_secret(key: str, value: str) -> bool:
    """
    Set a secret value.

    Args:
        key: The secret key name
        value: The secret value

    Returns:
        bool: True if successful, False otherwise
    """
    return _store.set_secret(key, value)


def get_secret(key: str) -> str | None:
    """
    Get a secret value.

    Args:
        key: The secret key name

    Returns:
        Optional[str]: The secret value if found, None otherwise
    """
    return _store.get_secret(key)


def delete_secret(key: str) -> bool:
    """
    Delete a secret.

    Args:
        key: The secret key name

    Returns:
        bool: True if deleted, False if not found
    """
    return _store.delete_secret(key)


def list_secrets() -> list:
    """
    List all configured secret keys.

    Returns:
        list: List of secret key names
    """
    return _store.list_secrets()


def secret_exists(key: str) -> bool:
    """
    Check if a secret exists.

    Args:
        key: The secret key name

    Returns:
        bool: True if the secret exists, False otherwise
    """
    return _store.secret_exists(key)
