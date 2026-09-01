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

from ...taskmanager import task_manager
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


def _task_summary(task_id: str) -> str:
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
    return task_id


def _wait_with_spinner(
    task_ids: list[str],
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
        task_ids (list[str]): The ids of the tasks to wait for (returned by
            StartTask).
        timeout (float, optional): Total wait budget in seconds.  None (the
            default) waits indefinitely.  When the budget expires before
            every listed task has finished, the results collected so far are
            returned with timed_out=True and pending_task_ids listing the
            tasks still running (stop them with StopTask if needed).
        max_lines (int, optional): Maximum number of lines of each task's
            stdout/stderr to return inline.  None (the default) uses the
            manager's default cap; a stream cut short is flagged with
            stdout_truncated/stderr_truncated and its full content remains
            available in the temp output file.
    """

    def run(
        self,
        task_ids: list[str],
        timeout: float | None = None,
        max_lines: int | None = None,
    ) -> dict[str, Any]:
        """
        Wait for the listed tasks to finish.

        Each task is announced up front as "Waiting for task n/total :
        <summary>".  As each task completes, its "<task id> complete" message
        is printed (via the reporter) the moment it finishes -- before the
        slowest task is done -- and its captured stdout/stderr content is
        returned inline in the result.

        Args:
            task_ids (list[str]): The ids of the tasks to wait for.
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
                  'summary', 'pid', 'returncode', 'stdout_filename',
                  'stderr_filename', 'stdout', 'stderr',
                  'stdout_truncated', 'stderr_truncated' and 'error'
                - 'timed_out': bool (True when timeout expired before all
                  tasks finished)
                - 'pending_task_ids': list of task ids still running when the
                  call returned
                - 'error': error message (only present if success is False)
        """
        try:

            def on_task_complete(result):
                self.report_result(f"task {result['task_id']} complete")

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
                # "task X complete" line prints above the live spinner).
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

            if info.get("timed_out"):
                self.report_result(
                    f"timed out after {timeout}s; "
                    f"{len(info['pending_task_ids'])} task(s) still running"
                )
            else:
                self.report_result("all tasks finished")
            return {"success": True, **info}

        except KeyError as e:
            self.report_error(str(e))
            return {"success": False, "error": str(e), "task_ids": task_ids}

        except Exception as e:
            self.report_error(f"Error: {e}")
            return {"success": False, "error": str(e), "task_ids": task_ids}


# ── CLI testing harness ────────────────────────────────────────────────────
def main():
    """Command line interface for testing the WaitForTask tool."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Wait for one or more tasks (started with StartTask) to finish"
    )
    parser.add_argument(
        "task_ids",
        nargs="+",
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
                print(
                    f"  ✅ task {task['task_id']} exited with "
                    f"return code {task['returncode']}"
                )
                if args.show_output:
                    stdout = task.get("stdout")
                    stderr = task.get("stderr")
                    if stdout is not None:
                        print(f"    --- stdout ---\n{stdout}", end="")
                        if not stdout.endswith("\n"):
                            print()
                    if stderr is not None:
                        print(f"    --- stderr ---\n{stderr}", end="")
                        if not stderr.endswith("\n"):
                            print()
            if result.get("timed_out"):
                print(
                    f"  ⏰ timed out; still running: "
                    f"{', '.join(result['pending_task_ids'])}"
                )
        else:
            print(f"  ❌ Failed: {result.get('error', 'Unknown error')}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    exit(main())
