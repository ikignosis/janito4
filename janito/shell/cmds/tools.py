"""
/tools command handler - displays all loaded tools.
"""

from rich.console import Console
from rich.table import Table

from .base import CmdHandler
from .registry import register_command


def _load_builtin_tools():
    """Load the built-in tools offered in this session (privilege-filtered).

    Under ``-r``/``-w``/``-x`` the "Built-in Tools" table shows what the
    model may actually call in a normal prompt (the session tool set); the
    tools excluded by the session privileges are returned separately so
    ``/tools`` can list them as "Privilege-restricted" (issue #87).
    Tools disabled for the active provider/model (issue #144, e.g.
    ``WebSearch`` when the model offers native server-side search) are
    returned separately so ``/tools`` can list them as "disabled for this
    model" instead of mislabeling them as privilege-restricted.

    Returns:
        Tuple of ``(offered_tools, offered_schemas, restricted, disabled)``
        where ``restricted`` maps the names of the loaded-but-privilege-
        excluded tools to a human-readable reason and ``disabled`` maps
        the names of the model-disabled tools to ``"disabled for this
        model"``.
    """
    try:
        from janito import privileges as _privileges_mod
        from janito.tooling.tools_registry import (
            get_all_tool_permissions,
            get_all_tools,
            get_disabled_tool_names,
            get_session_tool_names,
            get_session_tool_schemas,
        )
        from janito.tools import privilege_restriction_reason

        all_tools = get_all_tools()
        session_names = get_session_tool_names()
        offered_tools = {
            name: tool for name, tool in all_tools.items() if name in session_names
        }
        offered_schemas = {
            s["function"]["name"]: s["function"] for s in get_session_tool_schemas()
        }
        restricted = {}
        disabled = {}
        disabled_names = get_disabled_tool_names()
        permissions = get_all_tool_permissions()
        for name in all_tools:
            if name in session_names:
                continue
            if name in disabled_names:
                disabled[name] = "disabled for this model"
                continue
            if _privileges_mod.running_privileges is not None:
                reason = privilege_restriction_reason(permissions.get(name, ""))
                restricted[name] = reason or "restricted by session privileges"
    except Exception as e:  # noqa: BLE001 - advisory display must never break
        offered_tools = {}
        offered_schemas = {}
        restricted = {}
        disabled = {}
        print(f"Warning: Could not load built-in tools: {e}")
    return offered_tools, offered_schemas, restricted, disabled


def _load_mcp_tools():
    """Load MCP tool schemas from the MCP manager."""
    mcp_tools = []
    try:
        from janito.mcp_manager import get_mcp_manager

        mcp_manager = get_mcp_manager()
        if mcp_manager:
            for schema in mcp_manager.get_all_tools():
                mcp_tools.append(schema["function"])
    except Exception as e:  # noqa: BLE001 - advisory display must never break
        print(f"Warning: Could not load MCP tools: {e}")
    return mcp_tools


def _truncate(description: str) -> str:
    """Truncate a tool description to 60 chars for display."""
    if len(description) > 60:
        return description[:57] + "..."
    return description


def _tools_table(title: str, rows: list[tuple[str, str]]) -> None:
    """Print a Tool/Description table (or a friendly message when empty)."""
    console = Console(markup=False)
    if not rows:
        print(f"{title}: (none)")
        return
    table = Table(
        title=title,
        title_style="bold",
        header_style="bold cyan",
    )
    table.add_column("Tool", style="green", no_wrap=True)
    table.add_column("Description", overflow="fold")
    for name, description in rows:
        table.add_row(name, description)
    console.print(table)


class ToolsCmdHandler(CmdHandler):
    """Command handler for /tools command."""

    @property
    def name(self) -> str:
        return "/tools"

    @property
    def description(self) -> str:
        return "List all loaded tools"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /tools command."""
        if user_input.strip().lower() == self.name.lower():
            self._print_tools()
            return True
        return False

    def _print_tools(self) -> None:
        """Print information about all available tools as rich tables."""
        # Warn when tool loading is disabled via --no-tools.
        from janito.tooling.tools_registry import tools_loading_enabled

        if not tools_loading_enabled():
            Console().print(
                "[bold yellow]Warning:[/bold yellow] tool loading is disabled "
                "(--no-tools). Only the skill tools are available "
                "(load_skill, read_skill_resource)."
            )

        # Get built-in tools from tools_registry (the session/offered set,
        # privilege-filtered under -r/-w/-x; restricted/model-disabled below).
        builtin_tools, builtin_schemas, restricted, disabled = _load_builtin_tools()

        # Get MCP tools from MCP manager
        mcp_tools = _load_mcp_tools()

        # Print sections
        builtin_rows = []
        if builtin_tools:
            for name in sorted(builtin_tools.keys()):
                schema = builtin_schemas.get(name, {})
                description = _truncate(schema.get("description", "No description"))
                builtin_rows.append((name, description))
        _tools_table("Built-in Tools", builtin_rows)

        # Tools loaded but excluded by the session privileges (-r/-w/-x).
        # They are available to the /read /write /rx /rw /rwx overrides
        # (issue #87), so list them separately instead of hiding them.
        if restricted:
            restricted_rows = sorted(restricted.items())
            _tools_table("Privilege-restricted", restricted_rows)

        # Tools disabled for the active provider/model (issue #144, e.g.
        # WebSearch when the model offers native server-side search).
        if disabled:
            disabled_rows = sorted(disabled.items())
            _tools_table("Model-disabled", disabled_rows)

        skipped_rows = []
        try:
            from janito.tools import get_skipped_tools

            skipped_tools = get_skipped_tools()
        except Exception:  # noqa: BLE001 - advisory display must never break
            skipped_tools = {}
        for name, reason in sorted(skipped_tools.items()):
            skipped_rows.append((name, reason))
        _tools_table("Skipped Tools", skipped_rows)

        mcp_rows = []
        for tool in sorted(mcp_tools, key=lambda x: x["name"]):
            name = tool["name"]
            description = tool.get("description", "No description")
            # Remove the [service] prefix from description for cleaner display
            if description.startswith("[") and "] " in description:
                description = description.split("] ", 1)[1]
            mcp_rows.append((name, _truncate(description)))
        _tools_table("MCP Tools", mcp_rows)

        # Summary
        total_tools = len(builtin_tools) + len(mcp_tools)
        summary = Table(
            title="Summary",
            title_style="bold",
            header_style="bold cyan",
            show_header=False,
            box=None,
            pad_edge=False,
        )
        summary.add_column("Key", style="green", no_wrap=True)
        summary.add_column("Value")
        summary.add_row(
            "Total",
            f"{total_tools} tools ({len(builtin_tools)} built-in, {len(mcp_tools)} MCP)",
        )
        Console(markup=False).print(summary)


# Register this handler
_handler = ToolsCmdHandler()
register_command(_handler)
