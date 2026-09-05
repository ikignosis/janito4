""" /write command handler - switch the session privileges to write-only.

``/write`` changes the privileges of the whole session: subsequent prompts
only offer the write-only tools. See issue #141.
"""

from .base import CmdHandler
from .registry import register_command


def get_write_only_tool_schemas() -> list[dict]:
    """Return the function-calling schemas of the write-only (``"w"``) tools.

    A tool is considered write-only when its ``_tool_permissions`` is exactly
    ``"w"`` (the value set by ``@tool(permissions="w")``): write access and
    nothing else. Tools declaring no permissions (e.g. the skill tools) and
    tools that can also read or execute (``"r"``/``"x"``/combinations) are
    excluded. MCP tools carry no permission metadata here, so they are
    excluded too -- only the built-in write-only tools are offered.
    """
    from ._tool_filters import get_tool_schemas_by_permission

    return get_tool_schemas_by_permission("w")


class WriteCmdHandler(CmdHandler):
    """Command handler for /write - switch session privileges to write-only."""

    @property
    def name(self) -> str:
        return "/write"

    @property
    def description(self) -> str:
        return "Switch session privileges to write-only"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /write command."""
        # Match '/write' exactly or '/write ...' (extra text is ignored);
        # not '/writes', etc.
        text = user_input.strip()
        if text.lower() != self.name.lower() and not text.lower().startswith(
            self.name.lower() + " "
        ):
            return False

        from janito import privileges as _privileges_mod
        from janito.privileges import parse_privileges

        _privileges_mod.running_privileges = parse_privileges("w")
        print("\nPrivileges switched to write-only (w).\n")
        return True


# Register this handler
_handler = WriteCmdHandler()
register_command(_handler)
