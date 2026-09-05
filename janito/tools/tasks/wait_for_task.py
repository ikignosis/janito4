#!/usr/bin/env python3
"""
WaitForTask Tool - Waits for one or more tasks (started with StartTask) to
finish and reports their exit codes.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito.tools.tasks.wait_for_task [args]
For AI function calling, use through the tool registry (tooling.tools_registry).
"""

import json
import queue
import sys
import threading
from typing import Any

from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ...taskmanager import (
    EXIT_ERROR,
    EXIT_FINISHED,
    EXIT_KILLED,
    EXIT_STOPPED,
    EXIT_TIMEOUT,
    TERMINATED_REASONS,
    task_manager,
)
from ...tooling import BaseTool
from ...tooling.decorator import tool
from ...tooling.reporter import get_console, get_report_handler


def _should_show_spinner() -> bool:
    """Whether the CLI should animate the wait with a Rich spinner.

    Only on an interactive terminal (stderr is a TTY) and only when no web
    report handler is installed: web mode streams structured events to the
    browser, which already renders its own "Running" spinner on the tool
    card -- animating one here would garble the event stream. Piped/CI
    output keeps the plain progress lines.
    """
    if not sys.stderr.isatty():
        return False
    return get_report_handler() is None


def _task_summary(task_id: int) -> str:
    """Best-effort one-line summary for a task id.

    Looks the task up in the ``task_manager`` so WaitForTask can present
    "Waiting for task n/total : <summary>" lines to the user.  Falls back to
    the task id itself when the manager does not expose tasks (e.g. a fake
    manager in tests) or when the task id is unknown.

    Args:
        task_id: The id returned by StartTask.

    Returns:
        The task's stored summary, or ``task_id`` when no summary is
        available.
    """
    try:
        task = task_manager.get_task(task_id)
        if task.summary:
            return task.summary
    except (AttributeError, KeyError):
        pass
    return str(task_id)


def _format_duration(seconds: float | None) -> str:
    """Render a task runtime as a short ``41.2s`` suffix (or ``""``)."""
    if seconds is None:
        return ""
    return f", {seconds:.1f}s"


def _describe_outcome(result: dict[str, Any]) -> str:
    """One-line, reason-aware description of how a task ended.

    Carries the exit code when the task produced one, and says so explicitly
    when it did not -- a task janito killed has no exit code of its own, and
    leaving that unstated is what makes ``returncode``-based reporting
    misleading.

    Args:
        result: A task result dict as returned by
            :meth:`janito.taskmanager.TaskManager.wait_for_task`.

    Returns:
        e.g. ``"finished (exit 0, 41.2s)"``, ``"finished (exit 1, 12.0s)"``,
        ``"TIMED OUT after 120s (killed, no exit code, 120.3s)"`` or
        ``"terminated (stopped, no exit code, 7.4s)"``.
    """
    reason = result.get("exit_reason") or EXIT_FINISHED
    exit_code = result.get("exit_code")
    duration = _format_duration(result.get("duration_seconds"))

    if reason == EXIT_TIMEOUT:
        budget = result.get("timeout")
        suffix = (
            f"exit {exit_code} during shutdown"
            if exit_code is not None
            else "killed, no exit code"
        )
        after = f" after {budget:g}s" if budget else ""
        return f"TIMED OUT{after} ({suffix}{duration})"
    if reason == EXIT_STOPPED:
        suffix = (
            f"exit {exit_code} during shutdown"
            if exit_code is not None
            else "no exit code"
        )
        return f"terminated (stopped, {suffix}{duration})"
    if reason == EXIT_KILLED:
        code = result.get("returncode")
        return f"terminated (killed by signal {code}, no exit code{duration})"
    if reason == EXIT_ERROR:
        return f"failed to report ({result.get('error') or 'wait error'})"
    # `exit_code` is None when the child never produced one (a fake/older
    # manager, or a task still recorded as running) -- say so rather than
    # printing a misleading "exit None".
    if exit_code is None:
        return f"finished (no exit code{duration})"
    return f"finished (exit {exit_code}{duration})"


def _summarize_outcomes(results: list[dict[str, Any]]) -> str:
    """Aggregate ``"1 failed, 2 terminated"``-style counts (or ``""``).

    Counts off ``exit_reason`` / ``exit_code`` rather than ``returncode``: a
    task killed by janito has a negative ``returncode`` on POSIX but no exit
    code of its own, and one that shut down cleanly during the termination
    grace period can even report ``exit 0`` -- so only the reason distinguishes
    them.  Returns ``""`` when everything finished successfully, keeping the
    common case's line identical to a plain "all tasks finished".

    Args:
        results: The per-task result dicts from the manager.

    Returns:
        A comma-joined breakdown, or ``""`` when there is nothing to flag.
    """
    failed = sum(
        1
        for r in results
        if r.get("exit_reason") == EXIT_FINISHED and r.get("exit_code") not in (0, None)
    )
    terminated = sum(1 for r in results if r.get("exit_reason") in TERMINATED_REASONS)
    unreported = sum(1 for r in results if r.get("exit_reason") == EXIT_ERROR)
    parts = []
    if failed:
        parts.append(f"{failed} failed")
    if terminated:
        parts.append(f"{terminated} terminated")
    if unreported:
        parts.append(f"{unreported} unreported")
    return ", ".join(parts)


