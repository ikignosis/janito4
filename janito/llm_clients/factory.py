"""Factory for creating the per-API-type :class:`Client` instances.

The turn pipeline (:class:`~janito.llm_clients.base_client.Client` and its
subclasses) is shared by all five API types; only the concrete subclass
differs.  This factory is the single place that maps a resolved
:class:`~janito.llm_clients.api_config.APIConfig`'s ``api_type`` to the
matching subclass, so callers (the CLI composition point in
``cli/chat.py``) never import or instantiate the client classes directly
-- mirroring :func:`janito.mcp_client.factory.create_transport`.

The client subclasses are imported lazily inside the function, exactly like
``create_transport`` does for its transports: each client module imports the
``Client`` base class and ``APIConfig`` at module level, so importing them
up front would pull the whole client stack into the ``llm_clients`` package
import.
"""

from __future__ import annotations

from .api_config import APIConfig
from .base_client import Client


def create_client(api_config: APIConfig, ui_config=None) -> Client:
    """Create the :class:`Client` subclass for ``api_config.api_type``.

    Args:
        api_config: The resolved, immutable
            :class:`~janito.llm_clients.api_config.APIConfig` for this
            session.  ``api_config.api_type`` selects the subclass.
        ui_config: The injected, immutable
            :class:`~janito.ui.config.UIConfig` (per-round stream runner +
            turn observer) for this session (``None`` = headless defaults).

    Returns:
        A configured client instance whose ``run_turn`` implements the
        shared turn pipeline for that API type.

    Raises:
        ValueError: If ``api_config.api_type`` is not one of the supported
            API types (``Completions``, ``Responses``, ``Anthropic``,
            ``DashScope``, ``Gemini``).
    """
    if api_config.api_type == "Completions":
        from .openai.completions_api import CompletionsClient

        return CompletionsClient(api_config, ui_config)
    if api_config.api_type == "Responses":
        from .openai.conversations_api import ResponsesClient

        return ResponsesClient(api_config, ui_config)
    if api_config.api_type == "Anthropic":
        from .anthropic.anthropic_api import AnthropicClient

        return AnthropicClient(api_config, ui_config)
    if api_config.api_type == "DashScope":
        from .dashscope.dashscope_api import DashScopeClient

        return DashScopeClient(api_config, ui_config)
    if api_config.api_type == "Gemini":
        from .gemini.gemini_api import GeminiClient

        return GeminiClient(api_config, ui_config)
    raise ValueError(f"Unsupported API type: {api_config.api_type}")


__all__ = [
    "create_client",
]
