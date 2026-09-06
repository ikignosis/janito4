"""
/help command handler - displays all available commands.
"""

from rich.console import Console
from rich.table import Table

from .base import CmdHandler
from .registry import get_registered_commands, register_command


class HelpCmdHandler(CmdHandler):
    """Command handler for /help command."""

    @property
    def name(self) -> str:
        return "/help"

    @property
    def description(self) -> str:
        return "Show this help (all commands, tool modes and shortcuts)"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /help command."""
        if user_input.strip().lower() == self.name.lower():
            self._print_help()
            return True
        return False

    def _print_help(self) -> None:
        """Print help information for all available commands as rich tables."""
        commands = sorted(get_registered_commands(), key=lambda cmd: cmd.name)

        console = Console(markup=False)

        table = Table(
            title="Available Commands",
            title_style="bold",
            header_style="bold cyan",
            show_header=False,
            box=None,
            pad_edge=False,
        )
        table.add_column("Command", style="green", no_wrap=True)
        table.add_column("Description", overflow="fold")
        for cmd in commands:
            table.add_row(cmd.name, cmd.description)
        console.print(table)

        # The session privilege switches: bare commands that change the
        # privileges of the whole session (all subsequent prompts), plus the
        # per-message /notools mode. Split into its own table so the types
        # are easy to compare.
        tool_modes = Table(
            title="Session privilege switches",
            title_style="bold",
            header_style="bold cyan",
            show_header=False,
            box=None,
            pad_edge=False,
        )
        tool_modes.add_column("Command", style="green", no_wrap=True)
        tool_modes.add_column("Tool type", style="cyan", no_wrap=True)
        tool_modes.add_column("Description", overflow="fold")
        tool_modes.add_row(
            "/read",
            "read-only",
            "Switch the session privileges to read-only",
        )
        tool_modes.add_row(
            "/rw",
            "read + write",
            "Switch the session privileges to read + write",
        )
        tool_modes.add_row(
            "/rwx",
            "read + write + execute",
            "Switch the session privileges to full access",
        )
        tool_modes.add_row(
            "/rx",
            "read + execute",
            "Switch the session privileges to read + execute",
        )
        tool_modes.add_row(
            "/write",
            "write-only",
            "Switch the session privileges to write-only",
        )
        tool_modes.add_row(
            "/notools <message>",
            "none",
            "Send the prompt using the main history but without any tools " "(this message only)",
        )
        console.print(tool_modes)

        features = Table(
            title="Additional features",
            title_style="bold",
            header_style="bold cyan",
            show_header=False,
            box=None,
            pad_edge=False,
        )
        features.add_column("Key", style="green", no_wrap=True)
        features.add_column("Description", overflow="fold")
        features.add_row("!<command>", "Execute a shell command directly")
        console.print(features)

        shortcuts = Table(
            title="Keyboard shortcuts",
            title_style="bold",
            header_style="bold cyan",
            show_header=False,
            box=None,
            pad_edge=False,
        )
        shortcuts.add_column("Key", style="green", no_wrap=True)
        shortcuts.add_column("Description", overflow="fold")
        shortcuts.add_row("[F2]", "Clear conversation")
        shortcuts.add_row("[F12]", "Do It (continue existing plan)")
        console.print(shortcuts)

        mcp = Table(
            title="MCP Services",
            title_style="bold",
            header_style="bold cyan",
            show_header=False,
            box=None,
            pad_edge=False,
        )
        mcp.add_column("Command", style="green", no_wrap=True)
        mcp.add_column("Description", overflow="fold")
        mcp.add_row("/mcp add <name> stdio <cmd>", "Add stdio service")
        mcp.add_row("/mcp add <name> http <url>", "Add HTTP service")
        mcp.add_row("/mcp list", "List MCP services")
        mcp.add_row("/mcp remove <name>", "Remove an MCP service")
        console.print(mcp)


# Register this handler
_handler = HelpCmdHandler()
register_command(_handler)
