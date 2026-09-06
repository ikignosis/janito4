"""Process-level helpers for the task manager package.

Reading a child's captured output, validating its lifetime cap, mapping raw
return codes to the child's own exit status and terminating a child with the
shared SIGTERM/SIGKILL escalation (see the package docstring for the
exit-reason vocabulary).
"""

import subprocess

from .constants import _TRUNCATED_MARKER, KILL_GRACE_SECONDS, TERM_GRACE_SECONDS


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
        raise ValueError(f"timeout must be a positive number of seconds, got {seconds:g}")
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
