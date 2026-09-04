"""
Pure request-payload helpers for provider/model capabilities.

These functions turn resolved provider/model values (thinking mode, built-in
native tools) into the request-body payload shapes the API clients send
(``extra_body`` flags, Responses ``tools`` entries, display strings).  They
are stateless transforms: the only provider-config read is the Gemini-flavor
lookup used to guard the ``enable_thinking`` flag, resolved through
:func:`janito.providers.registry.get_provider`.

Part of the split provider-config module family (see
:mod:`janito.providers.registry`).
"""

from .registry import get_provider


def builtin_tools_enable_flags(tools) -> dict[str, bool]:
    """Map a model's built-in tool types to their API ``enable_*`` flags.

    Each built-in tool ``type`` maps to the request-body flag that turns it
    on for the OpenAI-compatible Chat Completions / native DashScope APIs:

    - ``code_interpreter`` -> ``enable_code_interpreter`` (which only
      supports calls in thinking mode, so ``enable_thinking`` is forced on);
    - ``web_search`` / ``web_extractor`` -> ``enable_search``.

    ``tools`` may be ``None`` (no built-in tools declared), in which case an
    empty dict is returned.  Entries may be dicts (``{"type": ...}``) or
    plain strings.
    """
    flags: dict[str, bool] = {}
    for tool in tools or []:
        tool_type = tool.get("type") if isinstance(tool, dict) else tool
        if tool_type == "code_interpreter":
            flags["enable_code_interpreter"] = True
            # The Code Interpreter feature only supports calls in thinking mode.
            flags["enable_thinking"] = True
        elif tool_type in ("web_search", "web_extractor"):
            flags["enable_search"] = True
    return flags


def apply_builtin_tools_to_extra_body(call_kwargs: dict, tools) -> None:
    """Add the model's built-in tool ``enable_*`` flags to ``call_kwargs``.

    The built-in tools declared in a model's provider-config ``tools`` entry
    are model capabilities enabled through request-body flags on the
    OpenAI-compatible Chat Completions API, not function tools.  Each
    ``type`` maps to an ``enable_<type>`` flag in ``extra_body`` (see
    :func:`builtin_tools_enable_flags`); ``code_interpreter`` additionally
    forces ``enable_thinking`` because it only supports calls in thinking
    mode.

    The ``call_kwargs`` dict is mutated in place; ``extra_body`` is created
    when needed.  ``tools`` may be ``None`` (no built-in tools declared), in
    which case nothing is sent.
    """
    flags = builtin_tools_enable_flags(tools)
    if flags:
        call_kwargs.setdefault("extra_body", {}).update(flags)


def apply_thinking_to_extra_body(
    call_kwargs: dict, thinking, provider: str | None = None
) -> None:
    """Add the resolved thinking mode to ``call_kwargs``' ``extra_body``.

    Thinking values may be:

    - ``True`` -- the flag-style providers (DeepSeek, Alibaba/Qwen) send
      ``extra_body={'enable_thinking': True}``;
    - a **dict** -- passed through verbatim as ``extra_body['thinking']``
      (e.g. MiniMax-M3's ``{'type': 'adaptive'}``, which its
      OpenAI-compatible API accepts with ``type`` ``disabled``/``adaptive``);
    - falsy (``False`` / ``None``) -- nothing is sent.

    Gemini-flavored providers (``gemini_flavor`` in their provider config,
    e.g. Google) never receive ``enable_thinking``: Gemini 3.x models reason
    by default and the OpenAI-compatibility layer rejects the unknown field
    with a 400 error.  Pass the ``provider`` name to enable this guard.

    The ``call_kwargs`` dict is mutated in place; ``extra_body`` is created
    when needed.
    """
    if thinking is True:
        # Gemini-flavored APIs (e.g. Google's Gemini OpenAI-compatibility
        # layer) do not accept an enable_thinking flag -- Gemini 3.x reasons
        # by default and the field does not exist in the request schema.
        if provider:
            found = get_provider(provider)
            if found is not None and found.gemini_flavor():
                return
        call_kwargs.setdefault("extra_body", {})["enable_thinking"] = True
    elif isinstance(thinking, dict):
        call_kwargs.setdefault("extra_body", {})["thinking"] = dict(thinking)


def format_thinking_display(thinking, provider: str | None = None) -> str:
    """Render a thinking value for human-readable display.

    When ``provider`` is given and uses the Gemini flavor (e.g. ``google``),
    the boolean thinking flag is not applicable because Gemini models reason by
    default and control reasoning depth through the reasoning effort; in that
    case returns ``"N/A (controlled via Reasoning Effort)"``.

    Otherwise:
    ``True`` (or any truthy non-dict) renders as ``"enabled"``; a structured
    dict (e.g. MiniMax-M3's ``{'type': 'adaptive'}``) renders as
    ``"enabled (<type>)"``; falsy values render as ``"disabled"``.
    """
    if provider:
        found = get_provider(provider)
        if found is not None and found.gemini_flavor():
            return "N/A (controlled via Reasoning Effort)"
    if isinstance(thinking, dict) and thinking.get("type"):
        return f"enabled ({thinking['type']})"
    return "enabled" if thinking else "disabled"


def resolve_thinking_display(
    effective_thinking, explicit_thinking: bool = False, provider: str | None = None
) -> str:
    """Render the effective thinking mode for session displays.

    ``effective_thinking`` is the resolved value (explicit flag or the
    model's built-in default); ``explicit_thinking`` reports whether the
    flag was forced on.  A falsy effective value means "not forced" (the
    model's own default applies), so it renders as ``"Model Default"``
    rather than ``"disabled"``.  A truthy built-in default without an
    explicit flag is marked ``" (model default)"``.  The Gemini-flavor
    ``N/A`` rendering from :func:`format_thinking_display` is passed
    through unchanged.
    """
    display = format_thinking_display(effective_thinking, provider=provider)
    if display == "disabled":
        return "Model Default"
    if effective_thinking and not explicit_thinking:
        if provider:
            found = get_provider(provider)
            if found is not None and found.gemini_flavor():
                return display
        return display + " (model default)"
    return display
