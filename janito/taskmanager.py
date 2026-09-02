"""
Task manager for parallel janito sub-processes (issue #94).

:class:`TaskManager` runs each task as a separate ``janito`` process: the
task's *description* is piped to the child's stdin (single-prompt mode, which
never writes the interactive input history by design -- ``--no-history`` is
not needed) and the child's stdout/stderr are redirected straight into temp
files, so the OS writes them live as the process flows.  No extra thread is
needed for the file updates: the child inherits the file descriptors and
writes to them directly.

One daemon thread per task is used only to wait for the child to exit and
record its exit status -- that is what :meth:`TaskManager.wait_for_task`
blocks on and what :meth:`TaskManager.stop_task` terminates.  The same thread
also arms the task's optional lifetime cap (``StartTask``'s ``timeout``): when
the deadline passes the child is terminated, so a task can never outlive its
budget just because nobody waited on it.

The child command line is built by :func:`build_task_command`, which
reproduces the parent's ``-c``/``--config-dir`` and ``-l``/``--local`` flags
via :func:`janito.config_dir.config_cli_args` so sub-processes resolve the
same configuration (issue #94), maps the task's ``privileges`` string to
the ``-r``/``-w``/``-x`` CLI flags (``None``/empty means the child starts
read-only, matching the janito default, issue #85), and always passes
``--no-tasks`` so a task sub-process can never spawn further tasks itself
(no recursive task execution).

Temp output files are created with ``delete=False`` (the LLM reads them, e.g.
with the ReadFile tool) and removed at process exit by the atexit-registered
:meth:`TaskManager.cleanup`.

:meth:`TaskManager.wait_for_task` also returns the finished tasks' output
content inline (``stdout`` / ``stderr`` in each result dict, capped at
``max_output_lines`` lines) so the LLM can check a task's results directly
without having to read the temp files -- the files stay available for the
full, untruncated content.

Exit status reporting
---------------------

Each finished task reports *why* it ended and *what it exited with*, as two
orthogonal fields (see :class:`Task`):

``exit_reason``
    ``"finished"`` (the child exited on its own), ``"timeout"`` (killed because
    it exceeded its ``timeout``), ``"stopped"`` (killed by :meth:`stop_task`)
    or ``"error"`` (the wait itself failed).

``exit_code``
    The child's own exit status, or ``None`` when it never produced one.

A terminated task may still carry a non-``None`` ``exit_code``: the grace
period lets a child that traps SIGTERM shut down and exit by itself, so
``exit_code == 0`` with ``exit_reason == "timeout"`` is a real (and
meaningful) combination.  Callers must therefore read ``exit_reason`` to tell
success from termination, and never infer it from ``returncode`` -- which is
kept as the raw :attr:`subprocess.Popen.returncode` for forensics and is
negative for signal deaths on POSIX but ``1`` for ``kill()`` on Windows.
"""

import atexit
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .config_dir import config_cli_args

__all__ = [
    "Task",
    "TaskManager",
    "build_task_command",
    "privilege_flags",
    "task_manager",
]

# How many lines of a task's stdout/stderr wait_for_task() returns inline per
# stream by default (mirrors GetUrl's default max_lines).  None = no limit.
DEFAULT_MAX_OUTPUT_LINES = 200

# Marker appended to stdout/stderr content when the requested line cap cuts
# it short (same marker GetUrl uses), so callers can see the output was
# truncated and read the temp file if they need the rest.
_TRUNCATED_MARKER = "\n... [truncated]"

# Grace periods used when terminating a task's child process (both the
# StartTask timeout path and StopTask): SIGTERM first, giving the child this
# many seconds to shut down cleanly -- and still report its own exit code --
# before escalating to SIGKILL and waiting this many seconds to reap it.
TERM_GRACE_SECONDS = 10
KILL_GRACE_SECONDS = 5

# Why a task's child process ended.  ``EXIT_RUNNING`` is the initial value;
# the first writer wins, so a StopTask landing microseconds before the wait
# thread reaps the child can never be relabelled "finished" (and vice versa).
EXIT_RUNNING = "running"
#: The child exited on its own and reported an exit status.
EXIT_FINISHED = "finished"
#: Killed by janito because it exceeded its ``timeout``.
EXIT_TIMEOUT = "timeout"
#: Killed by janito through :meth:`TaskManager.stop_task`.
EXIT_STOPPED = "stopped"
#: Killed by a signal nobody in janito sent (e.g. the OOM killer) -- it has no
#: exit status of its own, so it is not reported as "finished".
EXIT_KILLED = "killed"
#: Waiting for the child itself failed; ``error`` holds the reason.
EXIT_ERROR = "error"

