""" /rw command handler - switch the session privileges to read + write.

``/rw`` changes the privileges of the whole session: subsequent prompts
only offer the read and write tools. See issue #141.
"""

from .base import CmdHandler
from .registry import register_command


def get_read_write_tool_schemas() -> list[dict]:
    """Return the function-calling schemas of the read and write tools.

    A tool is considered read/write when its ``_tool_permissions`` is a
    non-empty subset of ``"rw"`` (the values set by ``@tool(permissions=...)``):
    ``"r"``, ``"w"`` and ``"rw"`` -- read access, write access or both and
    nothing else. Tools declaring no permissions (e.g. the skill tools),
    tools that can execute (``"x"``/combinations) are excluded. MCP tools
    carry no permission metadata here, so they are excluded too -- only the
    built-in read and write tools are offered.
    """
    from ._tool_filters import get_tool_schemas_by_permission_letters

    return get_tool_schemas_by_permission_letters("rw")


class RwCmdHandler(CmdHandler):
    """Command handler for /rw - switch session privileges to read + write."""

    @property
    def name(self) -> str:
        return "/rw"

    @property
    def description(self) -> str:
        return "Switch session privileges to read + write"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /rw command."""
        # Match '/rw' exactly or '/rw ...' (extra text is ignored);
        # not '/rws', etc.
        text = user_input.strip()
        if text.lower() != self.name.lower() and not text.lower().startswith(self.name.lower() + " "):
            return False

        from janito import privileges as _privileges_mod
        from janito.privileges import parse_privileges

        _privileges_mod.running_privileges = parse_privileges("rw")
        print("\nPrivileges switched to read + write (rw).\n")
        return True


# Register this handler
_handler = RwCmdHandler()
register_command(_handler)
