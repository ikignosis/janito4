#!/usr/bin/env python3
"""
StartTask Tool - Starts a new task that runs in parallel with other tasks.

The description is piped to a fresh ``janito`` sub-process (single-prompt
stdin mode) whose stdout/stderr go to temp files; the task keeps running in
the background.  Use StopTask to stop it and WaitForTask to wait for the
tasks to finish.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito.tools.tasks.start_task [args]
For AI function calling, use through the tool registry (tooling.tools_registry).
"""

import json
from typing import Any

from ...taskmanager import task_manager
from ...tooling import BaseTool
from ...tooling.decorator import tool
from ...tooling.turn_privileges import current_turn_privileges


@tool(permissions="x")
class StartTask(BaseTool):
    """
    Tool for starting a new task that runs in parallel with other tasks.

    Call this tool when there are multiple tasks that can run in parallel;
    the description should explain what needs to be done.  Each task runs as
    a separate janito process; use StopTask to stop it and WaitForTask to
    wait for the tasks to finish.

    Args:
        description (str): What needs to be done in this task (sent to the
            task process's stdin as a single prompt).
        working_dir (str, optional): Working directory for the task process
            (default: the current directory).
        privileges (str, optional): Privileges for the task process, a
            combination of 'r' / 'w' / 'x' (e.g. "rwx").  Default: the
            privileges of the current turn -- the child mirrors the running
            task (the session's -r/-w/-x flags plus any /read /write /rx
            /rw /rwx override), so a task spawned from a /rwx turn inherits
            full privileges instead of silently starting read-only.
    """

    def run(
        self,
        description: str,
        working_dir: str | None = None,
        privileges: str | None = None,
    ) -> dict[str, Any]:
        """
        Start a new parallel task.

        Args:
            description (str): What needs to be done in this task.
            working_dir (Optional[str]): Working directory for the task
                (default: current directory).
            privileges (Optional[str]): Privileges for the task process
                (default: the privileges of the current turn, mirroring the
                running task).

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool
                - 'task_id': the id of the started task
                - 'pid': the pid of the task process
                - 'stdout_filename': temp file with the task's stdout
                - 'stderr_filename': temp file with the task's stderr
                - 'description': what needs to be done
                - 'working_dir': the resolved task working directory
                - 'privileges': the privileges the task was started with
                - 'error': error message (only present if success is False)
        """
        try:
            self.report_start(f"Starting task: {description}", end="")

            # Mirror the running task's current (turn) privileges by default:
            # the child starts with the same -r/-w/-x flags the current turn
            # runs under (the session flags plus any /read /write /rx /rw
            # /rwx override), so a task spawned from a /rwx turn inherits
            # full privileges instead of silently starting read-only (issue
            # #94).  An explicit `privileges` argument still wins.
            if privileges is None:
                privileges = current_turn_privileges()

            info = task_manager.start_task(
                description=description,
                working_dir=working_dir,
                privileges=privileges,
            )

            self.report_result(
                f"task {info['task_id']} started (pid {info['pid']})"
            )
            return {
                "success": True,
                **info,
                "description": description,
                "working_dir": info["working_dir"],
                "privileges": privileges,
            }

        except Exception as e:
            self.report_error(f"Error: {e}")
            return {
                "success": False,
                "error": str(e),
                "description": description,
                "working_dir": working_dir,
                "privileges": privileges,
            }


# ── CLI testing harness ────────────────────────────────────────────────────
def main():
    """Command line interface for testing the StartTask tool."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Start a new task that runs in parallel with other tasks"
    )
    parser.add_argument("description", help="What needs to be done in this task")
    parser.add_argument(
        "--working-dir",
        default=None,
        help="Working directory for the task (default: current directory)",
    )
    parser.add_argument(
        "--privileges",
        default=None,
        help=(
            "Privileges for the task process (e.g. rwx). Default: mirror "
            "the current turn's privileges (session -r/-w/-x plus any "
            "/read /write /rx /rw /rwx override)"
        ),
    )
    parser.add_argument("--json", "-j", action="store_true",
                        help="Output in JSON format")
    args = parser.parse_args()

    result = StartTask().run(
        description=args.description,
        working_dir=args.working_dir,
        privileges=args.privileges,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            print(f"  ✅ task {result['task_id']} started (pid {result['pid']})")
            print(f"  stdout: {result['stdout_filename']}")
            print(f"  stderr: {result['stderr_filename']}")
        else:
            print(f"  ❌ Failed: {result.get('error', 'Unknown error')}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    exit(main())