#: Reasons meaning the task never ran to completion on its own (and therefore
#: has no meaningful exit status of its own).
TERMINATED_REASONS = (EXIT_TIMEOUT, EXIT_STOPPED, EXIT_KILLED)


def _read_output_file(filename: str, max_lines: int | None) -> tuple[str | None, bool]:
    """Read a task's captured stdout/stderr temp file, capped at ``max_lines``.

    Reads the file incrementally (line by line) so a huge output file is
    never slurped into memory just to enforce the line cap.  Returns the
    content and a ``truncated`` flag; when the cap cuts the content short,
    a ``\\n... [truncated]`` marker is appended (same marker GetUrl uses) so
    callers can see the output was cut and read the temp file for the rest.

    Args:
        filename: The temp output file to read.
        max_lines: Maximum number of lines to return.  ``None`` returns the
            full content.

    Returns:
        tuple[str | None, bool]: ``(content, truncated)``.  ``content`` is
        ``None`` when the file could not be read (e.g. it was already
        removed) -- the caller then reports ``None`` rather than failing the
        whole wait.  ``truncated`` is True when the cap cut the content
        short.
    """
    try:
        with open(filename, encoding="utf-8", errors="replace") as fh:
            if max_lines is None:
                return fh.read(), False
            lines: list[str] = []
            for line in fh:
                if len(lines) >= max_lines:
                    content = "".join(lines).rstrip("\n")
                    return content + _TRUNCATED_MARKER, True
                lines.append(line)
            return "".join(lines), False
    except OSError:
        return None, False


def _normalise_timeout(timeout: float | None) -> float | None:
    """Validate and coerce a task lifetime cap to a positive float seconds.

    Args:
        timeout: The requested cap (``None`` = no cap); anything numeric is
            accepted so a JSON-supplied int works.

    Returns:
        The cap as a float, or ``None`` when uncapped.

    Raises:
        ValueError: If the cap is not a positive number of seconds.
    """
    if timeout is None:
        return None
    try:
        seconds = float(timeout)
    except (TypeError, ValueError) as e:
        raise ValueError(f"timeout must be a number of seconds, got {timeout!r}") from e
    if seconds <= 0:
        raise ValueError(
            f"timeout must be a positive number of seconds, got {seconds:g}"
        )
    return seconds


def _own_exit_code(returncode: int | None) -> int | None:
    """Map a raw ``returncode`` to the child's own exit status (or ``None``).

    On POSIX a process killed by a signal reports a negative return code and
    never produced an exit status of its own, so the exit code is ``None``.
    ``None`` (no status observed) is likewise reported as ``None``.

    Caveat: on Windows ``kill()`` surfaces as return code ``1``, which this
    helper cannot tell apart from a real ``exit(1)`` -- which is why
    ``exit_reason`` (set explicitly by the code that sent the signal) is the
    authoritative field, never ``exit_code``.

    Args:
        returncode: The raw :attr:`subprocess.Popen.returncode`.

    Returns:
        The exit status, or ``None`` when the child never produced one.
    """
    if returncode is None or returncode < 0:
        return None
    return returncode


