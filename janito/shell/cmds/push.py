"""/push command - branch conversation onto a stack (issue #124)."""

from .base import CmdHandler
from .registry import register_command


class PushCmdHandler(CmdHandler):
    @property
    def name(self) -> str:
        return "/push"

    @property
    def description(self) -> str:
        return "Branch conversation to a new stack level, optionally starting a turn with a message"

    def handle(self, shell, user_input: str) -> bool:
        stripped = user_input.strip()
        lowered = stripped.lower()
        if (
            lowered == self.name
            or lowered.startswith(self.name + " ")
            or lowered.startswith(self.name + "\t")
        ):
            msg = stripped[len(self.name) :].strip()
            stack = shell.conversation_stack
            depth = stack.push(shell)
            print(f"Entering new chat thread [{depth}]")
            if msg:
                shell._run_turn(msg)
            return True
        return False


_handler = PushCmdHandler()
register_command(_handler)
