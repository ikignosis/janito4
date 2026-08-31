"""LLM client packages: the shared agent-loop pipeline and the per-vendor clients.

- SDK-agnostic core: :mod:`~janito.llm_clients.api_config` (``APIConfig`` +
  ``build_api_config``), :mod:`~janito.llm_clients.base_client`
  (``Client.run_turn``) and :mod:`~janito.llm_clients.client_support`.
- Per-vendor subpackages: :mod:`~janito.llm_clients.openai` (Completions +
  Responses), :mod:`~janito.llm_clients.anthropic`,
  :mod:`~janito.llm_clients.dashscope`, :mod:`~janito.llm_clients.gemini`
  (native SDKs).
"""

from .api_config import APIConfig, build_api_config
from .client_support import RequestCancelled
from .factory import create_client

__all__ = [
    "APIConfig",
    "RequestCancelled",
    "build_api_config",
    "create_client",
]
