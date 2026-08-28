"""
/rewind command handler - Rewind conversation to a previous message.
"""

from .base import CmdHandler
from .registry import register_command


def _pop_turn(turns) -> None:
    """Drop the most recent history turn, if any (used by the
    Responses-mode rewind branches so /history markers stay in sync)."""
    if turns:
        turns.pop()


class RewindCmdHandler(CmdHandler):
    """Command handler for /rewind command."""

    @property
    def name(self) -> str:
        return "/rewind"

    @property
    def description(self) -> str:
        return "Undo the most recent turn"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /rewind command."""
        if user_input.lower().strip() == self.name:
            self._do_rewind(shell)
            return True
        return False

    def _do_rewind(self, shell) -> None:
        """Undo the most recent turn, stepping back one turn at a time.

        Each successful turn records the number of rows /history would
        render before that turn in ``shell.history_turns``; /rewind undoes
        the most recent exchange by truncating the history back to the last
        recorded start and dropping it, so a second /rewind steps back one
        further turn.  The Responses-mode branches below (which live outside
        messages_history) drop the same recorded start so /history markers
        stay in sync.
        """
        turns = getattr(shell, "history_turns", None)
        if turns and len(shell.messages_history) > turns[-1]:
            start = turns[-1]
            removed = len(shell.messages_history) - start
            del shell.messages_history[start:]
            turns.pop()
            print(
                f"Rewound {removed} message(s). History now has {len(shell.messages_history)} message(s)."
            )
            return

        # Responses API mode: the conversation lives outside
        # messages_history. Stateless endpoints (e.g. DeepSeek) keep the
        # history client-side in conversation_items; server-side endpoints
        # (e.g. OpenAI) keep it behind previous_response_id.
        conversation_items = getattr(shell, "conversation_items", None)
        if conversation_items is not None:
            conversation_turn = getattr(shell, "conversation_turn", 0)
            if conversation_turn < len(conversation_items):
                del conversation_items[conversation_turn:]
                _pop_turn(turns)
                print(
                    "Rewound: conversation history truncated "
                    "(stateless Responses API / pending items)."
                )
                return

        # Server-side Responses (e.g. OpenAI): undo the last completed turn
        # by chaining the next turn (previous_response_id) from the response
        # that preceded it, instead of resetting the whole server-side
        # conversation. The chain of completed response ids was recorded by
        # the shell on each successful turn.
        response_chain = getattr(shell, "response_chain", None)
        if response_chain is not None:
            response_turn = getattr(shell, "response_turn", 0)
            if response_turn < len(response_chain):
                del response_chain[response_turn:]
                shell.previous_response_id = (
                    response_chain[-1] if response_chain else None
                )
                # Also truncate the /history display mirror of completed
                # server-side turns back to its recorded start, so /history no
                # longer shows the rewound exchange (the real conversation
                # lives on the server; this mirror is display-only).
                mirrored = getattr(shell, "mirrored_history", None)
                if mirrored:
                    mirrored_turn = getattr(shell, "mirrored_turn", 0)
                    del mirrored[mirrored_turn:]
                _pop_turn(turns)
                if shell.previous_response_id:
                    print(
                        "Rewound: server-side conversation rewound to "
                        "the previous response (Responses API)."
                    )
                else:
                    print(
                        "Rewound: server-side conversation reset to a "
                        "fresh conversation (Responses API)."
                    )
                return
            if response_chain and getattr(shell, "previous_response_id", None):
                # Already at the recorded start: nothing to undo (mirrors the
                # Completions-mode message for a second consecutive /rewind).
                print("Nothing to rewind. History is already at the last turn.")
                return
            # No chain tracked (e.g. a server-side conversation started
            # before the chain was kept, or a manually seeded shell state):
            # fall back to resetting the server conversation.
            if getattr(shell, "previous_response_id", None) is not None:
                shell.previous_response_id = None
                _pop_turn(turns)
                print("Rewound: server-side conversation reset (Responses API).")
                return

        print("Nothing to rewind. History is already at the last turn.")


# Register this handler
_handler = RewindCmdHandler()
register_command(_handler)
