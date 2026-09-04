"""/push command - branch conversation onto a stack (issue #124)."""

from .base import CmdHandler
from .registry import register_command


class PushCmdHandler(CmdHandler):
    @property
    def name(self) -> str:
        return "/push"

    @property
    def description(self) -> str:
        return "Branch conversation to a new stack level"

    def handle(self, shell, user_input: str) -> bool:
        if user_input.lower().strip() == self.name:
            stack = shell.conversation_stack
            depth = stack.push(shell)
            print(f"Entering new chat thread [{depth}]")
            return True
        return False


_handler = PushCmdHandler()
register_command(_handler)
