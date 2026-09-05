"""/pop command - return to previous stack level (issue #124)."""

from ..conversation import effective_rows
from .base import CmdHandler
from .registry import register_command


def _resolve_observer(shell):
    """Best-effort lookup of the session's turn observer."""
    observer = getattr(shell, "observer", None)
    if observer is not None:
        return observer
    turn_func = getattr(shell, "turn_func", None)
    for cell in getattr(turn_func, "__closure__", None) or ():
        candidate = cell.cell_contents
        observer = getattr(candidate, "observer", None)
        if observer is not None:
            return observer
        if hasattr(candidate, "on_message"):
            return candidate
    from janito.ui.observer import RichTurnObserver

    return RichTurnObserver()


def _last_assistant_message(shell) -> str | None:
    for role, content in reversed(effective_rows(shell)):
        if role == "assistant" and content:
            return content
    return None


class PopCmdHandler(CmdHandler):
    @property
    def name(self) -> str:
        return "/pop"

    @property
    def description(self) -> str:
        return "Return to the previous stack level"

    def handle(self, shell, user_input: str) -> bool:
        if user_input.lower().strip() == self.name:
            try:
                depth = shell.conversation_stack.pop(shell)
            except IndexError:
                print("Nothing to pop. Stack is empty.")
                return True
            label = f"thread [{depth}]" if depth else "main thread"
            print(f"Returned to {label}")
            last = _last_assistant_message(shell)
            if last:
                print("Last Message:")
                _resolve_observer(shell).on_message(last)
            return True
        return False


_handler = PopCmdHandler()
register_command(_handler)