def _wait_with_spinner(
    task_ids: list[int],
    timeout: float | None,
    on_task_complete: Any,
    max_output_lines: int | None = None,
) -> dict[str, Any]:
    """Block on ``task_manager.wait_for_task`` while animating a Rich spinner.

    The blocking wait runs in a background thread; the main thread drives a
    transient ``Progress`` spinner on the shared reporter console (see
    ``get_console``), mirroring ``janito/ui/stream_runner.py``. Completed
    tasks are drained through a queue so the description can count down
    ("Waiting for 7 tasks..."); each per-task completion line is printed by
    ``on_task_complete`` (running in the waiting thread) *above* the live
    spinner, since Rich ``Live`` renders concurrent console output above its
    live region. Exceptions raised by the manager propagate to the caller.

    Args:
        task_ids: The ids returned by StartTask.
        timeout: Total wait budget in seconds (None waits indefinitely).
        on_task_complete: Callback invoked with each task's result dict the
            moment that task finishes.
        max_output_lines: Maximum number of lines of each task's
            stdout/stderr to return inline (None = manager's default cap).

    Returns:
        The ``task_manager.wait_for_task`` result dict.
    """
    completed: queue.Queue[dict[str, Any]] = queue.Queue()
    result: list[dict[str, Any]] = []
    error: list[BaseException] = []

    def _on_complete(task_result: dict[str, Any]) -> None:
        if on_task_complete is not None:
            on_task_complete(task_result)
        completed.put(task_result)

    def target() -> None:
        try:
            result.append(
                task_manager.wait_for_task(
                    task_ids,
                    on_task_complete=_on_complete,
                    timeout=timeout,
                    max_output_lines=max_output_lines,
                )
            )
        except Exception as exc:  # noqa: BLE001 - surfaced by the caller
            error.append(exc)

    worker = threading.Thread(target=target)
    worker.start()

    remaining = len(task_ids)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=get_console(),
        transient=True,
    ) as progress:
        task = progress.add_task(f"Waiting for {remaining} task(s)", total=None)
        while worker.is_alive() or not completed.empty():
            while True:
                try:
                    completed.get_nowait()
                except queue.Empty:
                    break
                remaining -= 1
                progress.update(task, description=f"Waiting for {remaining} task(s)")
            worker.join(timeout=0.1)

    if error:
        raise error[0]
    return result[0]


