"""
Tests for the /rwx shell command.

``/rwx`` is a bare command that switches the privileges of the whole
session to full access (issue #141). These tests verify the command is
registered, dispatches correctly, switches ``running_privileges``, and that
the read + write + execute tool schema helper still filters by the
``"rwx"`` subset.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito import privileges as _privileges_mod
from janito.privileges import format_privileges
from janito.shell import InteractiveShell
from janito.shell.cmds.rwx import RwxCmdHandler, get_read_write_exec_tool_schemas
from tests.conftest import assert_command_matching, assert_command_registered

# A fake tool schema quartet: one read-only tool, one write tool, one read +
# write tool and one execute tool.
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
MOVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "MoveFile",
        "description": "Move a file",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
EXEC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "RunBashCode",
        "description": "Run a bash command",
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


def test_rwx_command_is_registered():
    assert_command_registered("/rwx")


def test_handler_name():
    assert RwxCmdHandler().name == "/rwx"


def test_handle_dispatches_only_rwx_command():
    assert_command_matching(RwxCmdHandler(), "/rwx")
    handler = RwxCmdHandler()
    shell = _shell()
    # Similar prefixes are not handled.
    assert handler.handle(shell, "/rwxs") is False
    assert handler.handle(shell, "/rwxd") is False


# ---------------------------------------------------------------------------
# Session privilege switch
# ---------------------------------------------------------------------------


def test_rwx_switches_session_privileges(monkeypatch):
    """Bare /rwx sets running_privileges to full access."""
    handler = RwxCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/rwx") is True
    assert format_privileges(_privileges_mod.running_privileges) == "rwx"


def test_rwx_ignores_extra_text(monkeypatch):
    """Extra text after /rwx is ignored; the switch still happens."""
    handler = RwxCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/rwx build the project") is True
    assert format_privileges(_privileges_mod.running_privileges) == "rwx"


def test_rwx_prints_confirmation(monkeypatch, capfd):
    handler = RwxCmdHandler()
    shell = _shell()
    monkeypatch.setattr(_privileges_mod, "running_privileges", _privileges_mod.Privileges())
    assert handler.handle(shell, "/rwx") is True
    out = capfd.readouterr().out
    assert out.strip(), "switch printed nothing"


def test_rwx_overwrites_previous_privileges(monkeypatch):
    """A previous session level (e.g. r-only) is replaced by full access."""
    handler = RwxCmdHandler()
    shell = _shell()
    monkeypatch.setattr(
        _privileges_mod,
        "running_privileges",
        _privileges_mod.Privileges(READ=True),
    )
    assert handler.handle(shell, "/rwx") is True
    assert format_privileges(_privileges_mod.running_privileges) == "rwx"


# ---------------------------------------------------------------------------
# Read + write + execute schema filtering
# ---------------------------------------------------------------------------


def test_get_read_write_exec_tool_schemas_filters_by_rwx_subset(monkeypatch):
    """Every tool declaring a permission subset of ``"rwx"`` is included."""
    permissions = {
        "ReadFile": "r",
        "ListFiles": "r",
        "CreateFile": "w",
        "CreateDirectory": "w",
        "MoveFile": "rw",
        "ReplaceTextInFile": "rw",
        "RunBashCode": "x",
        "RunPythonCode": "x",
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

    result = get_read_write_exec_tool_schemas()
    names = [s["function"]["name"] for s in result]
    assert names == [
        "ReadFile",
        "ListFiles",
        "CreateFile",
        "CreateDirectory",
        "MoveFile",
        "ReplaceTextInFile",
        "RunBashCode",
        "RunPythonCode",
    ]
    assert "LoadSkill" not in names


def test_get_read_write_exec_tool_schemas_empty_without_any_tools(monkeypatch):
    """No tools with declared permissions -> an empty list (equivalent to
    tools=[])."""
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: {"LoadSkill": ""},
    )
    monkeypatch.setattr("janito.tooling.tools_registry.get_all_tool_schemas", lambda: [READ_SCHEMA])

    assert get_read_write_exec_tool_schemas() == []


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-v"]))
