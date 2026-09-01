"""
Tests for the WaitForTask tool (janito.tools.tasks.wait_for_task).

The tool delegates task waiting to the TaskManager; these tests cover the
tool wiring (schema, discovery, delegation, error handling) with a fake
manager.  Real process waiting is covered by tests/test_taskmanager.py.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import janito.tools.tasks.wait_for_task as wait_for_task_module


class _FakeTaskManager:
    """Minimal stand-in for janito.taskmanager.TaskManager."""

    def __init__(self):
        self.calls = []
        self.callbacks = []
        self.timeouts = []
        self.max_output_lines = []

    def wait_for_task(
        self, task_ids, on_task_complete=None, timeout=None, max_output_lines=None
    ):
        self.calls.append(task_ids)
        self.callbacks.append(on_task_complete)
        self.timeouts.append(timeout)
        self.max_output_lines.append(max_output_lines)
        tasks = [
            {
                "task_id": task_id,
                "pid": 4242,
                "returncode": 0,
                "stdout_filename": f"/tmp/janito-{task_id}.out",
                "stderr_filename": f"/tmp/janito-{task_id}.err",
                "stdout": f"output of {task_id}",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "error": None,
            }
            for task_id in task_ids
        ]
        if on_task_complete is not None:
            for task in tasks:
                on_task_complete(task)
        return {
            "tasks": tasks,
            "timed_out": False,
            "pending_task_ids": [],
        }


def test_run_delegates_to_task_manager(monkeypatch):
    """The tool forwards the task ids to the manager and echoes the results."""
    fake = _FakeTaskManager()
    monkeypatch.setattr(wait_for_task_module, "task_manager", fake)

    result = wait_for_task_module.WaitForTask().run(task_ids=["task-1", "task-2"])

    assert fake.calls == [["task-1", "task-2"]]
    assert result["success"] is True
    assert len(result["tasks"]) == 2
    assert result["tasks"][0]["task_id"] == "task-1"
    assert result["tasks"][0]["returncode"] == 0


def test_run_passes_on_task_complete_callback(monkeypatch):
    """The tool passes a per-task completion callback to the manager."""
    fake = _FakeTaskManager()
    monkeypatch.setattr(wait_for_task_module, "task_manager", fake)

    wait_for_task_module.WaitForTask().run(task_ids=["task-1"])

    assert fake.callbacks[0] is not None


def test_run_forwards_timeout(monkeypatch):
    """The tool forwards the timeout value to the manager."""
    fake = _FakeTaskManager()
    monkeypatch.setattr(wait_for_task_module, "task_manager", fake)

    wait_for_task_module.WaitForTask().run(task_ids=["task-1"], timeout=12.5)

    assert fake.timeouts == [12.5]


def test_run_forwards_max_lines(monkeypatch):
    """The tool forwards max_lines to the manager as max_output_lines."""
    fake = _FakeTaskManager()
    monkeypatch.setattr(wait_for_task_module, "task_manager", fake)

    wait_for_task_module.WaitForTask().run(task_ids=["task-1"], max_lines=50)

    assert fake.max_output_lines == [50]


def test_run_default_max_lines_is_none(monkeypatch):
    """Without max_lines, the manager's default cap is used (None)."""
    fake = _FakeTaskManager()
    monkeypatch.setattr(wait_for_task_module, "task_manager", fake)

    wait_for_task_module.WaitForTask().run(task_ids=["task-1"])

    assert fake.max_output_lines == [None]


def test_run_surfaces_inline_output(monkeypatch):
    """Each finished task's stdout/stderr content is returned inline."""
    fake = _FakeTaskManager()
    monkeypatch.setattr(wait_for_task_module, "task_manager", fake)

    result = wait_for_task_module.WaitForTask().run(task_ids=["task-1"])

    entry = result["tasks"][0]
    assert entry["stdout"] == "output of task-1"
    assert entry["stderr"] == ""
    assert entry["stdout_truncated"] is False
    assert entry["stderr_truncated"] is False


def test_run_surfaces_timed_out_results(monkeypatch):
    """A timed-out manager result surfaces timed_out and pending ids."""

    class _TimeoutManager:
        def wait_for_task(
            self, task_ids, on_task_complete=None, timeout=None, max_output_lines=None
        ):
            return {
                "tasks": [],
                "timed_out": True,
                "pending_task_ids": list(task_ids),
            }

    monkeypatch.setattr(wait_for_task_module, "task_manager", _TimeoutManager())

    result = wait_for_task_module.WaitForTask().run(
        task_ids=["task-1", "task-2"], timeout=1
    )

    assert result["success"] is True
    assert result["timed_out"] is True
    assert result["pending_task_ids"] == ["task-1", "task-2"]


def test_run_returns_error_on_unknown_task(monkeypatch):
    """An unknown task id surfaces as success=False with the error message."""

    class _BoomManager:
        def wait_for_task(
            self, task_ids, on_task_complete=None, timeout=None, max_output_lines=None
        ):
            raise KeyError(f"Unknown task id: {task_ids[0]}")

    monkeypatch.setattr(wait_for_task_module, "task_manager", _BoomManager())

    result = wait_for_task_module.WaitForTask().run(task_ids=["missing"])

    assert result["success"] is False
    assert "Unknown task id" in result["error"]
    assert result["task_ids"] == ["missing"]


