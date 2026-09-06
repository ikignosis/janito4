"""Optional-SDK package guards shared by the client and web layers.

The native-SDK API types (``Anthropic``, ``DashScope``, ``Gemini``) depend on
optional packages that are never required to import ``janito`` itself (see
``janito.providers.REQUIRES_BY_API_TYPE``).  Both the CLI clients
(``janito.llm_clients.*``) and the web runners
(``janito.web.backend.agent.*``) used to repeat the same
``importlib.util.find_spec`` + ``RuntimeError`` guard; this module is the
single home for it.  It lives at the root level because both the
``llm_clients`` and ``web`` domains may import from it (see the allowed-edge
matrix in ``tests/test_import_graph.py``).
"""

from __future__ import annotations

import importlib.util


def require_optional_package(find_spec_name: str, api_type: str, pip_package: str) -> None:
    """Refuse to run when an optional SDK package is missing.

    Args:
        find_spec_name: The module spec name to probe (e.g. ``"google.genai"``).
        api_type: The canonical API type name used in the error message.
        pip_package: The pip package name users must install.

    Raises:
        RuntimeError: With an actionable install message when the package is
            not installed.
    """
    try:
        spec = importlib.util.find_spec(find_spec_name)
    except ModuleNotFoundError:
        spec = None
    if spec is None:
        raise RuntimeError(
            f"API type '{api_type}' requires the optional '{pip_package}' package, "
            f"which is not installed. Install it with: pip install {pip_package}"
        )


__all__ = [
    "require_optional_package",
]
