"""Shared SDK response-object introspection for the stream consumers.

The per-vendor stream consumers (Chat Completions, Responses, Anthropic,
DashScope and Gemini) all surface the raw top-level metadata of an SDK
response object (``id``, ``model``, ``created``, ``finish_reason``, ...) for
the CLI's verbose response dump.  SDK objects come in many shapes -- pydantic
models, ``SimpleNamespace``, DashScope's ``DictMixin``, plain dicts -- so the
extraction helpers live here, in the shared adapter layer, and the
``llm_clients`` stream modules depend on them (issue #90: the adapter layer
must not import from ``llm_clients``, so these helpers cannot stay in
``llm_clients.client_support``).
"""

from typing import Any


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
            except Exception:  # noqa: BLE001 - intentional boundary, log/convert and continue
                continue
    if hasattr(obj, "__dict__"):
        return vars(obj).items()
    if callable(getattr(obj, "keys", None)):
        return _dict_like_items(obj)
    return []


def _dict_like_items(obj: Any):
    """Return ``(key, value)`` pairs from a dict-like object with ``keys``."""
    try:
        keys = list(obj.keys())
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return []
    items = []
    for k in keys:
        try:
            items.append((k, obj[k]))
        except (AttributeError, TypeError, KeyError, ValueError, RuntimeError):
            continue
    return items


def _extract_raw_attrs(obj: Any, *, skip: tuple[str, ...] = (), max_list: int = 3) -> dict[str, Any]:
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
        if isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float, bool)) for v in value):
            if 0 < len(value) <= max_list:
                out[key] = list(value)
            continue
        if isinstance(value, dict) and all(isinstance(v, (str, int, float, bool)) for v in value.values()):
            if 0 < len(value) <= max_list:
                out[key] = dict(value)
    return out


__all__ = [
    "_extract_raw_attrs",
    "_object_items",
]
