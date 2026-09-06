""" /rwx command handler - switch the session privileges to full access.

``/rwx`` changes the privileges of the whole session: subsequent prompts
offer the full read + write + execute toolset. See issue #141.
"""

from .base import CmdHandler
from .registry import register_command


def get_read_write_exec_tool_schemas() -> list[dict]:
    """Return the function-calling schemas of the read, write and execute tools.

    A tool is considered read/write/execute when its ``_tool_permissions`` is
    a non-empty subset of ``"rwx"`` (the values set by ``@tool(permissions=...)``):
    ``"r"``, ``"w"``, ``"x"`` and any combination of those letters -- i.e.
    every built-in tool that declares a permission. Tools declaring no
    permissions (e.g. the skill tools) and MCP tools (which carry no
    permission metadata here) are excluded, matching the other
    permission-restricted commands -- only the built-in read, write and
    execute tools are offered.
    """
    from ._tool_filters import get_tool_schemas_by_permission_letters

    return get_tool_schemas_by_permission_letters("rwx")


class RwxCmdHandler(CmdHandler):
    """Command handler for /rwx - switch session privileges to full access."""

    @property
    def name(self) -> str:
        return "/rwx"

    @property
    def description(self) -> str:
        return "Switch session privileges to full access"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /rwx command."""
        # Match '/rwx' exactly or '/rwx ...' (extra text is ignored);
        # not '/rwxs', etc.
        text = user_input.strip()
        if text.lower() != self.name.lower() and not text.lower().startswith(self.name.lower() + " "):
            return False

        from janito import privileges as _privileges_mod
        from janito.privileges import parse_privileges

        _privileges_mod.running_privileges = parse_privileges("rwx")
        print("\nPrivileges switched to full access (rwx).\n")
        return True


# Register this handler
_handler = RwxCmdHandler()
register_command(_handler)
