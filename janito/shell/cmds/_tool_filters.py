"""
Shared tool-filtering helpers for the /read, /write, /rx, /rw and /rwx
shell commands.

These commands switch the privileges of the whole session (issue #141):
/read selects the read-only (``"r"``) tools, /write the write-only
(``"w"``) tools, /rx the read + execute (``"r"``/``"x"``) tools, /rw the
read + write (``"r"``/``"w"``) tools and /rwx the read + write + execute
tools. The filtering itself is identical, so it lives here.

The registry is **complete** (discovery loads every tool regardless of the
``-r``/``-w``/``-x`` flags), so switching the session privileges can also
*expand* beyond the privileges the session started with (issue #87):
under ``janito -r``, ``/write`` still offers the write-only tools.  When a
switch offers tools the previous session privileges would not normally
allow, :func:`warn_if_privilege_override` prints a one-line note so the
escalation is visible.
"""

from collections.abc import Iterable
from typing import Any


def warn_if_privilege_override(schemas: list[dict[str, Any]], permissions: str) -> None:
    """Print a one-line warning when ``schemas`` expand beyond the session
    privileges.

    The /read, /write, /rx, /rw and /rwx session switches (issue #141) can
    expand beyond the runtime ``-r``/``-w``/``-x`` restrictions the session
    started with (issue #87); surface that so the escalation is visible, mirroring the
    full-privileges warning at startup.  ``permissions`` is the set of
    permission letters the command grants for the turn (e.g. ``"rx"`` for
    ``/rx``); it is rendered in the note as ``(-r/-x)`` so the message
    reflects what the turn actually receives (issue #109).  Prints nothing
    when the offered schemas are a subset of the session's allowed tools
    (or when no privilege flags were passed).
    """
    if not schemas:
        return
    from janito import privileges as _privileges_mod

    if _privileges_mod.running_privileges is None:
        # No privilege restrictions configured - nothing is overridden.
        return
    from janito.tooling.tools_registry import get_session_tool_names

    offered = {schema.get("function", {}).get("name") for schema in schemas if schema.get("function", {}).get("name")}
    extra = offered - get_session_tool_names()
    if extra:
        from rich.console import Console

        flags = "/".join(f"-{letter}" for letter in sorted(permissions))
        Console().print(f"[bold yellow]Note:[/bold yellow] this turn runs with " f"privileges ({flags})")


def get_tool_schemas_by_permission(permission: str) -> list[dict[str, Any]]:
    """Return the function-calling schemas of the tools whose declared
    permission is exactly ``permission``.

    A tool matches when its ``_tool_permissions`` equals ``permission`` (the
    value set by ``@tool(permissions=...)``): e.g. ``"r"`` for read-only and
    ``"w"`` for write-only tools. Tools declaring no permissions (e.g. the
    skill tools), tools combining permissions (``"rw"``, ``"rwx"``, ...) and
    MCP tools (which carry no permission metadata here) are excluded -- only
    the matching built-in tools are offered.
    """
    return get_tool_schemas_by_permissions({permission})


def get_tool_schemas_by_permissions(
    permissions: Iterable[str],
) -> list[dict[str, Any]]:
    """Return the function-calling schemas of the tools whose declared
    permission is one of ``permissions``.

    A tool matches when its ``_tool_permissions`` equals any of the given
    permission values (the values set by ``@tool(permissions=...)``): e.g.
    ``{"r", "x"}`` offers the read-only and execute-only tools for ``/rx``.
    Tools declaring no permissions (e.g. the skill tools), tools combining
    permissions (``"rw"``, ``"rwx"``, ...) and MCP tools (which carry no
    permission metadata here) are excluded -- only the matching built-in
    tools are offered.
    """
    from janito.tooling.tools_registry import (
        get_all_tool_permissions,
        get_all_tool_schemas,
    )

    allowed = set(permissions)
    matching_names = {
        name for name, tool_permissions in get_all_tool_permissions().items() if tool_permissions in allowed
    }
    return [schema for schema in get_all_tool_schemas() if schema.get("function", {}).get("name") in matching_names]


def get_tool_schemas_by_permission_letters(
    letters: str,
) -> list[dict[str, Any]]:
    """Return the function-calling schemas of the tools whose declared
    permission is a **non-empty subset** of ``letters``.

    A tool matches when every character of its ``_tool_permissions`` is one
    of the given letters (the values set by ``@tool(permissions=...)``):
    e.g. ``"rw"`` offers the read, write and read + write tools for ``/rw``
    (``"r"``, ``"w"`` and ``"rw"``), and ``"rwx"`` offers every tool that
    declares any read/write/execute combination for ``/rwx``. This mirrors
    the subset semantics of the ``-r``/``-w``/``-x`` privilege model (see
    ``janito.tools._check_tool_privileges``). Tools declaring no
    permissions (e.g. the skill tools), tools using letters outside the
    allowed set, and MCP tools (which carry no permission metadata here)
    are excluded -- only the matching built-in tools are offered.
    """
    from janito.tooling.tools_registry import (
        get_all_tool_permissions,
        get_all_tool_schemas,
    )

    allowed = set(letters)
    matching_names = {
        name
        for name, tool_permissions in get_all_tool_permissions().items()
        if tool_permissions and set(tool_permissions) <= allowed
    }
    return [schema for schema in get_all_tool_schemas() if schema.get("function", {}).get("name") in matching_names]
