"""
Tests for the StartTask tool (janito.tools.tasks.start_task).

The tool delegates to the TaskManager: it pipes the task description to a
fresh janito sub-process and returns the task id / pid / output file names.
These tests exercise the tool wiring (schema, discovery, delegation, error
handling) with a fake manager; the real sub-process behaviour is covered by
tests/test_taskmanager.py.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import janito.tools.tasks.start_task as start_task_module


class _FakeTaskManager:
    """Minimal stand-in for janito.taskmanager.TaskManager."""

    def __init__(self):
        self.calls = []

    def start_task(
        self,
        description,
        working_dir=None,
        privileges=None,
        summary=None,
        timeout=None,
    ):
        # Mirror the real manager's contract so the tool's error path can be
        # exercised without spawning a subprocess.
        if timeout is not None and timeout <= 0:
            raise ValueError(
                f"timeout must be a positive number of seconds, got {timeout:g}"
            )
        # Records kwargs (not a positional tuple) so adding a manager argument
        # does not churn every assertion in this file.
        self.calls.append(
            {
                "description": description,
                "working_dir": working_dir,
                "privileges": privileges,
                "summary": summary,
                "timeout": timeout,
            }
        )
        return {
            "task_id": "task-1",
            "pid": 4242,
            "stdout_filename": "/tmp/janito-task-1.out",
            "stderr_filename": "/tmp/janito-task-1.err",
            "working_dir": working_dir or "/cwd",
            "summary": summary,
            "timeout": timeout,
        }


def _install_fake_manager(monkeypatch):
    fake = _FakeTaskManager()
    monkeypatch.setattr(start_task_module, "task_manager", fake)
    return fake


def test_run_delegates_to_task_manager(monkeypatch):
    """The tool forwards summary/description/working_dir/privileges to the manager."""
    fake = _install_fake_manager(monkeypatch)

    result = start_task_module.StartTask().run(
        summary="Fix the login page",
        description="Fix the login page",
        working_dir="/tmp/project",
        privileges="rwx",
    )

    assert fake.calls == [
        {
            "description": "Fix the login page",
            "working_dir": "/tmp/project",
            "privileges": "rwx",
            "summary": "Fix the login page",
            "timeout": None,
        }
    ]
    assert result["success"] is True
    assert result["task_id"] == "task-1"
    assert result["pid"] == 4242
    assert result["stdout_filename"] == "/tmp/janito-task-1.out"
    assert result["stderr_filename"] == "/tmp/janito-task-1.err"
    assert result["summary"] == "Fix the login page"
    assert result["description"] == "Fix the login page"
    assert result["working_dir"] == "/tmp/project"
    assert result["privileges"] == "rwx"


def test_run_defaults_to_current_turn_privileges(monkeypatch):
    """Without an explicit privileges arg, StartTask mirrors the current turn."""
    fake = _install_fake_manager(monkeypatch)
    from janito.tooling.turn_privileges import (
        reset_turn_privileges,
        set_turn_privileges,
    )

    token = set_turn_privileges("rx")  # e.g. a /rx turn
    try:
        result = start_task_module.StartTask().run(
            summary="Write docs", description="Write docs"
        )
    finally:
        reset_turn_privileges(token)

    assert fake.calls == [
        {
            "description": "Write docs",
            "working_dir": None,
            "privileges": "rx",
            "summary": "Write docs",
            "timeout": None,
        }
    ]
    assert result["success"] is True
    assert result["working_dir"] == "/cwd"
    assert result["privileges"] == "rx"


def test_run_defaults_to_session_privileges(monkeypatch):
    """Outside a restricted turn, StartTask mirrors the session privileges."""
    fake = _install_fake_manager(monkeypatch)
    import janito.privileges as _privileges_mod
    from janito.privileges import Privileges

    monkeypatch.setattr(
        _privileges_mod,
        "running_privileges",
        Privileges(READ=True, WRITE=True),
    )

    result = start_task_module.StartTask().run(
        summary="Write docs", description="Write docs"
    )

    assert fake.calls == [
        {
            "description": "Write docs",
            "working_dir": None,
            "privileges": "rw",
            "summary": "Write docs",
            "timeout": None,
        }
    ]
    assert result["privileges"] == "rw"


def test_run_explicit_privileges_override_turn(monkeypatch):
    """An explicit privileges argument beats the current turn's privileges."""
    fake = _install_fake_manager(monkeypatch)
    from janito.tooling.turn_privileges import (
        reset_turn_privileges,
        set_turn_privileges,
    )

    token = set_turn_privileges("r")
    try:
        result = start_task_module.StartTask().run(
            summary="Write docs", description="Write docs", privileges="rwx"
        )
    finally:
        reset_turn_privileges(token)

    assert fake.calls == [
        {
            "description": "Write docs",
            "working_dir": None,
            "privileges": "rwx",
            "summary": "Write docs",
            "timeout": None,
        }
    ]
    assert result["privileges"] == "rwx"


def test_run_returns_error_on_failure(monkeypatch):
    """Manager failures surface as success=False with the error message."""

    class _BoomManager:
        def start_task(
            self,
            description,
            working_dir=None,
            privileges=None,
            summary=None,
            timeout=None,
        ):
            raise ValueError("working_dir is not a directory: /nope")

    monkeypatch.setattr(start_task_module, "task_manager", _BoomManager())

    result = start_task_module.StartTask().run(
        summary="Do something", description="Do something", working_dir="/nope"
    )

    assert result["success"] is False
    assert "working_dir is not a directory" in result["error"]
    assert result["summary"] == "Do something"
    assert result["description"] == "Do something"
    assert result["working_dir"] == "/nope"


def test_run_forwards_timeout(monkeypatch):
    """The lifetime cap is forwarded to the manager and echoed back."""
    fake = _install_fake_manager(monkeypatch)

    result = start_task_module.StartTask().run(
        summary="Index the repo", description="Index the repo", timeout=120
    )

    assert fake.calls[0]["timeout"] == 120
    assert result["success"] is True
    assert result["timeout"] == 120


def test_run_timeout_defaults_to_none(monkeypatch):
    """Without a timeout the manager gets None (no cap) and results echo it."""
    fake = _install_fake_manager(monkeypatch)

    result = start_task_module.StartTask().run(summary="No cap", description="No cap")

    assert fake.calls[0]["timeout"] is None
    assert result["timeout"] is None


def test_run_rejects_non_positive_timeout(monkeypatch):
    """A bad cap surfaces as success=False (the manager validates it)."""
    _install_fake_manager(monkeypatch)

    result = start_task_module.StartTask().run(
        summary="Bad cap", description="Bad cap", timeout=0
    )

    assert result["success"] is False
    assert "timeout must be a positive number" in result["error"]
    assert result["timeout"] == 0


def test_schema_exposes_parameters():
    """summary and description are required; working_dir/privileges are optional."""
    from janito.tooling.schema import get_function_schema
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    schema = get_function_schema(tools["StartTask"])

    params = schema["function"]["parameters"]
    props = params["properties"]
    assert props["summary"]["type"] == "string"
    assert props["description"]["type"] == "string"
    assert props["working_dir"]["type"] == "string"
    assert props["privileges"]["type"] == "string"
    # The timeout is an optional number: tasks run uncapped unless asked for.
    assert props["timeout"]["type"] == "number"
    assert sorted(params["required"]) == ["description", "summary"]


def test_discovery_registers_start_task():
    """The tasks toolset discovery finds the StartTask tool."""
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    assert "StartTask" in tools
