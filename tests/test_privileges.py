"""
Tests for the session privilege model (issue #87).

The registry loads **every** tool whose ``should_load()`` gate passes --
privilege restrictions (``-r``/``-w``/``-x``) are applied by the *session
tool selector* (``get_session_tool_schemas`` / ``get_session_tool_names``)
instead of at discovery time.  This lets the per-command tool modes (``/read``
``/write`` ``/rx`` ``/rw`` ``/rwx``) override the session privileges for a
single exchange: under ``janito -r``, ``/write <msg>`` still offers the
write-only tools.  The execution-time gate (``allowed_tools`` on
``run_tool`` / ``ToolExecutor``) then ensures the model can only call the
tools that were actually offered in the turn.

These tests monkeypatch the module-level registry state (like
``test_tools_registry.py``) so no real filesystem discovery runs.
"""

import json
import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import janito.tooling.tools_registry as tools_registry
from janito import privileges as _privileges_mod
from janito.privileges import Privileges
from janito.tooling.executor import ToolExecutor, extract_tool_names, run_tool


def _fake_tool(name, permissions=""):
    def fake(**kwargs):
        return {"success": True}

    fake.__name__ = name
    fake._tool_permissions = permissions
    return fake


def _fake_schema(name, permissions=""):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Tool {name}",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


@pytest.fixture(autouse=True)
def _restore_privileges():
    """Save/restore the module-level running_privileges around each test."""
    saved = _privileges_mod.running_privileges
    yield
    _privileges_mod.running_privileges = saved


def _patch_registry(monkeypatch, tools):
    """Point the registry state at ``tools`` without running discovery."""
    monkeypatch.setattr(tools_registry, "AVAILABLE_TOOLS", dict(tools))
    monkeypatch.setattr(tools_registry, "_tools_initialized", True)


# ---------------------------------------------------------------------------
# Session tool selector
# ---------------------------------------------------------------------------


def test_session_schemas_none_privileges_allows_everything(monkeypatch):
    """No -r/-w/-x flags -> the session set equals the whole registry."""
    tools = {
        "ReadFile": _fake_tool("ReadFile", "r"),
        "CreateFile": _fake_tool("CreateFile", "w"),
        "RunBashCode": _fake_tool("RunBashCode", "x"),
        "MoveFile": _fake_tool("MoveFile", "rw"),
        "LoadSkill": _fake_tool("LoadSkill", ""),
    }
    _patch_registry(monkeypatch, tools)
    _privileges_mod.running_privileges = None

    names = {s["function"]["name"] for s in tools_registry.get_session_tool_schemas()}
    assert names == set(tools)
    assert tools_registry.get_session_tool_names() == set(tools)


def test_session_schemas_filtered_by_read_only_privileges(monkeypatch):
    """Under -r only read and permission-less tools are offered."""
    tools = {
        "ReadFile": _fake_tool("ReadFile", "r"),
        "CreateFile": _fake_tool("CreateFile", "w"),
        "RunBashCode": _fake_tool("RunBashCode", "x"),
        "MoveFile": _fake_tool("MoveFile", "rw"),
        "LoadSkill": _fake_tool("LoadSkill", ""),
    }
    _patch_registry(monkeypatch, tools)
    _privileges_mod.running_privileges = Privileges(READ=True, WRITE=False, EXEC=False)

    names = {s["function"]["name"] for s in tools_registry.get_session_tool_schemas()}
    assert names == {"ReadFile", "LoadSkill"}
    assert tools_registry.get_session_tool_names() == {"ReadFile", "LoadSkill"}


