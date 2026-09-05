""" /rx command handler - switch the session privileges to read + execute.

``/rx`` changes the privileges of the whole session: subsequent prompts
only offer the read and execute tools. See issue #141.
"""

from .base import CmdHandler
from .registry import register_command


def get_read_exec_tool_schemas() -> list[dict]:
    """Return the function-calling schemas of the read and execute tools.

    A tool is considered read/execute when its ``_tool_permissions`` is
    exactly ``"r"`` or ``"x"`` (the values set by ``@tool(permissions=...)``):
    read access or execute access and nothing else. Tools declaring no
    permissions (e.g. the skill tools), tools that can write
    (``"w"``/``"rw"``/combinations) are excluded. MCP tools carry no
    permission metadata here, so they are excluded too -- only the built-in
    read and execute tools are offered.
    """
    from ._tool_filters import get_tool_schemas_by_permissions

    return get_tool_schemas_by_permissions(["r", "x"])


class RxCmdHandler(CmdHandler):
    """Command handler for /rx - switch session privileges to read + execute."""

    @property
    def name(self) -> str:
        return "/rx"

    @property
    def description(self) -> str:
        return "Switch session privileges to read + execute"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /rx command."""
        # Match '/rx' exactly or '/rx ...' (extra text is ignored);
        # not '/rxs', etc.
        text = user_input.strip()
        if text.lower() != self.name.lower() and not text.lower().startswith(
            self.name.lower() + " "
        ):
            return False

        from janito import privileges as _privileges_mod
        from janito.privileges import parse_privileges

        _privileges_mod.running_privileges = parse_privileges("rx")
        print("\nPrivileges switched to read + execute (rx).\n")
        return True


# Register this handler
_handler = RxCmdHandler()
register_command(_handler)
