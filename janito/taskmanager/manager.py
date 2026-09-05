"""The :class:`TaskManager` registry for the task manager package.

Spawns each task as a separate ``janito`` process, tracks its exit status,
and provides the wait / stop / list / kill-all operations used by the
StartTask / StopTask / WaitForTask / ListTasks tools (see the package
docstring for the full contract).
"""

import atexit
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

from .command import build_task_command
from .constants import (
    DEFAULT_MAX_OUTPUT_LINES,
    EXIT_ERROR,
    EXIT_FINISHED,
    EXIT_KILLED,
    EXIT_RUNNING,
    EXIT_STOPPED,
    EXIT_TIMEOUT,
    KILL_GRACE_SECONDS,
    TERMINATED_REASONS,
)
from .process import (
    _normalise_timeout,
    _own_exit_code,
    _read_output_file,
    _terminate_process,
)
from .task import Task


class TaskManager:
    """Registry of parallel tasks, each a child ``janito`` process."""

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._lock = threading.Lock()
        self._next_task_id: int = 1
        # task_ids in the order their child processes exited (appended by the
        # per-task wait threads), so wait_for_task() can drain them in true
        # completion order even when several finish near-simultaneously.
        self._completion_order: list[int] = []
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

        with self._lock:
            task_id = self._next_task_id
            self._next_task_id += 1
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
            "exit_code": task.exit_code,
            "error": task.error,
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

    def get_task(self, task_id: int) -> Task:
        """Return the :class:`Task` registered under ``task_id``.

        Raises:
            KeyError: If no task with that id exists.
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task id: {task_id}")
        return task

    def stop_task(self, task_id: int) -> dict[str, Any]:
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
        task_ids: list[int],
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
