#!/usr/bin/env python3
"""
StopTask Tool - Stops a running task started with the StartTask tool.

The task process is terminated (SIGTERM, then SIGKILL after 10 seconds) and
its temp output files remain available for inspection.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito.tools.tasks.stop_task [args]
For AI function calling, use through the tool registry (tooling.tools_registry).
"""

import json
from typing import Any

from ...taskmanager import task_manager
from ...tooling import BaseTool
from ...tooling.decorator import tool


@tool(permissions="x")
class StopTask(BaseTool):
    """
    Tool for stopping a running task started with the StartTask tool.

    Args:
        task_id (str): The id of the task to stop (returned by StartTask).
    """

    def run(self, task_id: str) -> dict[str, Any]:
        """
        Stop a running task.

        Args:
            task_id (str): The id of the task to stop.

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool
                - 'task_id': the stopped task's id
                - 'pid': the pid of the task process
                - 'stopped': True when the stop was requested
                - 'returncode': the process return code (if already exited)
                - 'error': error message (only present if success is False)
        """
        try:
            self.report_start(f"Stopping task {task_id}", end="")

            info = task_manager.stop_task(task_id)

            self.report_result(f"task {task_id} stopped")
            return {"success": True, **info}

        except KeyError as e:
            self.report_error(str(e))
            return {"success": False, "error": str(e), "task_id": task_id}

        except Exception as e:
            self.report_error(f"Error: {e}")
            return {"success": False, "error": str(e), "task_id": task_id}


# ── CLI testing harness ────────────────────────────────────────────────────
def main():
    """Command line interface for testing the StopTask tool."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Stop a running task started with the StartTask tool"
    )
    parser.add_argument("task_id", help="The id of the task to stop")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Output in JSON format")
    args = parser.parse_args()

    result = StopTask().run(task_id=args.task_id)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            print(f"  ✅ task {result['task_id']} stopped")
        else:
            print(f"  ❌ Failed: {result.get('error', 'Unknown error')}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    exit(main())