def _terminate_process(proc: subprocess.Popen) -> int | None:
    """Terminate a task's child process (SIGTERM, then SIGKILL) and reap it.

    Shared by :meth:`TaskManager.stop_task` and the timeout path in
    :meth:`TaskManager._wait_for_exit` so both give the child the same grace to
    shut down cleanly (and, in doing so, still produce its own exit code).

    Args:
        proc: The child process to terminate.  Already-exited children are
            left untouched: their status is reported unchanged.

    Returns:
        The final :attr:`~subprocess.Popen.returncode` (negative for signal
        deaths on POSIX), or ``None`` if it could not be reaped.  Never
        raises: an uninterruptible child (e.g. stuck in an uninterruptible
        syscall) must not turn a successful kill into a failed tool call, so
        a :class:`subprocess.TimeoutExpired` from the reaping wait is swallowed
        and the return code observed so far is returned.
    """
    if proc.poll() is not None:
        return proc.returncode
    try:
        proc.terminate()
        try:
            return proc.wait(timeout=TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            # Still alive after the grace period: force it and reap.
            proc.kill()
            try:
                return proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:  # pragma: no cover - wedged child
                return proc.returncode
    except OSError:  # pragma: no cover - child already gone / no permission
        # A process that vanished between poll() and the signal is fine:
        # report whatever return code is observable now.
        return proc.poll()


def privilege_flags(privileges: str | None) -> list[str]:
    """Map a privileges string (``"rwx"``) to CLI flags (``["-r", "-w", "-x"]``).

    ``None`` or an empty string yields ``[]`` -- the child then starts with
    the janito default privileges (read-only, issue #85).

    Args:
        privileges: A combination of ``r`` / ``w`` / ``x`` (any order/case).

    Returns:
        The corresponding ``-r`` / ``-w`` / ``-x`` flags.

    Raises:
        ValueError: If ``privileges`` contains any character other than
            ``r`` / ``w`` / ``x``.
    """
    if not privileges:
        return []
    flags: list[str] = []
    for char in str(privileges).strip().lower():
        if char == "r":
            flags.append("-r")
        elif char == "w":
            flags.append("-w")
        elif char == "x":
            flags.append("-x")
        else:
            raise ValueError(
                f"Invalid privilege character {char!r} in {privileges!r}: "
                "expected a combination of 'r', 'w' and 'x'"
            )
    return flags


def build_task_command(privileges: str | None) -> list[str]:
    """Build the child ``janito`` command line (issue #94).

    Uses ``sys.executable -m janito`` so the child runs in the same Python
    environment as the parent, inherits the parent's ``-c``/``-l`` config
    flags via :func:`janito.config_dir.config_cli_args`, maps ``privileges``
    to the ``-r``/``-w``/``-x`` flags, and always appends ``--no-tasks`` so
    the child cannot spawn further tasks (preventing recursive task
    execution).

    Args:
        privileges: Privileges for the child (``None``/``""`` = read-only).

    Returns:
        The command line (as a list of argv strings).
    """
    cmd = [sys.executable, "-m", "janito"]
    cmd.extend(config_cli_args())
    cmd.extend(privilege_flags(privileges))
    cmd.append("--no-tasks")
    return cmd


@dataclass
class Task:
    """A running (or finished) parallel task.

    Attributes:
        exit_reason: Why the child process ended -- :data:`EXIT_RUNNING` while
            it is still alive, then :data:`EXIT_FINISHED` (it exited on its
            own), :data:`EXIT_TIMEOUT` (killed for exceeding ``timeout``),
            :data:`EXIT_STOPPED` (killed by :meth:`TaskManager.stop_task`) or
            :data:`EXIT_ERROR` (the wait itself failed).  The first writer
            wins, so a stop landing right as the child exits cannot be
            mislabelled ``"finished"``.
        exit_code: The child's own exit status, or ``None`` when it never
            produced one (killed without a clean shutdown, still running, or
            the wait failed).  A *terminated* task can still have an exit code
            when it exited during the SIGTERM grace period, so ``exit_reason``
            -- not this field -- is what distinguishes success from
            termination.
        returncode: Raw :attr:`subprocess.Popen.returncode` (kept for
            back-compat and forensics: negative for signal deaths on POSIX,
            ``1`` for ``kill()`` on Windows).  Prefer ``exit_reason`` /
            ``exit_code``.
        started_at: ``time.monotonic()`` stamp taken when the child was
            spawned, used to report ``duration_seconds``.
    """

    task_id: str
    summary: str | None
    description: str
    working_dir: str
    privileges: str | None
    pid: int
    stdout_filename: str
    stderr_filename: str
    process: subprocess.Popen
    thread: threading.Thread
    timeout: float | None = None
    returncode: int | None = None
    error: str | None = None
    exit_reason: str = EXIT_RUNNING
    exit_code: int | None = None
    started_at: float = field(default_factory=time.monotonic)
    duration_seconds: float | None = None
    done: threading.Event = field(default_factory=threading.Event)

    @property
    def terminated(self) -> bool:
        """Whether the child was killed by janito (timeout or stop) rather
        than exiting on its own."""
        return self.exit_reason in TERMINATED_REASONS


class TaskManager:
    """Registry of parallel tasks, each a child ``janito`` process."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        # task_ids in the order their child processes exited (appended by the
        # per-task wait threads), so wait_for_task() can drain them in true
        # completion order even when several finish near-simultaneously.
        self._completion_order: list[str] = []
        self._completion_lock = threading.Lock()
        # Clean up running processes and temp files at interpreter exit.
        atexit.register(self.cleanup)

    # -- lifecycle -----------------------------------------------------------

    def start_task(
        self,
        description: str,
        working_dir: str | None = None,
        privileges: str | None = None,
        summary: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Start a new parallel task.

        Spawns ``janito`` with the description piped to its stdin (single
        prompt) and stdout/stderr redirected to temp files.  Returns
        immediately with the task id, pid and output file names; the child
        keeps running in the background.

        Args:
            description: What needs to be done (sent to the child's stdin).
            working_dir: Working directory for the child process
                (default: the parent's current directory).
            privileges: Privileges for the child (``None``/``""`` =
                read-only).
            summary: Optional one-line, human-readable summary of the task
                (stored on the :class:`Task` so WaitForTask can present it to
                the user; default ``None``).
            timeout: Optional lifetime cap in seconds.  ``None`` (the default)
                lets the task run until it exits or is stopped.  When the
                deadline passes, the child is terminated by its wait thread
                (SIGTERM, then SIGKILL) exactly as :meth:`stop_task` would --
                independently of whether anyone is waiting on it -- and the
                task is recorded with ``exit_reason`` :data:`EXIT_TIMEOUT`.

        Returns:
            Dict with ``task_id``, ``pid``, ``working_dir`` (the resolved
            child working directory), ``summary``, ``timeout``,
            ``stdout_filename`` and ``stderr_filename``.

        Raises:
            ValueError: If ``description`` is empty, ``working_dir`` is not a
                directory, ``privileges`` is invalid, or ``timeout`` is not a
                positive number.
            RuntimeError: If the child could not be spawned or the
                description could not be sent to it.
        """
        if not description or not description.strip():
            raise ValueError("description must be a non-empty string")

        timeout = _normalise_timeout(timeout)

        if working_dir:
            cwd = os.path.abspath(os.path.expanduser(working_dir))
        else:
            cwd = os.getcwd()
        if not os.path.isdir(cwd):
            raise ValueError(f"working_dir is not a directory: {cwd}")

        task_id = uuid.uuid4().hex[:12]
        cmd = build_task_command(privileges)

        stdout_tmp = tempfile.NamedTemporaryFile(
            prefix=f"janito-{task_id}-", suffix=".out", delete=False
        )
        stderr_tmp = tempfile.NamedTemporaryFile(
            prefix=f"janito-{task_id}-", suffix=".err", delete=False
        )
        stdout_filename = stdout_tmp.name
        stderr_filename = stderr_tmp.name

        try:
            spawned_at = time.monotonic()
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=stdout_tmp,
                stderr=stderr_tmp,
                cwd=cwd,
                text=True,
                encoding="utf-8",
            )
        except Exception:
            # Spawn failed: release the temp files and re-raise.
            for tmp in (stdout_tmp, stderr_tmp):
                tmp.close()
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            raise

        # The child holds its own copies of the fds; close ours immediately
        # (the OS keeps writing to the files live as the process flows).
        stdout_tmp.close()
        stderr_tmp.close()

        # Pipe the description as the child's single prompt and close stdin so
        # the child can finish reading and exit.  Single-prompt mode never
        # writes the interactive input history (issue #94).
        try:
            proc.stdin.write(description)
            proc.stdin.close()
        except (BrokenPipeError, OSError) as e:
            proc.terminate()
            for filename in (stdout_filename, stderr_filename):
                try:
                    os.unlink(filename)
                except OSError:
                    pass
            raise RuntimeError(
                f"failed to send description to the task process: {e}"
            ) from e

        task = Task(
            task_id=task_id,
            summary=summary,
            description=description,
            working_dir=cwd,
            privileges=privileges,
            pid=proc.pid,
            stdout_filename=stdout_filename,
            stderr_filename=stderr_filename,
            process=proc,
            thread=None,
            timeout=timeout,
            # Stamp the true spawn time (the wait thread arms the deadline a
            # few microseconds later, but duration_seconds should measure the
            # child's real lifetime).
            started_at=spawned_at,
        )
        task.thread = threading.Thread(
            target=self._wait_for_exit, args=(task,), daemon=True
        )
        with self._lock:
            self._tasks[task_id] = task
        task.thread.start()

        return {
            "task_id": task_id,
            "pid": proc.pid,
            "working_dir": cwd,
            "summary": summary,
            "timeout": timeout,
            "stdout_filename": stdout_filename,
            "stderr_filename": stderr_filename,
        }

    @staticmethod
    def _label_exit(task: Task, returncode: int | None) -> None:
        """Record the exit status of a reaped child nobody has claimed yet.

        Sets ``exit_reason`` / ``exit_code`` from a raw ``returncode``, but only
        while the task is still :data:`EXIT_RUNNING`: :meth:`stop_task` and the
        timeout path label the outcome *before* signalling, so whichever writer
        gets there first wins and a killed child is never relabelled as having
        finished.

        Args:
            task: The task whose status to record.
            returncode: The raw :attr:`~subprocess.Popen.returncode` observed.
        """
        if task.exit_reason != EXIT_RUNNING:
            return
        exit_code = _own_exit_code(returncode)
        if exit_code is None:
            # A negative (or missing) return code with no signal from us:
            # something outside janito killed the child.
            task.exit_reason = EXIT_KILLED
        else:
            task.exit_reason = EXIT_FINISHED
            task.exit_code = exit_code

    def _wait_for_exit(self, task: Task) -> None:
        """Daemon-thread body: wait for the child and record its exit status.

        This is where a task's ``timeout`` is enforced: the wait carries the
        budget, so the child is killed whether or not anyone is blocked in
        :meth:`wait_for_task`.  ``TimeoutExpired`` must be caught *before* the
        catch-all ``except Exception`` below -- it is an ``Exception``
        subclass, and letting it fall through would record a timeout as a wait
        failure instead of terminating the child.

        All status fields are written before ``task.done.set()``: that event is
        the publication point :meth:`wait_for_task` blocks on, so a waiter can
        never observe a half-recorded task.
        """
        try:
            # wait() returns as soon as the child exits, so there is no
            # polling loop and no race between "exited naturally" and "timed
            # out": TimeoutExpired is only raised while the child is alive.
            returncode = task.process.wait(timeout=task.timeout)
            task.returncode = returncode
            self._label_exit(task, returncode)
        except subprocess.TimeoutExpired:
            # The lifetime budget expired with the child still running.  Give
            # it StopTask's grace to shut down cleanly -- which lets it still
            # report its own exit code -- before escalating to SIGKILL.
            returncode = _terminate_process(task.process)
            task.returncode = returncode
            task.exit_reason = EXIT_TIMEOUT
            task.exit_code = _own_exit_code(returncode)
            if task.exit_code is None:
                task.error = (
                    f"task exceeded its timeout of {task.timeout:g}s and was killed"
                )
            else:
                # It exited during the grace period, so it has a real code --
                # but it still did not finish within its budget.
                task.error = (
                    f"task exceeded its timeout of {task.timeout:g}s; it exited "
                    f"with code {task.exit_code} during shutdown"
                )
        except Exception as e:  # noqa: BLE001 - record any wait failure
            task.error = str(e)
            task.exit_reason = EXIT_ERROR
        finally:
            # Record the true completion order before signalling done, so
            # wait_for_task() can drain tasks in the order they finished.
            self._record_completion(task)

    def _record_completion(self, task: Task) -> None:
        """Mark a task's completion idempotently (order + ``done`` event).

        Stamps ``duration_seconds`` (only if not already set, so a value
        recorded by :meth:`stop_task` / :meth:`kill_all` is kept), appends the
        task to :attr:`_completion_order` unless it is already there, and sets
        its ``done`` event.  Idempotency matters because both the wait thread's
        ``finally`` and :meth:`kill_all` (issue #101) record completion: the
        second caller must not double-append (which would otherwise leave a
        phantom id in the drain list), yet a task killed via :meth:`kill_all`
        still needs its ``done`` event set so a later :meth:`wait_for_task`
        cannot hang waiting for a thread that may never have been scheduled.
        """
        if task.duration_seconds is None:
            task.duration_seconds = time.monotonic() - task.started_at
        with self._completion_lock:
            if task.task_id not in self._completion_order:
                self._completion_order.append(task.task_id)
        task.done.set()

    # -- queries -------------------------------------------------------------

    def _task_snapshot(self, task: Task) -> dict[str, Any]:
        """Snapshot of one task for :meth:`list_tasks` (issue #101).

        ``state`` is the task's ``exit_reason`` (``running`` while the child is
        still alive, then ``finished`` / ``timeout`` / ``stopped`` / ``killed``
        / ``error``).  A live child's duration is the elapsed wall time so far;
        a finished child's duration is the value recorded at completion.
        """
        if task.exit_reason == EXIT_RUNNING:
            duration = time.monotonic() - task.started_at
        else:
            duration = task.duration_seconds
        return {
            "task_id": task.task_id,
            "summary": task.summary,
            "state": task.exit_reason,
            "running": task.exit_reason == EXIT_RUNNING,
            "pid": task.pid,
            "working_dir": task.working_dir,
            "duration_seconds": duration,
        }

    def list_tasks(self) -> list[dict[str, Any]]:
        """Snapshot of every task (running and finished), issue #101.

        Running tasks come first (in start order), then finished/stopped
        tasks (also in start order).  Use this -- or the ``ListTasks`` tool --
        to see what the manager knows about without waiting on anything.

        Returns:
            List of dicts, one per task, each with ``task_id``, ``summary``,
            ``state`` (the ``exit_reason``: ``running`` while alive, then
            ``finished`` / ``timeout`` / ``stopped`` / ``killed`` / ``error``),
            ``running`` (bool), ``pid``, ``working_dir`` and
            ``duration_seconds``.
        """
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(
            key=lambda t: (0 if t.exit_reason == EXIT_RUNNING else 1, t.started_at)
        )
        return [self._task_snapshot(task) for task in tasks]

    def running_tasks(self) -> list[dict[str, Any]]:
        """Snapshot of the currently-running tasks only (issue #101).

        Filtered view used by the shell's end-of-turn notice and
        confirm-quit prompt; same ordering and dict shape as
        :meth:`list_tasks` (all entries carry ``running`` = True).
        """
        return [entry for entry in self.list_tasks() if entry["running"]]

    def kill_all(self) -> list[dict[str, Any]]:
        """Immediately SIGKILL every live child and reap it (issue #101).

        Unlike :meth:`stop_task` (and the ``timeout`` path), there is **no
        SIGTERM grace period**: each child that is still running is killed
        outright.  Each such task's ``exit_reason`` is set to
        :data:`EXIT_STOPPED` *before* the signal is sent -- first-writer-wins,
        the same convention :meth:`stop_task` uses -- so its wait thread can
        never relabel a child that dies right away as having "finished".
        Completion is recorded idempotently (via :meth:`_record_completion`)
        so a later :meth:`wait_for_task` sees each killed task as already done
        (and drained) instead of hanging on it.

        This is what the interactive shell's confirm-quit path calls, and what
        the atexit :meth:`cleanup` hook delegates to, so tasks are terminated
        whether the user quits from the shell or the process exits any other
        way.

        Returns:
            List of result dicts (one per *previously running* task that was
            killed here), each with ``task_id``, ``pid``, ``stopped``,
            ``exit_reason``, ``exit_code``, ``timeout`` and ``returncode`` --
            the same shape :meth:`stop_task` returns.  ``exit_code`` is
            ``None`` (a SIGKILL never lets the child report a status).  Tasks
            that had already finished on their own are not touched or listed.
        """
        with self._lock:
            tasks = list(self._tasks.values())
        killed: list[dict[str, Any]] = []
        for task in tasks:
            if task.process.poll() is not None:
                # Already exited on its own: keep its real outcome, don't
                # relabel it as stopped.
                continue
            # Claim the outcome first (see docstring), then SIGKILL -- no
            # SIGTERM grace, unlike _terminate_process / stop_task.
            task.exit_reason = EXIT_STOPPED
            try:
                task.process.kill()
                try:
                    returncode = task.process.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:  # pragma: no cover - wedged child
                    returncode = task.process.returncode
            except OSError:  # pragma: no cover - child already gone
                returncode = task.process.poll()
            task.returncode = returncode
            task.exit_code = _own_exit_code(returncode)
            task.error = (
                "task was stopped before it finished"
                if task.exit_code is None
                else f"task was stopped; it exited with code {task.exit_code} "
                "during shutdown"
            )
            task.duration_seconds = time.monotonic() - task.started_at
            self._record_completion(task)
            killed.append(
                {
                    "task_id": task.task_id,
                    "pid": task.pid,
                    "stopped": True,
                    "exit_reason": task.exit_reason,
                    "exit_code": task.exit_code,
                    "timeout": task.timeout,
                    "returncode": returncode,
                }
            )
        return killed

    def get_task(self, task_id: str) -> Task:
        """Return the :class:`Task` registered under ``task_id``.

        Raises:
            KeyError: If no task with that id exists.
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task id: {task_id}")
        return task

    def stop_task(self, task_id: str) -> dict[str, Any]:
        """Stop a running task (SIGTERM, then SIGKILL after 10s).

        The task is labelled :data:`EXIT_STOPPED` *before* the signal is sent,
        so the wait thread cannot relabel a child that dies right away as
        having "finished" -- and a child that exits on its own a microsecond
        earlier keeps its :data:`EXIT_FINISHED` label.

        Args:
            task_id: The id returned by :meth:`start_task`.

        Returns:
            Dict with ``task_id``, ``pid``, ``stopped``, ``exit_reason``,
            ``exit_code`` (``None`` when the child was killed rather than
            exiting by itself), ``timeout`` and ``returncode``.

        Raises:
            KeyError: If no task with that id exists.
        """
        task = self.get_task(task_id)
        was_running = task.process.poll() is None
        if was_running:
            # Claim the outcome first (see the docstring), then terminate.
            task.exit_reason = EXIT_STOPPED
        returncode = _terminate_process(task.process)
        task.returncode = returncode
        if was_running:
            task.exit_code = _own_exit_code(returncode)
            task.error = (
                "task was stopped before it finished"
                if task.exit_code is None
                else f"task was stopped; it exited with code {task.exit_code} "
                "during shutdown"
            )
            task.duration_seconds = time.monotonic() - task.started_at
        else:
            # poll() can beat the wait thread's wait(): label it now if it has
            # not been labelled yet (no-op once the thread got there first).
            self._label_exit(task, returncode)
        return {
            "task_id": task.task_id,
            "pid": task.pid,
            "stopped": True,
            "exit_reason": task.exit_reason,
            "exit_code": task.exit_code,
            "timeout": task.timeout,
            "returncode": returncode,
        }

    def wait_for_task(
        self,
        task_ids: list[str],
        on_task_complete: Callable[[dict[str, Any]], None] | None = None,
        timeout: float | None = None,
        max_output_lines: int | None = DEFAULT_MAX_OUTPUT_LINES,
    ) -> dict[str, Any]:
        """Block until every listed task has finished.

        Tasks are drained in **completion order**: as soon as a task's child
        process exits, its result is collected and, if ``on_task_complete``
        is provided, the callback is invoked with that task's result dict --
        so the caller can report ``"<task id> complete"`` the moment each task
        finishes, without waiting for the slowest one.

        Each finished task's result dict also carries its captured
        ``stdout`` and ``stderr`` content inline (capped at
        ``max_output_lines`` lines), so the caller can check the task's
        results directly without reading the temp files.  ``*_truncated``
        flags say whether the cap cut the content short; the temp files
        (``stdout_filename`` / ``stderr_filename``) always hold the full
        output.  A stream that could not be read (e.g. its temp file was
        already removed) is reported as ``None`` with ``truncated=False``.

        Args:
            task_ids: The ids returned by :meth:`start_task`.
            on_task_complete: Optional callback invoked (in the waiting
                thread) with each task's result dict as soon as that task
                finishes.  The result dict is the same shape as the entries
                in the returned ``tasks`` list (including the inline output
                and the exit status fields).
            timeout: Optional total **wait** budget in seconds -- how long to
                block here, *not* how long the tasks may run.  ``None`` (the
                default) waits indefinitely.  It never kills anything: when the
                budget expires before every listed task has exited, the results
                collected so far are returned with ``timed_out=True`` and
                ``pending_task_ids`` listing the tasks still running (the
                caller can then stop them with :meth:`stop_task`).  A task's
                own lifetime cap is :meth:`start_task`'s ``timeout``, armed at
                spawn and enforced by its wait thread whether or not anyone
                waits -- that is what marks a task ``exit_reason`` =
                :data:`EXIT_TIMEOUT`.
            max_output_lines: Maximum number of lines of ``stdout`` /
                ``stderr`` to return per task (default
                :data:`DEFAULT_MAX_OUTPUT_LINES`).  ``None`` returns the
                full content of each stream.

        Returns:
            Dict with a ``tasks`` list, one entry per task: ``task_id``,
            ``pid``, ``working_dir``, ``summary``, ``exit_reason``,
            ``exit_code``, ``returncode``, ``timeout``, ``duration_seconds``,
            ``stdout_filename``, ``stderr_filename``, ``stdout``, ``stderr``,
            ``stdout_truncated``, ``stderr_truncated`` and ``error`` (None
            when the child exited normally); plus ``timed_out`` (bool, True
            when ``timeout`` expired before all tasks finished),
            ``pending_task_ids`` (the ids of the tasks still running when
            the call returned) and ``terminated_task_ids`` (the ids of the
            tasks in ``tasks`` that janito killed -- see below).

        Reading the exit status
        ~~~~~~~~~~~~~~~~~~~~~~~
        ``exit_reason`` is the authoritative "did it finish or was it
        terminated?" field: :data:`EXIT_FINISHED` (it exited on its own),
        :data:`EXIT_TIMEOUT` (its own ``timeout`` fired), :data:`EXIT_STOPPED`
        (StopTask), :data:`EXIT_KILLED` (killed by a signal nobody in janito
        sent) or :data:`EXIT_ERROR` (the wait itself failed).  ``exit_code`` is
        the child's own status when it produced one and ``None`` otherwise, so
        a killed task reports no exit code rather than a misleading one.

        Do not read ``returncode`` to decide success: it is the raw
        :attr:`subprocess.Popen.returncode` (negative for signal deaths on
        POSIX, ``1`` for ``kill()`` on Windows), kept for forensics.  Note also
        that ``exit_code`` can be non-``None`` *alongside* a terminated
        ``exit_reason`` -- when a child traps SIGTERM it may exit cleanly within
        the termination grace period and report code 0 despite having run out of
        time.

        Raises:
            KeyError: If any task id is unknown.
        """
        pending = list(task_ids)
        results: list[dict[str, Any]] = []
        deadline = None if timeout is None else time.monotonic() + timeout
        while pending:
            # Drain any tasks that have already completed, in the order their
            # wait threads recorded them (true completion order).
            with self._completion_lock:
                completed = [t for t in self._completion_order if t in pending]
            if completed:
                task_id = completed[0]
                with self._completion_lock:
                    self._completion_order.remove(task_id)
            else:
                # Nothing finished yet: block on the first pending task's
                # event; its wait thread signals done the moment it exits.
                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                task = self.get_task(pending[0])
                if not task.done.wait(remaining):
                    # The budget expired without any task finishing.
                    break
                continue

            pending.remove(task_id)
            task = self.get_task(task_id)
            stdout, stdout_truncated = _read_output_file(
                task.stdout_filename, max_output_lines
            )
            stderr, stderr_truncated = _read_output_file(
                task.stderr_filename, max_output_lines
            )
            result = {
                "task_id": task.task_id,
                "pid": task.pid,
                "working_dir": task.working_dir,
                "summary": task.summary,
                "exit_reason": task.exit_reason,
                "exit_code": task.exit_code,
                "returncode": task.returncode,
                "timeout": task.timeout,
                "duration_seconds": task.duration_seconds,
                "stdout_filename": task.stdout_filename,
                "stderr_filename": task.stderr_filename,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "error": task.error,
            }
            results.append(result)
            if on_task_complete is not None:
                on_task_complete(result)

        timed_out = bool(pending)
        return {
            "tasks": results,
            "timed_out": timed_out,
            "pending_task_ids": pending,
            # Tasks that ran to completion here but were killed by janito
            # (their own ``timeout`` fired, or StopTask) rather than exiting on
            # their own -- their ``exit_code`` is None unless they shut down
            # cleanly during the termination grace period.
            "terminated_task_ids": [
                result["task_id"]
                for result in results
                if result["exit_reason"] in TERMINATED_REASONS
            ],
        }

    # -- shutdown ------------------------------------------------------------

    def cleanup(self) -> None:
        """Terminate every live child and remove their temp output files.

        Registered with ``atexit`` at construction time; also safe to call
        manually (idempotent).

        Contract change (issue #101): killing now delegates to
        :meth:`kill_all`, which sends **SIGKILL immediately** rather than the
        previous SIGTERM.  The grace period SIGTERM afforded a child was of no
        practical use at interpreter exit -- there is no event loop left to
        service a clean shutdown, and the process is going away regardless --
        so a stuck child that ignored SIGTERM could only be force-killed after
        burning the full grace window.  SIGKILL reaps it at once.  Temp-file
        removal (unchanged) follows.
        """
        self.kill_all()
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            for filename in (task.stdout_filename, task.stderr_filename):
                try:
                    os.unlink(filename)
                except OSError:
                    pass


# Module-level singleton used by the StartTask / StopTask / WaitForTask tools.
task_manager = TaskManager()
