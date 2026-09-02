"""
Tests for the interactive shell's running-tasks visibility (issue #101).

Covers the end-of-turn "tasks still running" notice and the Ctrl+C
confirm-quit flow that asks "Do you want to exit and terminate all tasks?"
(and kills them on confirmation).  The manager itself is faked; its
behaviour is covered by tests/test_taskmanager.py.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))


from janito.shell import InteractiveShell


def _shell():
    """Build a fresh shell for testing."""
    return InteractiveShell(model="test-model", no_history=True)


def _running_rows():
    """Fake snapshot rows shaped like TaskManager.running_tasks() output."""
    return [
        {
            "task_id": "abc123",
            "summary": "Run the test suite",
            "state": "running",
            "running": True,
            "pid": 111,
            "working_dir": "/tmp/wd",
            "duration_seconds": 3.5,
        },
        {
            "task_id": "def456",
            "summary": None,  # falls back to the task id in the notice
            "state": "running",
            "running": True,
            "pid": 222,
            "working_dir": "/tmp/wd",
            "duration_seconds": 0.1,
        },
    ]


# ---------------------------------------------------------------------------
# End-of-turn notice
# ---------------------------------------------------------------------------


def test_notice_prints_running_tasks_table(monkeypatch, capfd):
    """The notice lists each running task's id and summary."""
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", _running_rows)

    shell._print_running_tasks_notice()
    out = capfd.readouterr().out

    assert "The following (2) tasks are still running:" in out
    assert "Task ID" in out and "Summary" in out
    assert "abc123" in out and "Run the test suite" in out
    # A task without a summary falls back to its id.
    assert "def456" in out
    # No Rich markup leaks into the output (the console is markup=False).
    assert "[bold]" not in out and "[dim]" not in out
    # The hint names what the user can ask about (list/wait/kill tasks).
    assert "list all task" in out and "kill all tasks" in out


def test_notice_prints_nothing_when_no_tasks(monkeypatch, capfd):
    """A quiet turn (no running tasks) prints no notice at all."""
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", lambda: [])

    shell._print_running_tasks_notice()
    out = capfd.readouterr().out

    assert out == ""


def test_notice_is_best_effort_and_never_raises(monkeypatch, capfd):
    """A manager failure must not break the turn."""
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", _running_rows)

    # Nothing raises even if the console is unavailable.
    assert shell._running_tasks() == _running_rows()


def test_running_tasks_returns_empty_when_manager_unavailable(monkeypatch):
    """_running_tasks() swallows import/attribute errors (--no-tasks safe)."""
    shell = _shell()

    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def broken_import(name, *args, **kwargs):
        if name == "janito.taskmanager":
            raise ImportError("tasks toolset disabled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", broken_import)

    assert shell._running_tasks() == []


# ---------------------------------------------------------------------------
# Ctrl+C confirm-quit with running tasks
# ---------------------------------------------------------------------------


def _ctrl_c_then(shell, answer):
    """Make session.prompt raise KeyboardInterrupt once, then return answer."""
    calls = []

    def fake_prompt(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("message"))
        if len(calls) == 1:
            raise KeyboardInterrupt
        return answer

    return calls, fake_prompt


def test_confirm_quit_with_tasks_asks_about_termination(monkeypatch, capfd):
    """Ctrl+C with tasks running shows the notice and the tasks question."""
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", _running_rows)
    calls, fake_prompt = _ctrl_c_then(shell, "n")
    monkeypatch.setattr(shell.session, "prompt", fake_prompt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))

    result = shell._get_user_input()

    assert result == ""  # answered "n": keep going
    out = capfd.readouterr().out
    assert "The following (2) tasks are still running:" in out
    assert any("Do you want to exit and terminate all tasks?" in str(c) for c in calls)
    assert killed == []


def test_confirm_quit_with_tasks_yes_kills_all(monkeypatch, capfd):
    """Answering 'y' terminates all tasks and ends the session."""
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", _running_rows)
    calls, fake_prompt = _ctrl_c_then(shell, "y")
    monkeypatch.setattr(shell.session, "prompt", fake_prompt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))

    result = shell._get_user_input()

    assert result is None  # quit
    assert killed == [True]


def test_confirm_quit_without_tasks_keeps_plain_question(monkeypatch, capfd):
    """Without running tasks the prompt is the original quit question."""
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", lambda: [])
    calls, fake_prompt = _ctrl_c_then(shell, "n")
    monkeypatch.setattr(shell.session, "prompt", fake_prompt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))

    result = shell._get_user_input()

    assert result == ""
    out = capfd.readouterr().out
    assert "tasks are still running" not in out
    assert any("Do you want to quit the conversation?" in str(c) for c in calls)
    assert killed == []


def test_second_ctrl_c_during_confirmation_kills_and_quits(monkeypatch, capfd):
    """A second Ctrl+C at the confirmation prompt quits, killing tasks."""
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", _running_rows)

    def always_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(shell.session, "prompt", always_interrupt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))

    result = shell._get_user_input()

    assert result is None  # quit
    assert killed == [True]


def test_second_ctrl_c_without_tasks_quits_without_kill(monkeypatch):
    """A second Ctrl+C with no tasks just quits (no kill attempt)."""
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", lambda: [])

    def always_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(shell.session, "prompt", always_interrupt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))

    assert shell._get_user_input() is None
    assert killed == []


# ---------------------------------------------------------------------------
# _kill_all_tasks delegation
# ---------------------------------------------------------------------------


class _FakeManager:
    def __init__(self, killed=None, error=None):
        self._killed = killed if killed is not None else []
        self._error = error
        self.calls = 0

    def kill_all(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._killed


def test_kill_all_tasks_delegates_to_manager(monkeypatch, capfd):
    """_kill_all_tasks calls TaskManager.kill_all and reports the count."""
    import janito.taskmanager as tm_module

    manager = _FakeManager(killed=[{"task_id": "abc123"}])
    monkeypatch.setattr(tm_module, "task_manager", manager)

    shell = _shell()
    shell._kill_all_tasks()

    assert manager.calls == 1
    out = capfd.readouterr().out
    assert "Terminated 1 running task(s)." in out


def test_kill_all_tasks_silent_when_nothing_running(monkeypatch, capfd):
    """Nothing was killed -> no confirmation line."""
    import janito.taskmanager as tm_module

    manager = _FakeManager(killed=[])
    monkeypatch.setattr(tm_module, "task_manager", manager)

    shell = _shell()
    shell._kill_all_tasks()

    assert manager.calls == 1
    assert capfd.readouterr().out == ""


def test_kill_all_tasks_swallows_manager_errors(monkeypatch, capfd):
    """A failing manager must not block the quit (atexit is the safety net)."""
    import janito.taskmanager as tm_module

    manager = _FakeManager(error=RuntimeError("boom"))
    monkeypatch.setattr(tm_module, "task_manager", manager)

    shell = _shell()
    shell._kill_all_tasks()  # must not raise

    assert manager.calls == 1
