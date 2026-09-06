#!/usr/bin/env python3
"""
ListTasks Tool - Lists the tasks known to the TaskManager (issue #101).

Returns a snapshot of every task -- running *and* finished -- so the model can
see what has been started, what is still in flight, and how each one ended,
without waiting on anything (unlike WaitForTask, which blocks until the listed
tasks exit).  The snapshot is taken from :meth:`TaskManager.list_tasks`; the
shell's end-of-turn "tasks still running" notice uses the same manager.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito.tools.tasks.list_tasks [args]
For AI function calling, use through the tool registry (tooling.tools_registry).
"""

import json
from typing import Any

from ...taskmanager import task_manager
from ...tooling import BaseTool
from ...tooling.decorator import tool


@tool(permissions="r")
class ListTasks(BaseTool):
    """
    Tool for listing all tasks (running and finished) known to the manager.

    Returns one entry per task: its id, one-line summary, state -- the reason
    it ended, ``running`` while the child is still alive and then ``finished``
    / ``timeout`` / ``stopped`` / ``killed`` / ``error`` -- plus its pid,
    working directory and duration in seconds.  Running tasks are listed first
    (in start order), then finished tasks.  Use it to check on tasks started
    with StartTask without blocking: StopTask terminates a task and WaitForTask
    waits for (and collects the output of) one.

    Args:
        running_only (bool, optional): When True, return only the tasks still
            running.  Default False (finished tasks are included, each with
            its state).
    """

    def run(self, running_only: bool = False) -> dict[str, Any]:
        """
        List the tasks known to the manager.

        Args:
            running_only (bool): Return only currently-running tasks
                (default False -- finished tasks are included with their
                state).

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool
                - 'tasks': list of per-task dicts, each with 'task_id',
                  'summary', 'state', 'running', 'pid', 'working_dir' and
                  'duration_seconds'
                - 'count': total number of tasks returned
                - 'running_count': number of tasks still running
                - 'error': error message (only present if success is False)
        """
        try:
            self.report_start("Listing tasks", end="")

            if running_only:
                tasks = task_manager.running_tasks()
            else:
                tasks = task_manager.list_tasks()

            running_count = sum(1 for t in tasks if t.get("running"))
            self.report_result(
                f"{running_count} running, {len(tasks) - running_count} finished"
                if not running_only
                else f"{len(tasks)} task(s) running"
            )
            return {
                "success": True,
                "tasks": tasks,
                "count": len(tasks),
                "running_count": running_count,
            }

        except Exception as e:  # noqa: BLE001 - surfaced to the caller
            self.report_error(f"Error: {e}")
            return {"success": False, "error": str(e)}


# ── CLI testing harness ─────────────────────────────────────────────────────
def main():
    """Command line interface for testing the ListTasks tool."""
    import argparse

    parser = argparse.ArgumentParser(description="List all tasks (running and finished) known to the manager")
    parser.add_argument(
        "--running-only",
        action="store_true",
        help="List only the tasks that are still running",
    )
    parser.add_argument("--json", "-j", action="store_true", help="Output in JSON format")
    args = parser.parse_args()

    result = ListTasks().run(running_only=args.running_only)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            if not result["tasks"]:
                print("  (no tasks)")
            for task in result["tasks"]:
                state = "running" if task["running"] else task["state"]
                duration = task.get("duration_seconds")
                duration_text = f", {duration:.1f}s" if duration is not None else ""
                summary = task.get("summary") or task["task_id"]
                print(f"  {task['task_id']}  [{state}{duration_text}]  {summary}  " f"(pid {task['pid']})")
        else:
            print(f"  ❌ Failed: {result.get('error', 'Unknown error')}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    exit(main())
