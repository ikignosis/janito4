"""Current-turn privilege tracking (used by the StartTask tool).

A turn may run with an explicit tool set that is independent of the
session's ``-r``/``-w``/``-x`` privileges (issue #87; e.g. ``/notools``,
or the pre-#141 single-turn ``/read``/``/write``/``/rx``/``/rw``/``/rwx``
overrides).  ``StartTask`` spawns a child ``janito`` process and must
mirror the privileges of the task that is currently running -- the turn in
which the tool was called -- not just the privileges the parent was
launched with.  This module tracks those *turn-specific privileges*
through a ``ContextVar`` (mirroring the design of
:mod:`janito.tooling.reporter` and :mod:`janito.tooling.prompting`): the
shell sets it around each turn, ``StartTask`` reads it.

The value is the canonical ``r``/``w``/``x`` string (e.g. ``""`` for a
``/notools`` turn, ``"rw"`` for a normal turn under ``janito -r -w``).
``None`` means no turn is running in the current context (single-prompt
mode, web mode, tests) -- callers then fall back to the session's
``running_privileges``.
"""

from contextvars import ContextVar, Token

_turn_privileges: ContextVar[str | None] = ContextVar("_turn_privileges", default=None)


def set_turn_privileges(privileges: str | None) -> Token:
    """Set the current turn's privilege letters for this context.

    Args:
        privileges: The canonical ``r``/``w``/``x`` string for the turn, or
            ``None`` to clear the tracking.

    Returns:
        A contextvars token; pass it to :func:`reset_turn_privileges` when
        the turn ends so a restricted turn never leaks its privileges into
        the next turn.
    """
    return _turn_privileges.set(privileges)


def reset_turn_privileges(token: Token) -> None:
    """Restore the turn-privileges ContextVar captured by ``token``.

    Uses ``ContextVar.reset(token)`` (the ``Token.reset()`` method was
    removed in Python 3.14), so the value set before the turn is restored
    after it.
    """
    _turn_privileges.reset(token)


def get_turn_privileges() -> str | None:
    """Return the privileges of the turn running in this context.

    Returns:
        The canonical ``r``/``w``/``x`` string, or ``None`` when no turn is
        running here (callers fall back to ``running_privileges``).
    """
    return _turn_privileges.get()


def _session_privileges() -> str:
    """The session's effective privilege letters (``running_privileges``)."""
    from janito import privileges as _privileges_mod
    from janito.privileges import format_privileges

    if _privileges_mod.running_privileges is None:
        # No restrictions configured: the session may use every tool, which
        # maps to the full -r -w -x flags for a child process.
        return "rwx"
    return format_privileges(_privileges_mod.running_privileges)


def resolve_turn_privileges(tools: list[dict] | None) -> str:
    """Resolve the effective privilege letters of a turn's offered tools.

    Args:
        tools: The ``tools=`` list passed to the turn (function-calling
            schemas), or ``None`` for a normal turn that uses the session
            default tool set.

    Returns:
        The canonical ``r``/``w``/``x`` letters the turn may use: the
        session privileges for a normal turn (e.g. ``"rw"`` under
        ``janito -r -w``), or the union of the declared permission letters
        of the explicitly offered tools for a restricted turn (e.g. ``"rx"``
        for ``/rx``, ``"rwx"`` for ``/rwx``).  An explicit empty list
        yields ``""`` (no permission letters -- the child then starts with
        the janito default, read-only).
    """
    if tools is None:
        return _session_privileges()

    letters: set[str] = set()
    from janito.tooling.tools_registry import get_tool_permissions

    for schema in tools:
        if not isinstance(schema, dict):
            continue
        # Handles both the Chat Completions shape (name nested under
        # "function") and the Responses / Anthropic top-level shape.
        function = schema.get("function")
        if isinstance(function, dict) and function.get("name"):
            name = function["name"]
        else:
            name = schema.get("name")
        if not name:
            continue
        try:
            letters.update(get_tool_permissions(name))
        except KeyError:
            # Unknown/removed tool: it contributes no permission letters.
            continue
    return "".join(c for c in "rwx" if c in letters)


def current_turn_privileges() -> str:
    """Return the privileges a child spawned from this context should get.

    Prefers the turn running in the current context (set by the shell's
    ``_run_turn`` for turns with an explicit tool set such as ``/notools``),
    falling back to the session's ``running_privileges`` when no
    turn is active here (single-prompt mode, web mode, tests).

    Returns:
        The canonical ``r``/``w``/``x`` privilege string (e.g. ``"rx"``).
    """
    turn = _turn_privileges.get()
    if turn is not None:
        return turn
    return _session_privileges()


__all__ = [
    "current_turn_privileges",
    "get_turn_privileges",
    "reset_turn_privileges",
    "resolve_turn_privileges",
    "set_turn_privileges",
]