def test_schema_exposes_required_task_ids():
    """task_ids is required; timeout is an optional number in the schema."""
    from janito.tooling.schema import get_function_schema
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    schema = get_function_schema(tools["WaitForTask"])

    params = schema["function"]["parameters"]
    assert params["properties"]["task_ids"]["type"] == "array"
    assert params["properties"]["task_ids"]["items"]["type"] == "string"
    assert params["properties"]["timeout"]["type"] == "number"
    assert params["required"] == ["task_ids"]


def test_discovery_registers_wait_for_task():
    """The tasks toolset discovery finds the WaitForTask tool."""
    from janito.tools import discover_toolsets

    tools = discover_toolsets(["tasks"])
    assert "WaitForTask" in tools


# --- spinner path ----------------------------------------------------------


def test_should_show_spinner_false_on_non_tty(monkeypatch):
    """A non-interactive stderr (pipes, CI) never animates the wait."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    assert wait_for_task_module._should_show_spinner() is False


def test_should_show_spinner_false_with_report_handler(monkeypatch):
    """Web mode (a report handler is installed) never animates the wait."""
    from janito.tooling.reporter import set_report_handler

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    set_report_handler(lambda level, message, end: None)
    try:
        assert wait_for_task_module._should_show_spinner() is False
    finally:
        set_report_handler(None)


def test_should_show_spinner_true_on_tty_without_handler(monkeypatch):
    """An interactive terminal with no report handler gets the spinner."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert wait_for_task_module._should_show_spinner() is True


def test_spinner_path_reports_per_task_completions(monkeypatch):
    """With the spinner active, each task is announced and completion reported."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    fake = _FakeTaskManager()
    monkeypatch.setattr(wait_for_task_module, "task_manager", fake)

    tool = wait_for_task_module.WaitForTask()
    messages = []
    tool.report_result = lambda msg, end="\n": messages.append(msg)
    starts = []
    tool.report_start = lambda msg, end="\n": starts.append(msg)

    result = tool.run(task_ids=["task-1", "task-2"])

    assert result["success"] is True
    assert len(result["tasks"]) == 2
    # Per-task completion lines print as tasks finish, then the final
    # summary; the static "Waiting for N tasks" line is replaced by the
    # spinner, but each task is still announced up front with its summary
    # (the fake manager has no summaries, so the task ids are shown).
    assert messages == [
        "task task-1 complete",
        "task task-2 complete",
        "all tasks finished",
    ]
    assert starts == [
        "Waiting for task 1/2 : task-1",
        "Waiting for task 2/2 : task-2",
    ]


def test_spinner_path_surfaces_manager_errors(monkeypatch):
    """A manager error under the spinner surfaces as success=False."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)

    class _BoomManager:
        def wait_for_task(
            self, task_ids, on_task_complete=None, timeout=None, max_output_lines=None
        ):
            raise KeyError(f"Unknown task id: {task_ids[0]}")

    monkeypatch.setattr(wait_for_task_module, "task_manager", _BoomManager())

    tool = wait_for_task_module.WaitForTask()
    tool.report_result = lambda msg, end="\n": None

    result = tool.run(task_ids=["missing"])

    assert result["success"] is False
    assert "Unknown task id" in result["error"]


def test_fallback_path_keeps_start_line_on_non_tty(monkeypatch):
    """Piped output keeps the per-task 'Waiting for task n/total' start lines."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    fake = _FakeTaskManager()
    monkeypatch.setattr(wait_for_task_module, "task_manager", fake)

    tool = wait_for_task_module.WaitForTask()
    starts = []
    tool.report_start = lambda msg, end="\n": starts.append((msg, end))

    tool.run(task_ids=["task-1"])

    assert starts == [("Waiting for task 1/1 : task-1", "\n")]


def test_start_lines_use_task_summary(monkeypatch):
    """When the manager exposes task summaries, they appear in the start lines."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

    class _Task:
        def __init__(self, summary):
            self.summary = summary

    class _ManagerWithSummaries(_FakeTaskManager):
        def get_task(self, task_id):
            return _Task(summary=f"summary of {task_id}")

    monkeypatch.setattr(wait_for_task_module, "task_manager", _ManagerWithSummaries())

    tool = wait_for_task_module.WaitForTask()
    starts = []
    tool.report_start = lambda msg, end="\n": starts.append(msg)

    tool.run(task_ids=["task-1", "task-2"])

    assert starts == [
        "Waiting for task 1/2 : summary of task-1",
        "Waiting for task 2/2 : summary of task-2",
    ]


def test_start_lines_fall_back_to_id_for_unknown_task(monkeypatch):
    """An unknown task id falls back to the id in the start line."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

    class _ManagerWithoutTask:
        def wait_for_task(
            self, task_ids, on_task_complete=None, timeout=None, max_output_lines=None
        ):
            return {
                "tasks": [],
                "timed_out": False,
                "pending_task_ids": [],
            }

    monkeypatch.setattr(wait_for_task_module, "task_manager", _ManagerWithoutTask())

    tool = wait_for_task_module.WaitForTask()
    starts = []
    tool.report_start = lambda msg, end="\n": starts.append(msg)

    tool.run(task_ids=["task-1"])

    assert starts == ["Waiting for task 1/1 : task-1"]
