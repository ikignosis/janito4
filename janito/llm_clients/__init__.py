"""LLM client packages: the shared agent-loop pipeline and the per-vendor clients.

Layout (issue #79):

- SDK-agnostic core: :mod:`~janito.llm_clients.api_config` (``APIConfig`` +
  ``build_api_config``), :mod:`~janito.llm_clients.base_client`
  (``Client.run_turn``) and :mod:`~janito.llm_clients.client_support`
  (error classification, SDK response-object introspection, MCP loading and
  the ``RequestCancelled`` control-flow exception).  The UI-side pieces (the
  Rich turn observer and the per-round stream runner) live in
  :mod:`janito.ui`, injected by the CLI.
- Per-vendor subpackages:

  - :mod:`~janito.llm_clients.openai` -- Chat Completions and Responses.
  - :mod:`~janito.llm_clients.anthropic` -- native ``anthropic`` SDK.
  - :mod:`~janito.llm_clients.dashscope` -- native ``dashscope`` SDK.
  - :mod:`~janito.llm_clients.gemini` -- native ``google-genai`` SDK.
"""

from ..ui_config import UIConfig
from .api_config import APIConfig, build_api_config
from .client_support import RequestCancelled
from .openai.completions_api import get_env_config, resolve_runtime_config, run_turn
from .openai.conversations_api import ConversationResult

__all__ = [
    "APIConfig",
    "UIConfig",
    "ConversationResult",
    "RequestCancelled",
    "build_api_config",
    "get_env_config",
    "resolve_runtime_config",
    "run_turn",
]
