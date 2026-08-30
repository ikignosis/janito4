"""Shared LLM-side helpers for the API client modules.

The client modules (``completions_api``, ``conversations_api``,
``anthropic_api``, ``dashscope_api`` and ``gemini_api``) share a set of
small, API-domain helpers: MCP service loading, SDK response-object
introspection (raw attribute extraction for the verbose dumps) and error
classification for the observer.  This module centralizes them so each
client stays focused on its own API's wire format.

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
            logger.info(
                f"Loaded {len(mcp_tools)} MCP tools from {len(mcp_manager.connected_services)} services"
            )
        except Exception as e:
            logger.warning(f"Failed to load MCP tools: {e}")
            mcp_tools = []
    else:
        mcp_tools = []
    return mcp_manager, mcp_tools


def _object_items(obj: Any):
    """Yield ``(key, value)`` pairs from an SDK response object.

    Handles pydantic models (``model_dump``/``dict``), ``SimpleNamespace`` /
    plain objects (``__dict__``), plain dicts and dict-like objects
    (DashScope's ``DictMixin``).
    """
    if isinstance(obj, dict):
        return obj.items()
    for attr in ("model_dump", "dict"):
        method = getattr(obj, attr, None)
        if callable(method):
            try:
                if attr == "model_dump":
                    # Pydantic v2's model_dump accepts warnings=False; older
                    # pydantic v2 releases raise TypeError for the kwarg, so
                    # fall back to the plain call.  Suppressing serializer
                    # warnings keeps response metadata the SDK parsed with
                    # construct() (e.g. a provider echoing back a built-in
                    # tool type the SDK does not know, like Alibaba/Qwen's
                    # ``web_extractor``) from flooding the console during the
                    # raw-attribute dump: the echoed ``tools`` array is never
                    # surfaced anyway (only scalar top-level attributes are).
                    try:
                        return method(warnings=False).items()
                    except TypeError:
                        pass
                return method().items()
            except Exception:
                continue
    if hasattr(obj, "__dict__"):
        return vars(obj).items()
    if callable(getattr(obj, "keys", None)):
        try:
            return [(k, obj[k]) for k in obj.keys()]
        except Exception:
            return []
    return []


def _extract_raw_attrs(
    obj: Any, *, skip: tuple[str, ...] = (), max_list: int = 3
) -> dict[str, Any]:
    """Extract the scalar top-level attributes of an SDK response object.

    SDK response objects (pydantic models, ``SimpleNamespace``, DashScope's
    ``DictMixin``, plain dicts) expose their wire metadata as top-level
    attributes: ``id``, ``model``, ``created``, ``system_fingerprint``,
    ``status``, ``finish_reason``, ...  The verbose response dump should
    surface those alongside the already-extracted content/usage/tool-call
    fields, so this helper flattens them into a plain dict.

    Nested payloads (``choices``, ``output``, ``content``, ``usage``, ...)
    are omitted either via ``skip`` or because their values are not scalars
    (or are long lists of non-scalars), so the dump stays compact.
    """
    if obj is None:
        return {}
    out: dict[str, Any] = {}
    for key, value in _object_items(obj):
        if key.startswith("_") or key in skip or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
            continue
        if isinstance(value, (list, tuple)) and all(
            isinstance(v, (str, int, float, bool)) for v in value
        ):
            if 0 < len(value) <= max_list:
                out[key] = list(value)
            continue
        if isinstance(value, dict) and all(
            isinstance(v, (str, int, float, bool)) for v in value.values()
        ):
            if 0 < len(value) <= max_list:
                out[key] = dict(value)
    return out


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
    if (
        "model not exist" in message
        or "model not found" in message
        or "previous response" in message
    ):
        return "not_found"
    status_code = getattr(e, "status_code", None)
    code = getattr(e, "code", None)
    if (
        status_code == 401
        or code == 401
        or (isinstance(code, str) and "InvalidApiKey" in code)
    ):
        return "auth"
    return "unknown"
