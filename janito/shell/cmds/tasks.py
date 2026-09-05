"""/tasks command handler - lists all tasks in a rich table."""

from janito.taskmanager import task_manager

from .base import CmdHandler
from .registry import register_command


def _format_duration(task: dict) -> str:
    duration = task.get("duration_seconds")
    if duration is None:
        return "-"
    return f"{duration:.1f}s"


def _exit_display(task: dict) -> str:
    exit_code = task.get("exit_code")
    code = str(exit_code) if exit_code is not None else "-"
    error = task.get("error") or ""
    if len(error) > 60:
        error = error[:57] + "..."
    return f"{code} / {error}" if error else code


def _print_tasks() -> None:
    from rich.console import Console
    from rich.table import Table

    tasks = task_manager.list_tasks()
    if not tasks:
        Console(markup=False).print("  (no tasks)")
        return
    table = Table(title="Tasks", header_style="bold cyan")
    table.add_column("Task ID", no_wrap=True)
    table.add_column("state", no_wrap=True)
    table.add_column("duration", no_wrap=True)
    table.add_column("exit_code/reason", no_wrap=True)
    table.add_column("summary")
    for task in tasks:
        table.add_row(
            str(task["task_id"]),
            "running" if task.get("running") else task.get("state", ""),
            _format_duration(task),
            _exit_display(task),
            task.get("summary") or "-",
        )
    Console(markup=False).print(table)


class TasksCmdHandler(CmdHandler):
    """Command handler for /tasks command."""

    @property
    def name(self) -> str:
        return "/tasks"

    @property
    def description(self) -> str:
        return "List all tasks"

    def handle(self, shell, user_input: str) -> bool:
        if user_input.strip().lower() == self.name.lower():
            _print_tasks()
            return True
        return False


_handler = TasksCmdHandler()
register_command(_handler)
