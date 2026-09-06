"""
Tests for the /write shell command.

``/write`` is a bare command that switches the privileges of the whole
session to write-only (issue #141). These tests verify the command is
registered, dispatches correctly, switches ``running_privileges``, and that
the write-only tool schema helper still filters by the ``"w"`` permission.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito import privileges as _privileges_mod
from janito.privileges import format_privileges
from janito.shell import InteractiveShell
from janito.shell.cmds.write import WriteCmdHandler, get_write_only_tool_schemas
from tests.conftest import assert_command_matching, assert_command_registered

# A fake tool schema pair: one write tool and one read tool.
WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "CreateFile",
        "description": "Create a file",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ReadFile",
        "description": "Read a file",
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


def test_write_command_is_registered():
    assert_command_registered("/write")


def test_handler_name():
    assert WriteCmdHandler().name == "/write"


def test_handle_dispatches_only_write_command():
    assert_command_matching(WriteCmdHandler(), "/write")
    handler = WriteCmdHandler()
    shell = _shell()
    # Similar prefixes are not handled.
    assert handler.handle(shell, "/writes") is False
    assert handler.handle(shell, "/writeup") is False


# ---------------------------------------------------------------------------
# Session privilege switch
# ---------------------------------------------------------------------------


def test_write_switches_session_privileges(monkeypatch):
    """Bare /write sets running_privileges to write-only."""
    handler = WriteCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/write") is True
    assert format_privileges(_privileges_mod.running_privileges) == "w"


def test_write_ignores_extra_text(monkeypatch):
    """Extra text after /write is ignored; the switch still happens."""
    handler = WriteCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/write create a file") is True
    assert format_privileges(_privileges_mod.running_privileges) == "w"


def test_write_prints_confirmation(monkeypatch, capfd):
    handler = WriteCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/write") is True
    out = capfd.readouterr().out
    assert out.strip(), "switch printed nothing"


def test_write_overwrites_previous_privileges(monkeypatch):
    """A previous session level (e.g. r-only) is replaced by write-only."""
    handler = WriteCmdHandler()
    shell = _shell()
    monkeypatch.setattr(
        _privileges_mod,
        "running_privileges",
        _privileges_mod.Privileges(READ=True),
    )
    assert handler.handle(shell, "/write") is True
    assert format_privileges(_privileges_mod.running_privileges) == "w"


# ---------------------------------------------------------------------------
# Write-only schema filtering
# ---------------------------------------------------------------------------


def test_get_write_only_tool_schemas_filters_by_w_permission(monkeypatch):
    """Only tools whose permissions are exactly ``"w"`` are included."""
    permissions = {
        "CreateFile": "w",
        "CreateDirectory": "w",
        "ReadFile": "r",
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

    result = get_write_only_tool_schemas()
    names = [s["function"]["name"] for s in result]
    assert names == ["CreateFile", "CreateDirectory"]
    assert "ReadFile" not in names
    assert "MoveFile" not in names
    assert "RunBashCode" not in names
    assert "LoadSkill" not in names


def test_get_write_only_tool_schemas_empty_without_w_tools(monkeypatch):
    """No write-only tools -> an empty list (equivalent to tools=[])."""
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: {"ReadFile": "r", "RunBashCode": "x"},
    )
    monkeypatch.setattr("janito.tooling.tools_registry.get_all_tool_schemas", lambda: [READ_SCHEMA])

    assert get_write_only_tool_schemas() == []


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-v"]))
