"""Tests for the interactive shell's running-tasks visibility (issue #101)."""

from janito.shell import InteractiveShell


def _shell():
    return InteractiveShell(model="test-model", no_history=True)


def _running_rows():
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
            "summary": None,
            "state": "running",
            "running": True,
            "pid": 222,
            "working_dir": "/tmp/wd",
            "duration_seconds": 0.1,
        },
    ]


def test_notice_smoke(monkeypatch, capfd):
    """One smoke: notice renders non-empty output driven by task rows."""
    shell = _shell()
    rows = _running_rows()
    monkeypatch.setattr(shell, "_running_tasks", lambda: rows)
    shell._print_running_tasks_notice()
    out = capfd.readouterr().out
    assert out.strip() != ""
    # Expectations driven from the rows (source of truth), not hardcoded copy.
    for row in rows:
        assert row["task_id"] in out


def test_notice_prints_nothing_when_no_tasks(monkeypatch, capfd):
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", lambda: [])
    shell._print_running_tasks_notice()
    assert capfd.readouterr().out == ""


def test_running_tasks_returns_empty_when_manager_unavailable(monkeypatch):
    shell = _shell()
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def broken_import(name, *args, **kwargs):
        if name == "janito.taskmanager":
            raise ImportError("tasks toolset disabled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", broken_import)
    assert shell._running_tasks() == []


def _ctrl_c_then(shell, answer):
    calls = []

    def fake_prompt(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("message"))
        if len(calls) == 1:
            raise KeyboardInterrupt
        return answer

    return calls, fake_prompt


def test_confirm_quit_with_tasks_asks_about_termination(monkeypatch, capfd):
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", _running_rows)
    calls, fake_prompt = _ctrl_c_then(shell, "n")
    monkeypatch.setattr(shell.session, "prompt", fake_prompt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))
    result = shell._get_user_input()
    assert result == ""
    capfd.readouterr()
    assert any("terminate all tasks" in str(c).lower() for c in calls)
    # State: answered "n" -> no kill.
    assert killed == []


def test_confirm_quit_with_tasks_yes_kills_all(monkeypatch, capfd):
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", _running_rows)
    _, fake_prompt = _ctrl_c_then(shell, "y")
    monkeypatch.setattr(shell.session, "prompt", fake_prompt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))
    result = shell._get_user_input()
    capfd.readouterr()
    assert result is None
    assert killed == [True]


def test_confirm_quit_without_tasks_keeps_plain_question(monkeypatch, capfd):
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", lambda: [])
    calls, fake_prompt = _ctrl_c_then(shell, "n")
    monkeypatch.setattr(shell.session, "prompt", fake_prompt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))
    result = shell._get_user_input()
    assert result == ""
    out = capfd.readouterr().out
    assert "still running" not in out
    assert killed == []


def test_second_ctrl_c_during_confirmation_kills_and_quits(monkeypatch, capfd):
    shell = _shell()
    monkeypatch.setattr(shell, "_running_tasks", _running_rows)

    def always_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(shell.session, "prompt", always_interrupt)
    killed = []
    monkeypatch.setattr(shell, "_kill_all_tasks", lambda: killed.append(True))
    result = shell._get_user_input()
    capfd.readouterr()
    assert result is None
    assert killed == [True]


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
    import janito.taskmanager as tm_module

    manager = _FakeManager(killed=[{"task_id": "abc123"}])
    monkeypatch.setattr(tm_module, "task_manager", manager)
    shell = _shell()
    shell._kill_all_tasks()
    assert manager.calls == 1
    assert capfd.readouterr().out.strip() != ""


def test_kill_all_tasks_silent_when_nothing_running(monkeypatch, capfd):
    import janito.taskmanager as tm_module

    manager = _FakeManager(killed=[])
    monkeypatch.setattr(tm_module, "task_manager", manager)
    shell = _shell()
    shell._kill_all_tasks()
    assert manager.calls == 1
    assert capfd.readouterr().out == ""


def test_kill_all_tasks_swallows_manager_errors(monkeypatch, capfd):
    import janito.taskmanager as tm_module

    manager = _FakeManager(error=RuntimeError("boom"))
    monkeypatch.setattr(tm_module, "task_manager", manager)
    shell = _shell()
    shell._kill_all_tasks()
    capfd.readouterr()
    assert manager.calls == 1
