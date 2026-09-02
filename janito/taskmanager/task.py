"""The :class:`Task` dataclass for the task manager package.

One :class:`Task` records a spawned child process and its exit status (see
the package docstring for the ``exit_reason`` / ``exit_code`` vocabulary).
"""

import subprocess
import threading
import time
from dataclasses import dataclass, field

from .constants import EXIT_RUNNING, TERMINATED_REASONS


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