def test_session_schemas_filtered_by_read_write_privileges(monkeypatch):
    """Under -rw, read/write and permission-less tools are offered, exec is not."""
    tools = {
        "ReadFile": _fake_tool("ReadFile", "r"),
        "CreateFile": _fake_tool("CreateFile", "w"),
        "RunBashCode": _fake_tool("RunBashCode", "x"),
        "MoveFile": _fake_tool("MoveFile", "rw"),
        "LoadSkill": _fake_tool("LoadSkill", ""),
    }
    _patch_registry(monkeypatch, tools)
    _privileges_mod.running_privileges = Privileges(READ=True, WRITE=True, EXEC=False)

    names = {s["function"]["name"] for s in tools_registry.get_session_tool_schemas()}
    assert names == {"ReadFile", "CreateFile", "MoveFile", "LoadSkill"}


# ---------------------------------------------------------------------------
# Override commands offer the full registry subset (issue #87)
# ---------------------------------------------------------------------------


def test_write_cmd_offers_write_tools_under_read_only_privileges(monkeypatch):
    """Under -r, /write still offers the write-only tools (explicit override)."""
    from janito.shell.cmds.write import get_write_only_tool_schemas

    permissions = {
        "ReadFile": "r",
        "CreateFile": "w",
        "CreateDirectory": "w",
        "RunBashCode": "x",
        "MoveFile": "rw",
        "LoadSkill": "",
    }
    schemas = [_fake_schema(name, perms) for name, perms in sorted(permissions.items())]
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: permissions,
    )
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_schemas", lambda: schemas
    )
    _privileges_mod.running_privileges = Privileges(READ=True, WRITE=False, EXEC=False)

    names = [s["function"]["name"] for s in get_write_only_tool_schemas()]
    assert names == ["CreateDirectory", "CreateFile"]


def test_rwx_cmd_offers_everything_under_read_only_privileges(monkeypatch):
    """Under -r, /rwx still offers every built-in tool (explicit override)."""
    from janito.shell.cmds.rwx import get_read_write_exec_tool_schemas

    permissions = {"ReadFile": "r", "CreateFile": "w", "RunBashCode": "x"}
    schemas = [_fake_schema(name, perms) for name, perms in sorted(permissions.items())]
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: permissions,
    )
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_schemas", lambda: schemas
    )
    _privileges_mod.running_privileges = Privileges(READ=True, WRITE=False, EXEC=False)

    names = {s["function"]["name"] for s in get_read_write_exec_tool_schemas()}
    assert names == {"ReadFile", "CreateFile", "RunBashCode"}


def test_rx_cmd_excludes_write_tools(monkeypatch):
    """/rx offers exactly the read + execute tools, never write."""
    from janito.shell.cmds.rx import get_read_exec_tool_schemas

    permissions = {"ReadFile": "r", "RunBashCode": "x", "CreateFile": "w"}
    schemas = [_fake_schema(name, perms) for name, perms in sorted(permissions.items())]
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: permissions,
    )
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_schemas", lambda: schemas
    )

    names = {s["function"]["name"] for s in get_read_exec_tool_schemas()}
    assert names == {"ReadFile", "RunBashCode"}


# ---------------------------------------------------------------------------
# Execution-time gate
# ---------------------------------------------------------------------------


def test_run_tool_rejects_tool_not_offered():
    """A call to a tool outside the turn's offered set is rejected."""
    result, error, exec_ms = run_tool(
        "CreateFile", {"filepath": "/tmp/x"}, allowed_tools={"ReadFile"}
    )
    assert error is not None
    assert "not offered in this turn" in error
    assert result["success"] is False
    assert exec_ms == 0


def test_run_tool_allows_offered_tool(monkeypatch):
    """A call to an offered tool executes normally."""
    calls = []

    def fake_tool(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "janito.tooling.executor.get_tool_by_name", lambda name: fake_tool
    )
    result, error, _ = run_tool(
        "ReadFile", {"path": "/tmp/x"}, allowed_tools={"ReadFile"}
    )
    assert error is None
    assert result == {"success": True}
    assert calls == [{"path": "/tmp/x"}]


