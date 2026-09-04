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
    it exceeded its ``timeout``), ``"stopped"`` (killed by
    :meth:`TaskManager.stop_task`) or ``"error"`` (the wait itself failed).

``exit_code``
    The child's own exit status, or ``None`` when it never produced one.

A terminated task may still carry a non-``None`` ``exit_code``: the grace
period lets a child that traps SIGTERM shut down and exit by itself, so
``exit_code == 0`` with ``exit_reason == "timeout"`` is a real (and
meaningful) combination.  Callers must therefore read ``exit_reason`` to tell
success from termination, and never infer it from ``returncode`` -- which is
kept as the raw :attr:`subprocess.Popen.returncode` for forensics and is
negative for signal deaths on POSIX but ``1`` for ``kill()`` on Windows.

Package layout (issue #104): the former single ``janito/taskmanager.py`` is
split into ``constants`` (exit-reason vocabulary and grace periods),
``process`` (output reading, timeout validation, exit-code mapping and
termination), ``command`` (child command-line construction), ``task`` (the
:class:`Task` dataclass) and ``manager`` (:class:`TaskManager` and the
``task_manager`` singleton).  Everything is re-exported here, so the public
``janito.taskmanager`` surface is unchanged.
"""

from .command import build_task_command, privilege_flags
from .constants import (
    DEFAULT_MAX_OUTPUT_LINES,
    EXIT_ERROR,
    EXIT_FINISHED,
    EXIT_KILLED,
    EXIT_RUNNING,
    EXIT_STOPPED,
    EXIT_TIMEOUT,
    KILL_GRACE_SECONDS,
    TERM_GRACE_SECONDS,
    TERMINATED_REASONS,
)
from .manager import TaskManager, task_manager
from .task import Task

__all__ = [
    "DEFAULT_MAX_OUTPUT_LINES",
    "EXIT_ERROR",
    "EXIT_FINISHED",
    "EXIT_KILLED",
    "EXIT_RUNNING",
    "EXIT_STOPPED",
    "EXIT_TIMEOUT",
    "KILL_GRACE_SECONDS",
    "TERM_GRACE_SECONDS",
    "TERMINATED_REASONS",
    "Task",
    "TaskManager",
    "build_task_command",
    "privilege_flags",
    "task_manager",
]
