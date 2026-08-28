"""
Shared tool-filtering helpers for the /read, /write and /rx shell commands.

These commands send the prompt through the shell's main-prompt path while
restricting ``tools=`` to a permission subset of the built-in tools: /read
offers the read-only (``"r"``) tools, /write offers the write-only (``"w"``)
tools and /rx offers the read + execute (``"r"``/``"x"``) tools. The
filtering itself is identical, so it lives here.
"""

from collections.abc import Iterable
from typing import Any


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
        name
        for name, tool_permissions in get_all_tool_permissions().items()
        if tool_permissions in allowed
    }
    return [
        schema
        for schema in get_all_tool_schemas()
        if schema.get("function", {}).get("name") in matching_names
    ]