def test_run_tool_no_gate_by_default(monkeypatch):
    """allowed_tools=None keeps the old behaviour (no gating)."""
    calls = []

    def fake_tool(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setattr(
        "janito.tooling.executor.get_tool_by_name", lambda name: fake_tool
    )
    result, error, _ = run_tool("Whatever", {"x": 1})
    assert error is None
    assert result == {"success": True}
    assert calls == [{"x": 1}]


def test_tool_executor_gates_calls(monkeypatch):
    """ToolExecutor forwards allowed_tools to the shared core."""
    ex = ToolExecutor(allowed_tools={"ReadFile"})
    call = {
        "id": "call_1",
        "function": {"name": "CreateFile", "arguments": "{}"},
    }
    msg = ex.execute_tool_call(call)
    assert msg["role"] == "tool"
    result = json.loads(msg["content"])
    assert result["success"] is False
    assert "not offered in this turn" in result["error"]


# ---------------------------------------------------------------------------
# Privilege override warning
# ---------------------------------------------------------------------------


def test_warn_if_privilege_override_prints_warning(monkeypatch, capsys):
    from janito.shell.cmds._tool_filters import warn_if_privilege_override

    _privileges_mod.running_privileges = Privileges(READ=True, WRITE=False, EXEC=False)
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_session_tool_names",
        lambda: {"ReadFile"},
    )

    warn_if_privilege_override([_fake_schema("CreateFile", "w")])
    out = capsys.readouterr().out
    assert "overrides the session privileges" in out
    assert "this turn" in out
    # The tool names are intentionally not listed in the note.
    assert "CreateFile" not in out


def test_warn_if_privilege_override_silent_within_privileges(monkeypatch, capsys):
    from janito.shell.cmds._tool_filters import warn_if_privilege_override

    _privileges_mod.running_privileges = Privileges(READ=True, WRITE=False, EXEC=False)
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_session_tool_names",
        lambda: {"ReadFile"},
    )

    warn_if_privilege_override([_fake_schema("ReadFile", "r")])
    assert capsys.readouterr().out == ""


def test_warn_if_privilege_override_silent_with_full_privileges(capsys):
    from janito.shell.cmds._tool_filters import warn_if_privilege_override

    _privileges_mod.running_privileges = None
    warn_if_privilege_override([_fake_schema("CreateFile", "w")])
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_extract_tool_names_handles_both_formats():
    assert extract_tool_names(
        [{"type": "function", "function": {"name": "ReadFile"}}]
    ) == {"ReadFile"}
    # Responses / Anthropic top-level shape.
    assert extract_tool_names([{"name": "ReadFile", "input_schema": {}}]) == {
        "ReadFile"
    }
    assert extract_tool_names(None) == set()
    assert extract_tool_names([]) == set()


def test_privilege_restriction_reason():
    from janito.tools import privilege_restriction_reason

    _privileges_mod.running_privileges = Privileges(READ=True, WRITE=False, EXEC=False)
    assert privilege_restriction_reason("r") is None
    assert privilege_restriction_reason("") is None
    assert privilege_restriction_reason("w") == (
        "insufficient privileges: requires 'w' (WRITE)"
    )
    assert privilege_restriction_reason("rw") == (
        "insufficient privileges: requires 'w' (WRITE)"
    )
    assert "requires 'w' (WRITE)" in privilege_restriction_reason("rwx")
    assert "'x' (EXEC)" in privilege_restriction_reason("rwx")


def test_tool_is_allowed_by_privileges():
    from janito.tools import tool_is_allowed_by_privileges

    _privileges_mod.running_privileges = None
    assert tool_is_allowed_by_privileges("rwx") is True
    _privileges_mod.running_privileges = Privileges(READ=True, WRITE=False, EXEC=False)
    assert tool_is_allowed_by_privileges("r") is True
    assert tool_is_allowed_by_privileges("w") is False
    assert tool_is_allowed_by_privileges("rw") is False
    assert tool_is_allowed_by_privileges("") is True


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-v"]))
