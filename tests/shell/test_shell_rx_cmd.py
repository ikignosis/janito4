"""
Tests for the /rx shell command.

``/rx`` is a bare command that switches the privileges of the whole
session to read + execute (issue #141). These tests verify the command is
registered, dispatches correctly, switches ``running_privileges``, and that
the read + execute tool schema helper still filters by the ``"r"``/``"x"``
permissions.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito import privileges as _privileges_mod
from janito.privileges import format_privileges
from janito.shell import InteractiveShell
from janito.shell.cmds.rx import RxCmdHandler, get_read_exec_tool_schemas
from tests.conftest import assert_command_matching, assert_command_registered

# A fake tool schema trio: one read-only tool, one execute tool and one
# write tool.
READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ReadFile",
        "description": "Read a file",
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


def test_rx_command_is_registered():
    assert_command_registered("/rx")


def test_handler_name():
    assert RxCmdHandler().name == "/rx"


def test_handle_dispatches_only_rx_command():
    assert_command_matching(RxCmdHandler(), "/rx")
    handler = RxCmdHandler()
    shell = _shell()
    # Similar prefixes are not handled.
    assert handler.handle(shell, "/rxs") is False
    assert handler.handle(shell, "/rxd") is False


# ---------------------------------------------------------------------------
# Session privilege switch
# ---------------------------------------------------------------------------


def test_rx_switches_session_privileges(monkeypatch):
    """Bare /rx sets running_privileges to read + execute."""
    handler = RxCmdHandler()
    shell = _shell()
    monkeypatch.setattr(
        _privileges_mod, "running_privileges", _privileges_mod.Privileges()
    )
    assert handler.handle(shell, "/rx") is True
    assert format_privileges(_privileges_mod.running_privileges) == "rx"


def test_rx_ignores_extra_text(monkeypatch):
    """Extra text after /rx is ignored; the switch still happens."""
    handler = RxCmdHandler()
    shell = _shell()
    monkeypatch.setattr(
        _privileges_mod, "running_privileges", _privileges_mod.Privileges()
    )
    assert handler.handle(shell, "/rx list the files") is True
    assert format_privileges(_privileges_mod.running_privileges) == "rx"


def test_rx_prints_confirmation(monkeypatch, capfd):
    handler = RxCmdHandler()
    shell = _shell()
    monkeypatch.setattr(
        _privileges_mod, "running_privileges", _privileges_mod.Privileges()
    )
    assert handler.handle(shell, "/rx") is True
    out = capfd.readouterr().out
    assert out.strip(), "switch printed nothing"


def test_rx_overwrites_previous_privileges(monkeypatch):
    """A previous session level (e.g. r-only) is replaced by read + execute."""
    handler = RxCmdHandler()
    shell = _shell()
    monkeypatch.setattr(
        _privileges_mod,
        "running_privileges",
        _privileges_mod.Privileges(READ=True),
    )
    assert handler.handle(shell, "/rx") is True
    assert format_privileges(_privileges_mod.running_privileges) == "rx"


# ---------------------------------------------------------------------------
# Read + execute schema filtering
# ---------------------------------------------------------------------------


def test_get_read_exec_tool_schemas_filters_by_r_and_x_permissions(monkeypatch):
    """Only tools whose permissions are exactly ``"r"`` or ``"x"`` are included."""
    permissions = {
        "ReadFile": "r",
        "ListFiles": "r",
        "RunBashCode": "x",
        "RunPythonCode": "x",
        "CreateFile": "w",
        "MoveFile": "rw",
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
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_schemas", lambda: schemas
    )

    result = get_read_exec_tool_schemas()
    names = [s["function"]["name"] for s in result]
    assert names == ["ReadFile", "ListFiles", "RunBashCode", "RunPythonCode"]
    assert "CreateFile" not in names
    assert "MoveFile" not in names
    assert "LoadSkill" not in names


def test_get_read_exec_tool_schemas_empty_without_r_or_x_tools(monkeypatch):
    """No read/execute tools -> an empty list (equivalent to tools=[])."""
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: {"CreateFile": "w", "MoveFile": "rw"},
    )
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_schemas", lambda: [WRITE_SCHEMA]
    )

    assert get_read_exec_tool_schemas() == []


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-v"]))
