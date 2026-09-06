""" /read command handler - switch the session privileges to read-only.

Unlike ``/ask`` (which starts a fresh, isolated chat history), ``/read``
changes the privileges of the whole session: subsequent prompts only offer
the read-only tools. See issue #141.
"""

from .base import CmdHandler
from .registry import register_command


def get_read_only_tool_schemas() -> list[dict]:
    """Return the function-calling schemas of the read-only (``"r"``) tools.

    A tool is considered read-only when its ``_tool_permissions`` is exactly
    ``"r"`` (the value set by ``@tool(permissions="r")``): read access and
    nothing else. Tools declaring no permissions (e.g. the skill tools) and
    tools that can write or execute (``"w"``/``"x"``/combinations) are
    excluded. MCP tools carry no permission metadata here, so they are
    excluded too -- only the built-in read-only tools are offered.
    """
    from ._tool_filters import get_tool_schemas_by_permission

    return get_tool_schemas_by_permission("r")


class ReadCmdHandler(CmdHandler):
    """Command handler for /read - switch session privileges to read-only."""

    @property
    def name(self) -> str:
        return "/read"

    @property
    def description(self) -> str:
        return "Switch session privileges to read-only"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /read command."""
        # Match '/read' exactly or '/read ...' (extra text is ignored);
        # not '/reads', etc.
        text = user_input.strip()
        if text.lower() != self.name.lower() and not text.lower().startswith(self.name.lower() + " "):
            return False

        from janito import privileges as _privileges_mod
        from janito.privileges import parse_privileges

        _privileges_mod.running_privileges = parse_privileges("r")
        print("\nPrivileges switched to read-only (r).\n")
        return True


# Register this handler
_handler = ReadCmdHandler()
register_command(_handler)
