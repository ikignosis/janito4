#!/usr/bin/env python3
"""
GetTaskInfo Tool - Returns full detail about a single task (issue #117).

Unlike ListTasks (one summary row per task), this returns everything the
manager knows about one task -- including its description and the
stdout/stderr temp output filenames -- so the model can inspect a task
without waiting on it.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito.tools.tasks.get_task_info [args]
For AI function calling, use through the tool registry (tooling.tools_registry).
"""

import json
from typing import Any

from ...taskmanager import task_manager
from ...tooling import BaseTool
from ...tooling.decorator import tool


@tool(permissions="r")
class GetTaskInfo(BaseTool):
    """
    Tool for returning full detail about a single task.

    Args:
        task_id (int): The id of the task to inspect (returned by StartTask).
    """

    def run(self, task_id: int) -> dict[str, Any]:
        """
        Return all data known about a task.

        Args:
            task_id (int): The id of the task to inspect.

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool
                - 'task_id', 'summary', 'description', 'working_dir',
                  'privileges', 'pid', 'stdout_filename',
                  'stderr_filename', 'timeout', 'state', 'running',
                  'exit_reason', 'exit_code', 'returncode',
                  'duration_seconds', 'error'
                - 'error': error message (only present if success is False)
        """
        try:
            self.report_start(f"Getting info for task {task_id}", end="")

            info = task_manager.get_task_info(task_id)

            self.report_result(f"task {task_id} info retrieved")
            return {"success": True, **info}

        except KeyError as e:
            self.report_error(str(e))
            return {"success": False, "error": str(e), "task_id": task_id}

        except (ValueError, OSError, RuntimeError) as e:
            self.report_error(f"Error: {e}")
            return {"success": False, "error": str(e), "task_id": task_id}


# ── CLI testing harness ────────────────────────────────────────────────────
def main():
    """Command line interface for testing the GetTaskInfo tool."""
    import argparse

    parser = argparse.ArgumentParser(description="Show full detail about a single task")
    parser.add_argument("task_id", type=int, help="The id of the task to inspect")
    parser.add_argument("--json", "-j", action="store_true", help="Output in JSON format")
    args = parser.parse_args()

    result = GetTaskInfo().run(task_id=args.task_id)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            state = "running" if result["running"] else result["state"]
            print(f"  task {result['task_id']} [{state}] {result.get('summary') or ''}")
            print(f"  pid: {result['pid']}")
            print(f"  working_dir: {result['working_dir']}")
            print(f"  stdout: {result['stdout_filename']}")
            print(f"  stderr: {result['stderr_filename']}")
        else:
            print(f"  ❌ Failed: {result.get('error', 'Unknown error')}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    exit(main())
