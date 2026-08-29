"""
/rwx command handler - sends a prompt to the LLM using the main conversation
history but restricted to the read, write and execute tools.

Unlike ``/ask`` (which starts a fresh, isolated chat history), ``/rwx`` sends
the prompt through the main conversation: the model sees the ongoing history
and the exchange is appended to it (rollback/cancel behaviour matches a normal
prompt). The only difference is that ``tools=`` is filtered down to the
read + write + execute tools -- the built-in tools whose ``@tool(permissions=...)``
declares only ``"r"``, ``"w"`` and/or ``"x"``, so the model gets the full
toolset (read, write and execute) in a single exchange.
"""

from ._tool_filters import get_tool_schemas_by_permission_letters
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
    return get_tool_schemas_by_permission_letters("rwx")


class RwxCmdHandler(CmdHandler):
    """Command handler for /rwx - asks the LLM with read + write + execute tools."""

    @property
    def name(self) -> str:
        return "/rwx"

    @property
    def description(self) -> str:
        return "Send a prompt restricted to read, write and execute tools"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /rwx command."""
        # Match '/rwx' exactly or '/rwx <question>' (not '/rwxs', etc.)
        if (
            user_input.lower() != self.name.lower()
            and not user_input.lower().startswith(self.name.lower() + " ")
        ):
            return False

        # Extract the question (everything after '/rwx ')
        question = user_input[len(self.name) :].strip()

        if not question:
            print("\nUsage: /rwx <your question>")
            print(
                "  Sends the prompt to the LLM using the main conversation"
                " history, but restricted to the read, write and execute tools."
            )
            print(
                "  The exchange stays in the main conversation history"
                " (rollback/cancel behave like a normal prompt).\n"
            )
            return True

        self._rwx(shell, question)
        return True

    def _rwx(self, shell, question: str) -> None:
        """Send the prompt with the main history, using read/write/execute tools."""
        turn_func = getattr(shell, "turn_func", None)
        if turn_func is None:
            print(
                "\nError: No prompt function available. Are you in an active session?\n"
            )
            return

        read_write_exec_schemas = get_read_write_exec_tool_schemas()
        print()  # blank line before the streamed response, like /ask
        # Reuse the shell's main-prompt path: same history, turns,
        # Responses state sync and cancel/rollback handling -- only the tool
        # set is restricted to the read, write and execute tools.
        shell._run_turn(question, tools=read_write_exec_schemas)


# Register this handler
_handler = RwxCmdHandler()
register_command(_handler)
