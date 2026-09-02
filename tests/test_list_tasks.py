"""
Tests for the ListTasks tool (janito.tools.tasks.list_tasks).

The tool delegates the snapshot to the TaskManager; these tests cover the
tool wiring (schema, discovery, delegation, running_only filtering) with a
fake manager.  Real snapshot ordering/content is covered by
tests/test_taskmanager.py.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import janito.tools.tasks.list_tasks as list_tasks_module

_RUNNING = {
    "task_id": "abc123",
    "summary": "still going",
    "state": "running",
    "running": True,
    "pid": 111,
    "working_dir": "/tmp/wd",
    "duration_seconds": 3.5,
}
_FINISHED = {
    "task_id": "def456",
    "summary": "all done",
    "state": "finished",
    "running": False,
    "pid": 222,
    "working_dir": "/tmp/wd",
    "duration_seconds": 12.0,
}


class _FakeTaskManager:
    """Minimal stand-in for janito.taskmanager.TaskManager."""

    def __init__(self, tasks):
        self.tasks = tasks

    def list_tasks(self):
        return list(self.tasks)

    def running_tasks(self):
        return [t for t in self.tasks if t["running"]]


def test_run_returns_running_and_finished_tasks(monkeypatch):
    """Without running_only, the snapshot includes finished tasks too."""
    fake = _FakeTaskManager([_RUNNING, _FINISHED])
    monkeypatch.setattr(list_tasks_module, "task_manager", fake)

    result = list_tasks_module.ListTasks().run()

    assert result["success"] is True
    assert result["count"] == 2
    assert result["running_count"] == 1
    assert [t["task_id"] for t in result["tasks"]] == ["abc123", "def456"]
    assert [t["state"] for t in result["tasks"]] == ["running", "finished"]


def test_run_running_only_filters(monkeypatch):
    """running_only=True returns only the tasks still running."""
    fake = _FakeTaskManager([_RUNNING, _FINISHED])
    monkeypatch.setattr(list_tasks_module, "task_manager", fake)

    result = list_tasks_module.ListTasks().run(running_only=True)

    assert result["success"] is True
    assert result["count"] == 1
    assert result["running_count"] == 1
    assert [t["task_id"] for t in result["tasks"]] == ["abc123"]
    assert result["tasks"][0]["running"] is True


def test_run_empty_manager(monkeypatch):
    """No tasks at all: success with empty lists and zero counts."""
    monkeypatch.setattr(list_tasks_module, "task_manager", _FakeTaskManager([]))

    result = list_tasks_module.ListTasks().run()

    assert result["success"] is True
    assert result["tasks"] == []
    assert result["count"] == 0
    assert result["running_count"] == 0


def test_run_returns_error_on_manager_failure(monkeypatch):
    """A manager failure surfaces as success=False with the error message."""

    class _BoomManager:
        def list_tasks(self):
            raise RuntimeError("snapshot failed")

    monkeypatch.setattr(list_tasks_module, "task_manager", _BoomManager())

    result = list_tasks_module.ListTasks().run()

    assert result["success"] is False
    assert "snapshot failed" in result["error"]


def test_schema_has_no_required_args():
    """ListTasks takes only the optional running_only flag."""
    from janito.tooling.schema import get_function_schema
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    schema = get_function_schema(tools["ListTasks"])

    params = schema["function"]["parameters"]
    assert "running_only" in params["properties"]
    assert params["properties"]["running_only"]["type"] == "boolean"
    assert params["required"] == []


def test_discovery_registers_list_tasks():
    """The tasks toolset discovery finds the ListTasks tool."""
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    assert "ListTasks" in tools
