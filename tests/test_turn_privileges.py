"""
Tests for janito.tooling.turn_privileges.

The module tracks the *turn-specific* privileges (e.g. a /rx or /rwx turn)
that StartTask mirrors when spawning a child janito process, falling back to
the session's running_privileges when no turn is active in the context.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import janito.privileges as _privileges_mod
from janito.privileges import Privileges
from janito.tooling import turn_privileges as tp


def _schema(name):
    """A Chat-Completions-style function schema carrying only the name."""
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _install_fake_permissions(monkeypatch):
    """Replace get_tool_permissions with a deterministic name -> letters map."""
    perms = {
        "ReadTool": "r",
        "WriteTool": "w",
        "ExecTool": "x",
        "ReadWriteTool": "rw",
    }

    def fake_get_tool_permissions(name):
        if name in perms:
            return perms[name]
        raise KeyError(name)

    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_tool_permissions",
        fake_get_tool_permissions,
    )


def test_session_privileges_fallback(monkeypatch):
    """A normal turn (tools=None) uses the session's running_privileges."""
    monkeypatch.setattr(
        _privileges_mod,
        "running_privileges",
        Privileges(READ=True, WRITE=True),
    )
    assert tp.resolve_turn_privileges(None) == "rw"
    assert tp.current_turn_privileges() == "rw"


def test_read_only_session_default(monkeypatch):
    """The CLI default (read-only, issue #85) resolves to 'r'."""
    monkeypatch.setattr(_privileges_mod, "running_privileges", Privileges(READ=True))
    assert tp.resolve_turn_privileges(None) == "r"


def test_unrestricted_session_maps_to_full_flags(monkeypatch):
    """No restrictions configured -> the child gets the full -r -w -x set."""
    monkeypatch.setattr(_privileges_mod, "running_privileges", None)
    assert tp.resolve_turn_privileges(None) == "rwx"
    assert tp.current_turn_privileges() == "rwx"


def test_resolve_from_tool_schemas_union(monkeypatch):
    """A restricted turn maps to the union of the offered tools' letters."""
    _install_fake_permissions(monkeypatch)

    # /rx offers the read and execute tools.
    assert tp.resolve_turn_privileges([_schema("ReadTool"), _schema("ExecTool")]) == "rx"
    # /rwx offers every read/write/execute combination.
    assert tp.resolve_turn_privileges([_schema("ReadWriteTool"), _schema("ExecTool")]) == "rwx"
    # /write offers the write-only tools.
    assert tp.resolve_turn_privileges([_schema("WriteTool")]) == "w"


def test_resolve_skips_unknown_and_non_schema_entries(monkeypatch):
    """Unknown tools and malformed entries contribute no letters."""
    _install_fake_permissions(monkeypatch)

    assert tp.resolve_turn_privileges([_schema("Nope"), {"type": "function"}]) == ""
    assert tp.resolve_turn_privileges([]) == ""


def test_resolve_handles_top_level_name_shape(monkeypatch):
    """The Responses / Anthropic schema shape (name at top level) works too."""
    _install_fake_permissions(monkeypatch)

    assert tp.resolve_turn_privileges([{"name": "ReadTool", "parameters": {}}]) == "r"


def test_set_get_reset_turn_privileges():
    """The ContextVar starts unset, is set during a turn, and resets after."""
    assert tp.get_turn_privileges() is None
    token = tp.set_turn_privileges("rx")
    try:
        assert tp.get_turn_privileges() == "rx"
        assert tp.current_turn_privileges() == "rx"
    finally:
        tp.reset_turn_privileges(token)
    assert tp.get_turn_privileges() is None


def test_current_turn_privileges_prefers_context(monkeypatch):
    """An active turn overrides the session privileges for current_turn_privileges."""
    monkeypatch.setattr(_privileges_mod, "running_privileges", Privileges(READ=True))

    token = tp.set_turn_privileges("rwx")
    try:
        assert tp.current_turn_privileges() == "rwx"
    finally:
        tp.reset_turn_privileges(token)

    # Once the turn ends, the session default applies again.
    assert tp.current_turn_privileges() == "r"
