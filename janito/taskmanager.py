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
record its return code / error -- that is what :meth:`TaskManager.wait_for_task`
blocks on and what :meth:`TaskManager.stop_task` terminates.

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
    """A running (or finished) parallel task."""

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
    returncode: int | None = None
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


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

        Returns:
            Dict with ``task_id``, ``pid``, ``working_dir`` (the resolved
            child working directory), ``summary``, ``stdout_filename`` and
            ``stderr_filename``.

        Raises:
            ValueError: If ``description`` is empty, ``working_dir`` is not a
                directory, or ``privileges`` is invalid.
            RuntimeError: If the child could not be spawned or the
                description could not be sent to it.
        """
        if not description or not description.strip():
            raise ValueError("description must be a non-empty string")

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
            "stdout_filename": stdout_filename,
            "stderr_filename": stderr_filename,
        }

    def _wait_for_exit(self, task: Task) -> None:
        """Daemon-thread body: wait for the child and record the outcome."""
        try:
            task.returncode = task.process.wait()
        except Exception as e:  # noqa: BLE001 - record any wait failure
            task.error = str(e)
        finally:
            # Record the true completion order before signalling done, so
            # wait_for_task() can drain tasks in the order they finished.
            with self._completion_lock:
                self._completion_order.append(task.task_id)
            task.done.set()

    # -- queries -------------------------------------------------------------

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

        Args:
            task_id: The id returned by :meth:`start_task`.

        Returns:
            Dict with ``task_id``, ``pid``, ``stopped`` and ``returncode``.

        Raises:
            KeyError: If no task with that id exists.
        """
        task = self.get_task(task_id)
        if task.process.poll() is None:
            task.process.terminate()
            try:
                task.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                task.process.kill()
                task.process.wait(timeout=5)
        return {
            "task_id": task.task_id,
            "pid": task.pid,
            "stopped": True,
            "returncode": task.process.returncode,
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
                in the returned ``tasks`` list (including the inline output).
            timeout: Optional total wait budget in seconds.  ``None`` (the
                default) waits indefinitely.  When the budget expires before
                every listed task has exited, the results collected so far
                are returned with ``timed_out=True`` and ``pending_task_ids``
                listing the tasks still running (the caller can then stop
                them with :meth:`stop_task`).
            max_output_lines: Maximum number of lines of ``stdout`` /
                ``stderr`` to return per task (default
                :data:`DEFAULT_MAX_OUTPUT_LINES`).  ``None`` returns the
                full content of each stream.

        Returns:
            Dict with a ``tasks`` list, one entry per task: ``task_id``,
            ``pid``, ``working_dir``, ``summary``, ``returncode``,
            ``stdout_filename``, ``stderr_filename``, ``stdout``, ``stderr``,
            ``stdout_truncated``, ``stderr_truncated`` and ``error`` (None
            when the child exited normally); plus ``timed_out`` (bool, True
            when ``timeout`` expired before all tasks finished) and
            ``pending_task_ids`` (the ids of the tasks still running when
            the call returned).

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
                "returncode": task.returncode,
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
        }

    # -- shutdown ------------------------------------------------------------

    def cleanup(self) -> None:
        """Terminate running children and remove their temp output files.

        Registered with ``atexit`` at construction time; also safe to call
        manually (idempotent).
        """
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            if task.process.poll() is None:
                task.process.terminate()
        for task in tasks:
            for filename in (task.stdout_filename, task.stderr_filename):
                try:
                    os.unlink(filename)
                except OSError:
                    pass


# Module-level singleton used by the StartTask / StopTask / WaitForTask tools.
task_manager = TaskManager()
