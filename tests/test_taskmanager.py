"""
Tests for the TaskManager (janito.taskmanager).

The manager spawns real janito sub-processes; these tests unit-test its
contracts with a fake ``subprocess.Popen`` so no child process or API
configuration is needed.  Regression coverage for the ``'working_dir'``
KeyError in StartTask: ``start_task()`` must return the resolved working
directory (issue #94).
"""

import os
import sys
import threading
import time
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import janito.taskmanager as tm
from janito.taskmanager import TaskManager, build_task_command, privilege_flags


class _FakeStdin:
    def __init__(self):
        self.written = []

    def write(self, text):
        self.written.append(text)

    def close(self):
        pass


class _FakeProc:
    """Minimal stand-in for a subprocess.Popen that exits immediately."""

    def __init__(self, pid=12345):
        self.pid = pid
        self.returncode = 0
        self.stdin = _FakeStdin()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _manager_with_fake_popen(monkeypatch):
    """Return a fresh TaskManager whose Popen is a fake that records calls."""
    calls = {}
    proc = _FakeProc()

    def fake_popen(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(tm.subprocess, "Popen", fake_popen)
    return TaskManager(), calls, proc


def test_privilege_flags():
    assert privilege_flags(None) == []
    assert privilege_flags("") == []
    assert privilege_flags("rwx") == ["-r", "-w", "-x"]
    assert privilege_flags("xwr") == ["-x", "-w", "-r"]
    assert privilege_flags("RW") == ["-r", "-w"]
    try:
        privilege_flags("rz")
    except ValueError as e:
        assert "Invalid privilege character" in str(e)
    else:  # pragma: no cover - the ValueError must be raised
        raise AssertionError("privilege_flags('rz') should raise ValueError")


def test_build_task_command_uses_module_and_privileges(monkeypatch):
    monkeypatch.setattr(tm, "config_cli_args", lambda: [])
    cmd = build_task_command("rw")
    assert cmd[0] == sys.executable
    assert cmd[1] == "-m"
    assert cmd[2] == "janito"
    assert cmd[3:] == ["-r", "-w", "--no-tasks"]


def test_build_task_command_inherits_config_cli_args(monkeypatch):
    monkeypatch.setattr(tm, "config_cli_args", lambda: ["-l", "-c", "/tmp/cfg"])
    cmd = build_task_command(None)
    assert "-l" in cmd
    assert "-c" in cmd
    assert "/tmp/cfg" in cmd
    # The child must always start with the tasks toolset disabled so a task
    # sub-process cannot spawn further tasks (no recursive task execution).
    assert "--no-tasks" in cmd


def test_build_task_command_always_disables_tasks(monkeypatch):
    """Regression: every child command line carries --no-tasks."""
    monkeypatch.setattr(tm, "config_cli_args", lambda: ["-l"])
    for privileges in (None, "", "rwx"):
        cmd = build_task_command(privileges)
        assert cmd[-1] == "--no-tasks", cmd


def test_start_task_returns_working_dir(monkeypatch, tmp_path):
    """Regression: start_task() must include the resolved working_dir."""
    manager, calls, proc = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(
        description="Run: echo hello and report the output.",
        working_dir=str(tmp_path),
    )

    assert info["task_id"]
    assert info["pid"] == 12345
    assert info["working_dir"] == str(tmp_path)
    assert info["stdout_filename"].endswith(".out")
    assert info["stderr_filename"].endswith(".err")
    # The description is piped to the child's stdin (single prompt).
    assert calls["kwargs"]["stdin"] is not None
    assert calls["kwargs"]["cwd"] == str(tmp_path)
    assert "".join(proc.stdin.written) == ("Run: echo hello and report the output.")


def test_start_task_default_working_dir_is_cwd(monkeypatch):
    manager, calls, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(description="do the thing")

    assert info["working_dir"] == str(Path.cwd())
    assert calls["kwargs"]["cwd"] == str(Path.cwd())


def test_start_task_stores_summary(monkeypatch):
    """The summary is stored on the Task and returned by start/wait."""
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(
        description="Fix the login page",
        summary="Fix the login page",
    )

    assert info["summary"] == "Fix the login page"
    task = manager.get_task(info["task_id"])
    assert task.summary == "Fix the login page"

    result = manager.wait_for_task([info["task_id"]])
    assert result["tasks"][0]["summary"] == "Fix the login page"


def test_start_task_summary_defaults_to_none(monkeypatch):
    """Without a summary, the Task stores None and results omit it as None."""
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(description="no summary here")

    assert info["summary"] is None
    assert manager.get_task(info["task_id"]).summary is None


def test_wait_for_task_includes_working_dir(monkeypatch):
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(description="task one")
    result = manager.wait_for_task([info["task_id"]])

    assert result["tasks"][0]["working_dir"] == info["working_dir"]
    assert result["tasks"][0]["returncode"] == 0


def test_wait_for_task_invokes_callback_per_task(monkeypatch):
    """The on_task_complete callback fires once per finished task."""
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info1 = manager.start_task(description="task one")
    info2 = manager.start_task(description="task two")

    completed = []
    manager.wait_for_task(
        [info1["task_id"], info2["task_id"]],
        on_task_complete=lambda result: completed.append(result["task_id"]),
    )

    assert completed == [info1["task_id"], info2["task_id"]]


def test_wait_for_task_reports_in_completion_order(monkeypatch):
    """wait_for_task drains tasks in completion order, not request order."""
    manager = TaskManager()
    procs = {}

    class _BlockingProc:
        """Popen stand-in whose wait() blocks until released."""

        def __init__(self, pid):
            self.pid = pid
            self.release = threading.Event()
            self.stdin = _FakeStdin()
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.release.wait(timeout)
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    def fake_popen(cmd, **kwargs):
        proc = _BlockingProc(len(procs) + 1000)
        procs[proc.pid] = proc
        return proc

    monkeypatch.setattr(tm.subprocess, "Popen", fake_popen)

    info1 = manager.start_task(description="task one")
    info2 = manager.start_task(description="task two")

    # task2 finishes first, task1 finishes second.
    procs[info2["pid"]].returncode = 0
    procs[info2["pid"]].release.set()
    # Wait until task2's completion has been recorded before finishing task1,
    # so the completion order is deterministic (thread wake-up order is not).
    assert manager.get_task(info2["task_id"]).done.wait(5)
    procs[info1["pid"]].returncode = 0
    procs[info1["pid"]].release.set()

    # Requested in reverse order; results must still be in completion order.
    result = manager.wait_for_task([info1["task_id"], info2["task_id"]])

    assert [t["task_id"] for t in result["tasks"]] == [
        info2["task_id"],
        info1["task_id"],
    ]


def test_wait_for_task_callback_fires_in_completion_order(monkeypatch):
    """The on_task_complete callback fires as each task completes."""
    manager = TaskManager()
    procs = {}

    class _BlockingProc:
        def __init__(self, pid):
            self.pid = pid
            self.release = threading.Event()
            self.stdin = _FakeStdin()
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.release.wait(timeout)
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    def fake_popen(cmd, **kwargs):
        proc = _BlockingProc(len(procs) + 2000)
        procs[proc.pid] = proc
        return proc

    monkeypatch.setattr(tm.subprocess, "Popen", fake_popen)

    info1 = manager.start_task(description="task one")
    info2 = manager.start_task(description="task two")

    completed = []
    waiter_thread = threading.Thread(
        target=lambda: manager.wait_for_task(
            [info1["task_id"], info2["task_id"]],
            on_task_complete=lambda result: completed.append(result["task_id"]),
        )
    )
    waiter_thread.start()

    # Let the waiter block, then finish task2 first, then task1.
    time.sleep(0.05)
    procs[info2["pid"]].returncode = 0
    procs[info2["pid"]].release.set()
    time.sleep(0.05)
    procs[info1["pid"]].returncode = 0
    procs[info1["pid"]].release.set()

    waiter_thread.join(timeout=5)
    assert not waiter_thread.is_alive()
    assert completed == [info2["task_id"], info1["task_id"]]


def test_start_task_rejects_empty_description(monkeypatch):
    manager, _, _ = _manager_with_fake_popen(monkeypatch)
    try:
        manager.start_task(description="   ")
    except ValueError as e:
        assert "non-empty" in str(e)
    else:  # pragma: no cover
        raise AssertionError("empty description should raise ValueError")


class _BlockingProc:
    """Popen stand-in whose wait() blocks until released."""

    def __init__(self, pid):
        self.pid = pid
        self.release = threading.Event()
        self.stdin = _FakeStdin()
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.release.wait(timeout)
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _manager_with_blocking_procs(monkeypatch):
    """Return a TaskManager whose Popen yields _BlockingProc instances."""
    manager = TaskManager()
    procs = {}

    def fake_popen(cmd, **kwargs):
        proc = _BlockingProc(len(procs) + 3000)
        procs[proc.pid] = proc
        return proc

    monkeypatch.setattr(tm.subprocess, "Popen", fake_popen)
    return manager, procs


def test_wait_for_task_timeout_expiry_returns_pending(monkeypatch):
    """The budget expiring before any task exits reports timed_out=True."""
    manager, procs = _manager_with_blocking_procs(monkeypatch)

    info1 = manager.start_task(description="task one")
    info2 = manager.start_task(description="task two")

    # Never release the procs: both tasks stay running.
    try:
        result = manager.wait_for_task(
            [info1["task_id"], info2["task_id"]], timeout=0.15
        )
    finally:
        # Let the wait threads finish so the test exits cleanly.
        for proc in procs.values():
            proc.returncode = -9
            proc.release.set()

    assert result["timed_out"] is True
    assert sorted(result["pending_task_ids"]) == sorted(
        [info1["task_id"], info2["task_id"]]
    )
    assert result["tasks"] == []


def test_wait_for_task_timeout_returns_partial_results(monkeypatch):
    """Tasks finished before the expiry are reported alongside pending ones."""
    manager, procs = _manager_with_blocking_procs(monkeypatch)

    info1 = manager.start_task(description="task one")
    info2 = manager.start_task(description="task two")

    # task1 finishes; task2 never does.
    procs[info1["pid"]].returncode = 0
    procs[info1["pid"]].release.set()

    try:
        result = manager.wait_for_task(
            [info1["task_id"], info2["task_id"]], timeout=0.25
        )
    finally:
        # Let the wait threads finish so the test exits cleanly.
        for proc in procs.values():
            proc.returncode = -9
            proc.release.set()

    assert result["timed_out"] is True
    assert result["pending_task_ids"] == [info2["task_id"]]
    assert [t["task_id"] for t in result["tasks"]] == [info1["task_id"]]


def test_wait_for_task_includes_output_content(monkeypatch):
    """Finished tasks' stdout/stderr content is returned inline."""
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(description="task one")
    task = manager.get_task(info["task_id"])
    with open(task.stdout_filename, "w", encoding="utf-8") as fh:
        fh.write("hello stdout\nline two\n")
    with open(task.stderr_filename, "w", encoding="utf-8") as fh:
        fh.write("warning on stderr\n")

    result = manager.wait_for_task([info["task_id"]])

    entry = result["tasks"][0]
    assert entry["stdout"] == "hello stdout\nline two\n"
    assert entry["stderr"] == "warning on stderr\n"
    assert entry["stdout_truncated"] is False
    assert entry["stderr_truncated"] is False


def test_wait_for_task_output_truncated_by_max_lines(monkeypatch):
    """max_output_lines caps each stream and sets the truncated flag."""
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(description="task one")
    task = manager.get_task(info["task_id"])
    with open(task.stdout_filename, "w", encoding="utf-8") as fh:
        fh.write("line 1\nline 2\nline 3\n")

    result = manager.wait_for_task([info["task_id"]], max_output_lines=2)

    entry = result["tasks"][0]
    assert entry["stdout"] == "line 1\nline 2\n... [truncated]"
    assert entry["stdout_truncated"] is True
    # The (empty) stderr stream is not truncated.
    assert entry["stderr"] == ""
    assert entry["stderr_truncated"] is False


def test_wait_for_task_output_unlimited(monkeypatch):
    """max_output_lines=None returns the full content of each stream."""
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(description="task one")
    task = manager.get_task(info["task_id"])
    content = "\n".join(f"line {i}" for i in range(500))
    with open(task.stdout_filename, "w", encoding="utf-8") as fh:
        fh.write(content)

    result = manager.wait_for_task([info["task_id"]], max_output_lines=None)

    entry = result["tasks"][0]
    assert entry["stdout"] == content
    assert entry["stdout_truncated"] is False


def test_wait_for_task_unreadable_output_is_none(monkeypatch):
    """A stream whose temp file is gone reports None without raising."""
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(description="task one")
    task = manager.get_task(info["task_id"])
    os.unlink(task.stdout_filename)  # simulate cleanup / missing file

    result = manager.wait_for_task([info["task_id"]])

    entry = result["tasks"][0]
    assert entry["stdout"] is None
    assert entry["stdout_truncated"] is False
    # The still-present stderr stream is returned normally.
    assert entry["stderr"] == ""
    assert entry["stderr_truncated"] is False


def test_wait_for_task_timeout_not_exceeded(monkeypatch):
    """Tasks finishing within the budget report timed_out=False."""
    manager, _, _ = _manager_with_fake_popen(monkeypatch)

    info = manager.start_task(description="task one")
    result = manager.wait_for_task([info["task_id"]], timeout=5)

    assert result["timed_out"] is False
    assert result["pending_task_ids"] == []
    assert result["tasks"][0]["task_id"] == info["task_id"]
