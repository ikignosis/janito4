"""Tests for the /tasks shell command (issue #130).

/tasks renders TaskManager.list_tasks() as a Rich table including the
execution time (duration_seconds) already tracked by the manager.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.conftest import assert_command_matching, assert_command_registered


def _handler():
    from janito.shell.cmds.tasks import TasksCmdHandler

    return TasksCmdHandler()


def _rows():
    return [
        {
            "task_id": 1,
            "summary": "running job",
            "state": "running",
            "running": True,
            "pid": 111,
            "working_dir": "/tmp/wd",
            "duration_seconds": 3.5,
            "exit_code": None,
            "error": None,
        },
        {
            "task_id": 2,
            "summary": "done job",
            "state": "finished",
            "running": False,
            "pid": 222,
            "working_dir": "/tmp/wd",
            "duration_seconds": 12.0,
            "exit_code": 0,
            "error": None,
        },
    ]


def test_format_duration():
    from janito.shell.cmds import tasks as tasks_cmd

    assert tasks_cmd._format_duration({"duration_seconds": 3.5}) == "3.5s"
    assert tasks_cmd._format_duration({"duration_seconds": None}) == "-"
    assert tasks_cmd._format_duration({}) == "-"


def test_tasks_registered_and_matching():
    assert_command_registered("/tasks")
    assert_command_matching(_handler(), "/tasks")


def test_tasks_table_shows_durations(monkeypatch, capfd):
    import janito.shell.cmds.tasks as tasks_cmd

    monkeypatch.setattr(tasks_cmd.task_manager, "list_tasks", lambda: _rows())

    assert _handler().handle(None, "/tasks") is True
    out = capfd.readouterr().out
    assert out.strip() != ""
    assert out.strip() != ""
    assert "3.5" in out
    assert "12.0" in out


def test_tasks_empty_prints_notice(monkeypatch, capfd):
    import janito.shell.cmds.tasks as tasks_cmd

    monkeypatch.setattr(tasks_cmd.task_manager, "list_tasks", lambda: [])

    assert _handler().handle(None, "/tasks") is True
    assert capfd.readouterr().out.strip() != ""
