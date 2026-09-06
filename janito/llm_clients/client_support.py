"""Shared LLM-side helpers for the API client modules.

The client modules (``completions_api``, ``conversations_api``,
``anthropic_api``, ``dashscope_api`` and ``gemini_api``) share a set of
small, API-domain helpers: MCP service loading, error classification for the
observer and the ``RequestCancelled`` control-flow exception.  (The SDK
response-object introspection used by the stream consumers for the verbose
dumps lives in :mod:`janito.llm_adapters.sdk`, the shared adapter layer -- issue
#90.)  This module centralizes them so each client stays focused on its own
API's wire format.

This module is deliberately **UI-free**: everything that renders output (the
Rich turn observer, the per-round stream runner, the usage summary and the
error explainers) lives in :mod:`janito.ui`, injected by the CLI at the
composition point (``cli/chat.py``).  The one UI-adjacent exception is
:class:`RequestCancelled`: the exception the UI stream runner raises on
Enter-to-cancel.  It is part of the clients' control-flow contract -- the
stream consumers catch it to carry the partial result (and the server-side
response id) forward -- so it lives here with the clients that handle it.
"""

import logging
from typing import Any

from janito.mcp_manager import get_mcp_manager
from janito.tooling.tools_registry import tools_loading_enabled

# Configure logger for this module
logger = logging.getLogger(__name__)


class RequestCancelled(Exception):
    """Raised when the user cancels a pending API request by pressing Enter.

    Unlike ``KeyboardInterrupt`` (Ctrl+C), which rolls the conversation
    history back to the last turn, this signals an *interrupt without
    rollback*: the user's message stays in the conversation history so the
    conversation can continue from where it was interrupted.

    Raised by the injected per-round stream runner
    (:func:`janito.ui.stream_runner._run_with_progress_bar`) when the user
    presses Enter while the request is in flight; the API clients catch it
    to keep the conversation state.

    Attributes:
        partial_result: The worker thread's return value, when it finished
            honouring the cancel before the exception was raised (e.g. the
            stream consumers return the partially-assembled response parts,
            from which a server-side response id can be recovered). ``None``
            when the worker was still busy.
    """

    def __init__(self, message: str = "Request cancelled by user (pressed Enter)."):
        super().__init__(message)
        self.partial_result = None


def _load_mcp(use_mcp: bool) -> tuple[Any, list[dict[str, Any]]]:
    """Load MCP services/tools when enabled; return ``(manager, tools)``.

    MCP tools are never loaded when tool loading is disabled (``--no-tools``):
    that flag suppresses every non-skill tool, MCP included.
    """
    mcp_manager = None
    if use_mcp and tools_loading_enabled():
        mcp_manager = get_mcp_manager()
        try:
            mcp_manager.load_services()
            mcp_tools = mcp_manager.get_all_tools()
            logger.info(f"Loaded {len(mcp_tools)} MCP tools from {len(mcp_manager.connected_services)} services")
        except Exception as e:  # noqa: BLE001 - intentional boundary, log/convert and continue
            logger.warning(f"Failed to load MCP tools: {e}")
            mcp_tools = []
    else:
        mcp_tools = []
    return mcp_manager, mcp_tools


def _is_rate_limit(e: Exception) -> bool:
    """Return True when *e* is an HTTP 429 rate-limit failure (issue #116).

    Matches the OpenAI SDK ``RateLimitError`` by class name (no SDK import
    here), a ``status_code``/``status``/``code`` of 429, or ``429`` /
    ``rate limit`` / ``too many requests`` in the message.
    """
    name = type(e).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name or "too many requests" in name:
        return True
    for attr in ("status_code", "status", "code"):
        try:
            if getattr(e, attr, None) == 429:
                return True
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    message = str(e).lower()
    return "429" in message or "rate limit" in message or "rate_limit" in message or "too many requests" in message


def _retry_after_seconds(e: Exception) -> float | None:
    """Extract a ``Retry-After`` delay (seconds) from a 429 error, if present.

    Checks a ``retry_after`` attribute, then the response headers
    (``response.headers`` mapping or ``headers`` mapping). Returns None when
    absent or unparsable so the caller falls back to exponential backoff.
    """
    direct = getattr(e, "retry_after", None)
    if isinstance(direct, (int, float)) and direct >= 0:
        return float(direct)
    for holder in (getattr(e, "response", None), e):
        value = _headers_retry_after(getattr(holder, "headers", None))
        if value is not None:
            return value
    return None


def _headers_retry_after(headers: Any) -> float | None:
    """Return the ``Retry-After`` header value as seconds, or None."""
    if headers is None:
        return None
    try:
        if isinstance(headers, dict):
            value = headers.get("retry-after", headers.get("Retry-After"))
        else:
            value = headers.get("retry-after", headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _classify_error(e: Exception) -> str:
    """Classify an exception for the error explainers: "not_found", "auth" or "unknown".

    Used by the native-SDK clients (Anthropic / DashScope / Gemini), whose
    generic ``except Exception`` handlers raise their own exception types,
    to pick the explainer explicitly -- mirroring the checks the explainers
    themselves perform (unknown-model / stale-response message, 401 status
    or error code).  The OpenAI SDK clients skip this: their typed
    ``except`` blocks pass the kind directly.
    """
    message = str(e).lower()
    if "model not exist" in message or "model not found" in message or "previous response" in message:
        return "not_found"
    status_code = getattr(e, "status_code", None)
    code = getattr(e, "code", None)
    if status_code == 401 or code == 401 or (isinstance(code, str) and "InvalidApiKey" in code):
        return "auth"
    return "unknown"
