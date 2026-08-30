"""
Main tools package with auto-discovery support.

This package provides the built-in toolsets (``files/``, ``system/``,
``net/``, ``janitoweb/``).  Tool discovery -- scanning the toolsets for
``@tool``-marked classes, running their ``should_load()`` gate and wrapping
them -- and the privilege predicates live in the tooling framework
(:mod:`janito.tooling.discovery`, issue #90), so the tools package depends
one-way on the framework and never the other way around.  This module
re-exports those functions for the plugin manager and the shell/web tool
lists, which historically imported them from ``janito.tools``.
"""

from ..tooling.discovery import (
    _check_should_load,
    _check_tool_privileges,
    _collect_module_tools,
    _load_module_tools,
    _make_class_tool,
    _skipped_tools,
    discover_module_tools,
    discover_toolsets,
    get_skipped_tools,
    missing_privileges,
    privilege_restriction_reason,
    tool_is_allowed_by_privileges,
    wrap_tool_class,
)

__all__ = [
    "_check_should_load",
    "_check_tool_privileges",
    "_collect_module_tools",
    "_load_module_tools",
    "_make_class_tool",
    "_skipped_tools",
    "discover_module_tools",
    "discover_toolsets",
    "get_skipped_tools",
    "missing_privileges",
    "privilege_restriction_reason",
    "tool_is_allowed_by_privileges",
    "wrap_tool_class",
]
