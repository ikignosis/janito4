"""
Tests for the StopTask tool (janito.tools.tasks.stop_task).

The tool delegates task termination to the TaskManager; these tests cover the
tool wiring (schema, discovery, delegation, error handling) with a fake
manager.  Real process termination is covered by tests/core/test_taskmanager.py.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import janito.tools.tasks.stop_task as stop_task_module


class _FakeTaskManager:
    """Minimal stand-in for janito.taskmanager.TaskManager."""

    def __init__(self):
        self.calls = []

    def stop_task(self, task_id):
        self.calls.append(task_id)
        return {"task_id": task_id, "pid": 4242, "stopped": True, "returncode": -15}


def test_run_delegates_to_task_manager(monkeypatch):
    """The tool forwards the task id to the manager and echoes the result."""
    fake = _FakeTaskManager()
    monkeypatch.setattr(stop_task_module, "task_manager", fake)

    result = stop_task_module.StopTask().run(task_id="task-1")

    assert fake.calls == ["task-1"]
    assert result["success"] is True
    assert result["task_id"] == "task-1"
    assert result["stopped"] is True


def test_run_returns_error_on_unknown_task(monkeypatch):
    """An unknown task id surfaces as success=False with the error message."""

    class _BoomManager:
        def stop_task(self, task_id):
            raise KeyError(f"Unknown task id: {task_id}")

    monkeypatch.setattr(stop_task_module, "task_manager", _BoomManager())

    result = stop_task_module.StopTask().run(task_id="missing")

    assert result["success"] is False
    assert result["error"].strip() != ""
    assert result["task_id"] == "missing"


def test_schema_exposes_required_task_id():
    """task_id is a required string in the generated schema."""
    from janito.tooling.schema import get_function_schema
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    schema = get_function_schema(tools["StopTask"])

    params = schema["function"]["parameters"]
    assert params["properties"]["task_id"]["type"] == "integer"
    assert params["required"] == ["task_id"]


def test_discovery_registers_stop_task():
    """The tasks toolset discovery finds the StopTask tool."""
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    assert "StopTask" in tools
