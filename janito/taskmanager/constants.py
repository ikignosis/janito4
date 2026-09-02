"""Constants for the task manager package.

Timeouts, grace periods and the exit-reason vocabulary shared by the
process helpers, the :class:`~janito.taskmanager.Task` dataclass and
:class:`~janito.taskmanager.TaskManager` (see the package docstring for the
full picture).
"""

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
