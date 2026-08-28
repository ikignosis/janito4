"""
/notools command handler - sends a prompt to the LLM using the main
conversation history but without offering any tools.

Unlike ``--no-tools`` (which applies to the whole session), ``/notools``
affects the send_prompt of the current message only: the exchange is sent
through the main conversation (the model sees the ongoing history and the
exchange is appended to it, rollback/cancel behaviour matches a normal
prompt), but ``tools=`` is set to ``[]`` so the model cannot call any tool
for this one turn. The next prompt goes back to the session's default tool
configuration.
"""

from .base import CmdHandler
from .registry import register_command


class NoToolsCmdHandler(CmdHandler):
    """Command handler for /notools - asks the LLM without any tools."""

    @property
    def name(self) -> str:
        return "/notools"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /notools command."""
        # Match '/notools' exactly or '/notools <question>' (not '/notoolsx')
        if (
            user_input.lower() != self.name.lower()
            and not user_input.lower().startswith(self.name.lower() + " ")
        ):
            return False

        # Extract the message (everything after '/notools ')
        message = user_input[len(self.name) :].strip()

        if not message:
            print("\nUsage: /notools <your message>")
            print(
                "  Sends the prompt to the LLM using the main conversation"
                " history, but without offering any tools (like --no-tools)."
            )
            print(
                "  Only this message is affected; the next prompt goes back"
                " to the session's default tools. The exchange stays in the"
                " main conversation history (rollback/cancel behave like a"
                " normal prompt).\n"
            )
            return True

        self._send_without_tools(shell, message)
        return True

    def _send_without_tools(self, shell, message: str) -> None:
        """Send the prompt with the main history and no tools for this turn."""
        send_prompt_func = getattr(shell, "send_prompt_func", None)
        if send_prompt_func is None:
            print(
                "\nError: No prompt function available. Are you in an active session?\n"
            )
            return

        print()  # blank line before the streamed response, like /ask
        # Reuse the shell's main-prompt path: same history, checkpoints,
        # Responses state sync and cancel/rollback handling. ``tools=[]``
        # suppresses every tool for this turn (the same thing ``--no-tools``
        # does for the whole session).
        shell._send_prompt(message, tools=[])


# Register this handler
_handler = NoToolsCmdHandler()
register_command(_handler)