@tool(permissions="r")
class WaitForTask(BaseTool):
    """
    Tool for waiting for one or more tasks (started with StartTask) to finish.

    Blocks until every listed task has exited and reports their return codes,
    output file names and the captured stdout/stderr content of each task --
    so the results can be checked directly, without reading the temp output
    files (use the stdout_filename/stderr_filename paths with ReadFile only
    when more than max_lines of output is needed).
    Each task is announced up front with its one-line summary ("Waiting for
    task n/total : <summary>"), taken from the Task record stored by
    StartTask.

    Args:
        task_ids (list[int]): The ids of the tasks to wait for (returned by
            StartTask).
        timeout (float, optional): Total wait budget in seconds.  None (the
            default) waits indefinitely.  When the budget expires before
            every listed task has finished, the results collected so far are
            returned with timed_out=True and pending_task_ids listing the
            tasks still running (stop them with StopTask if needed).  This
            bounds only *this call* and kills nothing: a task's own runtime cap
            is StartTask's timeout argument.
        max_lines (int, optional): Maximum number of lines of each task's
            stdout/stderr to return inline.  None (the default) uses the
            manager's default cap; a stream cut short is flagged with
            stdout_truncated/stderr_truncated and its full content remains
            available in the temp output file.
    """

    def run(
        self,
        task_ids: list[int],
        timeout: float | None = None,
        max_lines: int | None = None,
    ) -> dict[str, Any]:
        """
        Wait for the listed tasks to finish.

        Each task is announced up front as "Waiting for task n/total :
        <summary>".  As each task finishes, its outcome -- how it ended and the
        exit code it produced, when it has one -- is printed the moment it
        finishes, before the slowest task is done.  Its captured
        stdout/stderr content is returned inline in the result.

        Args:
            task_ids (list[int]): The ids of the tasks to wait for.
            timeout (float, optional): Total wait budget in seconds.  None
                (default) waits indefinitely.  When it expires before every
                task has finished, the results collected so far are returned
                with timed_out=True and pending_task_ids listing the tasks
                still running.
            max_lines (int, optional): Maximum number of lines of each task's
                stdout/stderr to return inline.  None (the default) uses the
                manager's default cap.

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool
                - 'tasks': list of per-task results, each with 'task_id',
                  'summary', 'pid', 'exit_reason', 'exit_code', 'returncode',
                  'timeout', 'duration_seconds', 'stdout_filename',
                  'stderr_filename', 'stdout', 'stderr', 'stdout_truncated',
                  'stderr_truncated' and 'error'.  'exit_reason' is 'finished'
                  when the task ran to completion (its 'exit_code' is then the
                  code it exited with), or 'timeout' / 'stopped' / 'killed'
                  when it was terminated -- in which case 'exit_code' is None
                  unless it shut down cleanly during the termination grace
                  period.
                - 'timed_out': bool (True when timeout expired before all
                  tasks finished)
                - 'pending_task_ids': list of task ids still running when the
                  call returned
                - 'terminated_task_ids': list of the reported task ids that
                  janito terminated (exit_reason 'timeout' / 'stopped' /
                  'killed'), so failures and terminations are easy to tell
                  apart
                - 'error': error message (only present if success is False)
        """
        try:

            def on_task_complete(result):
                # Reason-aware line: a killed task has no exit code of its own,
                # so report the outcome instead of a bare return code.
                self.report_result(
                    f"task {result['task_id']} {_describe_outcome(result)}"
                )

            # Announce each task up front with its one-line summary, e.g.
            # "Waiting for task 1/3 : Fix login page", so the user can see
            # exactly what is being waited for.  The summary is stored on
            # the Task by StartTask and looked up here via the manager.
            total = len(task_ids)
            for i, task_id in enumerate(task_ids, start=1):
                self.report_start(
                    f"Waiting for task {i}/{total} : {_task_summary(task_id)}"
                )

            if _should_show_spinner():
                # Interactive terminal: animate the wait with a Rich spinner
                # (the description counts down as tasks finish and each
                # per-task outcome line prints above the live spinner).
                info = _wait_with_spinner(
                    task_ids,
                    timeout,
                    on_task_complete=on_task_complete,
                    max_output_lines=max_lines,
                )
            else:
                # Web mode / piped output: keep the plain progress lines.
                info = task_manager.wait_for_task(
                    task_ids,
                    on_task_complete=on_task_complete,
                    timeout=timeout,
                    max_output_lines=max_lines,
                )

            # Distinguish the *wait* budget that just expired from a task's own
            # StartTask timeout: the first leaves tasks running (reported
            # here), the second kills them (reported per task above).
            breakdown = _summarize_outcomes(info.get("tasks") or [])
            if info.get("timed_out"):
                self.report_result(
                    f"wait budget of {timeout:g}s expired; "
                    f"{len(info['pending_task_ids'])} task(s) still running"
                )
            elif breakdown:
                self.report_result(f"all tasks finished: {breakdown}")
            else:
                self.report_result("all tasks finished")
            return {"success": True, **info}

        except KeyError as e:
            self.report_error(str(e))
            return {"success": False, "error": str(e), "task_ids": task_ids}

        except (ValueError, OSError, RuntimeError) as e:
            self.report_error(f"Error: {e}")
            return {"success": False, "error": str(e), "task_ids": task_ids}


# ── CLI testing harness ────────────────────────────────────────────────────
def _print_stream(name: str, content: str | None) -> None:
    """Print a task's captured stdout/stderr section (CLI harness only)."""
    if content is None:
        return
    print(f"    --- {name} ---\n{content}", end="")
    if not content.endswith("\n"):
        print()


def _print_task(task: dict[str, Any], show_output: bool) -> None:
    """Print one task's outcome line (CLI harness only)."""
    # A terminated task has no exit code of its own: use a distinct marker
    # instead of implying it exited normally.
    mark = "✅" if task.get("exit_reason") == EXIT_FINISHED else "⏹"
    print(f"  {mark} task {task['task_id']} {_describe_outcome(task)}")
    if show_output:
        _print_stream("stdout", task.get("stdout"))
        _print_stream("stderr", task.get("stderr"))


def main():
    """Command line interface for testing the WaitForTask tool."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Wait for one or more tasks (started with StartTask) to finish"
    )
    parser.add_argument(
        "task_ids",
        nargs="+",
        type=int,
        help="The ids of the tasks to wait for (returned by StartTask)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Total wait budget in seconds (default: wait indefinitely)",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=None,
        help=(
            "Maximum number of lines of each task's stdout/stderr to "
            "return inline (default: the manager's cap)"
        ),
    )
    parser.add_argument(
        "--show-output",
        action="store_true",
        help="Print each task's captured stdout/stderr content",
    )
    parser.add_argument(
        "--json", "-j", action="store_true", help="Output in JSON format"
    )
    args = parser.parse_args()

    result = WaitForTask().run(
        task_ids=args.task_ids, timeout=args.timeout, max_lines=args.max_lines
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            for task in result["tasks"]:
                _print_task(task, args.show_output)
            if result.get("timed_out"):
                print(
                    f"  ⏳ wait budget expired; still running: "
                    f"{', '.join(str(x) for x in result['pending_task_ids'])}"
                )
            terminated = result.get("terminated_task_ids") or []
            if terminated:
                print(f"  ⏹ terminated: {', '.join(str(x) for x in terminated)}")
        else:
            print(f"  ❌ Failed: {result.get('error', 'Unknown error')}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    exit(main())
