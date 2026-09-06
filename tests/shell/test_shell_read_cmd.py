"""
Tests for the /read shell command.

``/read`` is a bare command that switches the privileges of the whole
session to read-only (issue #141). These tests verify the command is
registered, dispatches correctly, switches ``running_privileges``, and that
the read-only tool schema helper still filters by the ``"r"`` permission.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito import privileges as _privileges_mod
from janito.privileges import format_privileges
from janito.shell import InteractiveShell
from janito.shell.cmds.read import ReadCmdHandler, get_read_only_tool_schemas
from tests.conftest import assert_command_matching, assert_command_registered

# A fake tool schema pair: one read-only tool and one write tool.
READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ReadFile",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "CreateFile",
        "description": "Create a file",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _shell():
    """Build a fresh shell for testing."""
    return InteractiveShell(model="test-model", no_history=True)


@pytest.fixture(autouse=True)
def _restore_running_privileges():
    """Privilege switches mutate the module-global; never leak it."""
    old = _privileges_mod.running_privileges
    yield
    _privileges_mod.running_privileges = old


# ---------------------------------------------------------------------------
# Registry / dispatch
# ---------------------------------------------------------------------------


def test_read_command_is_registered():
    assert_command_registered("/read")


def test_handler_name():
    assert ReadCmdHandler().name == "/read"


def test_handle_dispatches_only_read_command():
    assert_command_matching(ReadCmdHandler(), "/read")
    handler = ReadCmdHandler()
    shell = _shell()
    # Similar prefixes are not handled.
    assert handler.handle(shell, "/reads") is False
    assert handler.handle(shell, "/readme") is False


# ---------------------------------------------------------------------------
# Session privilege switch
# ---------------------------------------------------------------------------


def test_read_switches_session_privileges(monkeypatch):
    """Bare /read sets running_privileges to read-only."""
    handler = ReadCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/read") is True
    assert format_privileges(_privileges_mod.running_privileges) == "r"


def test_read_ignores_extra_text(monkeypatch):
    """Extra text after /read is ignored; the switch still happens."""
    handler = ReadCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/read please summarize") is True
    assert format_privileges(_privileges_mod.running_privileges) == "r"


def test_read_prints_confirmation(monkeypatch, capfd):
    handler = ReadCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/read") is True
    out = capfd.readouterr().out
    assert out.strip(), "switch printed nothing"


def test_read_overwrites_previous_privileges(monkeypatch):
    """A previous session level (e.g. rwx) is replaced by read-only."""
    handler = ReadCmdHandler()
    shell = _shell()
    monkeypatch.setattr(
        _privileges_mod,
        "running_privileges",
        _privileges_mod.Privileges(READ=True, WRITE=True, EXEC=True),
    )
    assert handler.handle(shell, "/read") is True
    assert format_privileges(_privileges_mod.running_privileges) == "r"


# ---------------------------------------------------------------------------
# Read-only schema filtering
# ---------------------------------------------------------------------------


def test_get_read_only_tool_schemas_filters_by_r_permission(monkeypatch):
    """Only tools whose permissions are exactly ``"r"`` are included."""
    permissions = {
        "ReadFile": "r",
        "ListFiles": "r",
        "CreateFile": "w",
        "MoveFile": "rw",
        "RunBashCode": "x",
        "LoadSkill": "",  # skill tools declare no permissions
    }
    schemas = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Tool {name}",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
        for name in permissions
    ]

    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: permissions,
    )
    monkeypatch.setattr("janito.tooling.tools_registry.get_all_tool_schemas", lambda: schemas)

    result = get_read_only_tool_schemas()
    names = [s["function"]["name"] for s in result]
    assert names == ["ReadFile", "ListFiles"]
    assert "CreateFile" not in names
    assert "MoveFile" not in names
    assert "RunBashCode" not in names
    assert "LoadSkill" not in names


def test_get_read_only_tool_schemas_empty_without_r_tools(monkeypatch):
    """No read-only tools -> an empty list (equivalent to tools=[])."""
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: {"CreateFile": "w", "RunBashCode": "x"},
    )
    monkeypatch.setattr("janito.tooling.tools_registry.get_all_tool_schemas", lambda: [WRITE_SCHEMA])

    assert get_read_only_tool_schemas() == []


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-v"]))
