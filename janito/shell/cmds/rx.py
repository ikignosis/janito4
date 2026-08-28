"""
/rx command handler - sends a prompt to the LLM using the main conversation
history but restricted to the read and execute tools.

Unlike ``/ask`` (which starts a fresh, isolated chat history), ``/rx`` sends
the prompt through the main conversation: the model sees the ongoing history
and the exchange is appended to it (rollback/cancel behaviour matches a normal
prompt). The only difference is that ``tools=`` is filtered down to the
read + execute tools -- the built-in tools whose ``@tool(permissions=...)``
declares ``"r"`` or ``"x"`` and nothing else, so the model can read, search,
fetch and run commands but cannot write or modify anything.
"""

from ._tool_filters import get_tool_schemas_by_permissions
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
    return get_tool_schemas_by_permissions(["r", "x"])


class RxCmdHandler(CmdHandler):
    """Command handler for /rx - asks the LLM with read + execute tools."""

    @property
    def name(self) -> str:
        return "/rx"

    @property
    def description(self) -> str:
        return "Send a prompt restricted to read and execute tools"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /rx command."""
        # Match '/rx' exactly or '/rx <question>' (not '/rxs', etc.)
        if (
            user_input.lower() != self.name.lower()
            and not user_input.lower().startswith(self.name.lower() + " ")
        ):
            return False

        # Extract the question (everything after '/rx ')
        question = user_input[len(self.name) :].strip()

        if not question:
            print("\nUsage: /rx <your question>")
            print(
                "  Sends the prompt to the LLM using the main conversation"
                " history, but restricted to the read and execute tools."
            )
            print(
                "  The exchange stays in the main conversation history"
                " (rollback/cancel behave like a normal prompt).\n"
            )
            return True

        self._rx(shell, question)
        return True

    def _rx(self, shell, question: str) -> None:
        """Send the prompt with the main history, using only read/execute tools."""
        send_prompt_func = getattr(shell, "send_prompt_func", None)
        if send_prompt_func is None:
            print(
                "\nError: No prompt function available. Are you in an active session?\n"
            )
            return

        read_exec_schemas = get_read_exec_tool_schemas()
        print()  # blank line before the streamed response, like /ask
        # Reuse the shell's main-prompt path: same history, turns,
        # Responses state sync and cancel/rollback handling -- only the tool
        # set is restricted to the read and execute tools.
        shell._send_prompt(question, tools=read_exec_schemas)


# Register this handler
_handler = RxCmdHandler()
register_command(_handler)
