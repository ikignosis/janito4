"""
Tests for the GetTaskInfo tool (janito.tools.tasks.get_task_info).

The tool delegates to TaskManager.get_task_info; these tests cover the
tool wiring (schema, discovery, delegation, error handling) with a fake
manager. Manager-level detail snapshots are covered by
tests/core/test_taskmanager.py.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import janito.tools.tasks.get_task_info as get_task_info_module


def _info(task_id=1, **overrides):
    info = {
        "task_id": task_id,
        "summary": f"summary of {task_id}",
        "description": f"do thing {task_id}",
        "working_dir": "/tmp",
        "privileges": "r",
        "pid": 4242,
        "stdout_filename": f"/tmp/janito-{task_id}.out",
        "stderr_filename": f"/tmp/janito-{task_id}.err",
        "timeout": None,
        "state": "running",
        "running": True,
        "exit_reason": "running",
        "exit_code": None,
        "returncode": None,
        "duration_seconds": 1.5,
        "error": None,
    }
    info.update(overrides)
    return info


class _FakeTaskManager:
    """Minimal stand-in for janito.taskmanager.TaskManager."""

    def __init__(self, info=None):
        self.calls = []
        self._info = info if info is not None else _info()

    def get_task_info(self, task_id):
        self.calls.append(task_id)
        return dict(self._info, task_id=task_id)


def test_run_delegates_to_task_manager(monkeypatch):
    """The tool forwards the task id and returns the full detail dict."""
    fake = _FakeTaskManager()
    monkeypatch.setattr(get_task_info_module, "task_manager", fake)

    result = get_task_info_module.GetTaskInfo().run(task_id=7)

    assert fake.calls == [7]
    assert result["success"] is True
    assert result["task_id"] == 7
    assert result["description"] == "do thing 1"
    assert result["stdout_filename"] == "/tmp/janito-1.out"
    assert result["stderr_filename"] == "/tmp/janito-1.err"


def test_run_returns_error_on_unknown_task(monkeypatch):
    """An unknown task id surfaces as success=False with the error message."""

    class _BoomManager:
        def get_task_info(self, task_id):
            raise KeyError(f"Unknown task id: {task_id}")

    monkeypatch.setattr(get_task_info_module, "task_manager", _BoomManager())

    result = get_task_info_module.GetTaskInfo().run(task_id="missing")

    assert result["success"] is False
    assert "Unknown task id" in result["error"]
    assert result["task_id"] == "missing"


def test_schema_exposes_required_task_id():
    """task_id is a required integer in the generated schema."""
    from janito.tooling.schema import get_function_schema
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    schema = get_function_schema(tools["GetTaskInfo"])

    params = schema["function"]["parameters"]
    assert params["properties"]["task_id"]["type"] == "integer"
    assert params["required"] == ["task_id"]


def test_discovery_registers_get_task_info():
    """The tasks toolset discovery finds the GetTaskInfo tool."""
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    assert "GetTaskInfo" in tools
