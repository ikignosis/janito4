"""
/rw command handler - sends a prompt to the LLM using the main conversation
history but restricted to the read and write tools.

Unlike ``/ask`` (which starts a fresh, isolated chat history), ``/rw`` sends
the prompt through the main conversation: the model sees the ongoing history
and the exchange is appended to it (rollback/cancel behaviour matches a normal
prompt). The only difference is that ``tools=`` is filtered down to the
read + write tools -- the built-in tools whose ``@tool(permissions=...)``
declares only ``"r"`` and/or ``"w"`` (e.g. ``"r"``, ``"w"`` or ``"rw"``), so
the model can read, search, fetch and modify files but cannot execute
anything.
"""

from ._tool_filters import get_tool_schemas_by_permission_letters
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
    return get_tool_schemas_by_permission_letters("rw")


class RwCmdHandler(CmdHandler):
    """Command handler for /rw - asks the LLM with read + write tools."""

    @property
    def name(self) -> str:
        return "/rw"

    @property
    def description(self) -> str:
        return "Send a prompt restricted to read and write tools"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /rw command."""
        # Match '/rw' exactly or '/rw <question>' (not '/rws', etc.)
        if (
            user_input.lower() != self.name.lower()
            and not user_input.lower().startswith(self.name.lower() + " ")
        ):
            return False

        # Extract the question (everything after '/rw ')
        question = user_input[len(self.name) :].strip()

        if not question:
            print("\nUsage: /rw <your question>")
            print(
                "  Sends the prompt to the LLM using the main conversation"
                " history, but restricted to the read and write tools."
            )
            print(
                "  The exchange stays in the main conversation history"
                " (rollback/cancel behave like a normal prompt).\n"
            )
            return True

        self._rw(shell, question)
        return True

    def _rw(self, shell, question: str) -> None:
        """Send the prompt with the main history, using only read/write tools."""
        turn_func = getattr(shell, "turn_func", None)
        if turn_func is None:
            print(
                "\nError: No prompt function available. Are you in an active session?\n"
            )
            return

        read_write_schemas = get_read_write_tool_schemas()
        print()  # blank line before the streamed response, like /ask
        # Reuse the shell's main-prompt path: same history, turns,
        # Responses state sync and cancel/rollback handling -- only the tool
        # set is restricted to the read and write tools.
        shell._run_turn(question, tools=read_write_schemas)


# Register this handler
_handler = RwCmdHandler()
register_command(_handler)
