"""
/history command handler - displays the contents of the conversation history.
"""

from __future__ import annotations

from ..conversation import effective_rows
from .base import CmdHandler
from .registry import register_command


class HistoryCmdHandler(CmdHandler):
    """Command handler for /history command."""

    @property
    def name(self) -> str:
        return "/history"

    @property
    def description(self) -> str:
        return "Show the conversation history"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /history command."""
        if user_input.lower() == self.name.lower():
            self._print_history(shell)
            return True
        return False

    def _history_rows(self, shell) -> list[tuple[str, str]]:
        """Return ``(role, content)`` rows from the effective history source.

        Delegates to :func:`janito.shell.conversation.effective_rows`, the
        single place that knows where the conversation lives per API mode:

        - Completions / Anthropic / DashScope: ``shell.messages_history`` holds
          the whole conversation (system + user + assistant).
        - Stateless Responses (e.g. DeepSeek): the full conversation is kept
          client-side in ``shell.conversation_items`` as Responses input items
          (with the system prompt folded in on the first turn); ``messages_history``
          then only ever holds the system prompt, so the items win.
        - Server-side Responses (e.g. OpenAI): the history is stored on the
          server; the shell keeps a display-only mirror of the completed
          turns in ``shell.mirrored_history`` (Responses input items) purely
          so /history can render the conversation, plus any pending
          (Enter-cancelled) messages in ``conversation_items`` that are not
          yet part of a completed server response.
        """
        return effective_rows(shell)

    def _turn_markers(self, shell, num_rows: int) -> dict[int, list[int]]:
        """Map turn-start values to their ordinal numbers per display position.

        ``shell.history_turns`` holds the number of rows /history
        would render each time a user prompt was about to be sent (see
        ``InteractiveShell._history_row_count``), so each recorded value
        directly names the displayed row its turn started at.  Returns
        ``{row_index: [ordinals]}`` for each position that needs a marker
        (out-of-range values are ignored); each turn keeps its
        own ordinal (1-based position in the list).
        """
        turns = getattr(shell, "history_turns", None) or []
        markers: dict[int, list[int]] = {}
        for ordinal, c in enumerate(turns, start=1):
            if 0 <= c <= num_rows:
                markers.setdefault(c, []).append(ordinal)
        return dict(sorted(markers.items()))

    def _print_history(self, shell) -> None:
        """Print the contents of the message history as a rich table."""
        from rich.console import Console
        from rich.table import Table

        rows = self._history_rows(shell)
        if not rows:
            Console(markup=False).print("(empty)")
            return

        markers = self._turn_markers(shell, len(rows))

        table = Table(
            title="Message History",
            title_style="bold",
            header_style="bold cyan",
        )
        table.add_column("#", justify="right", style="dim", no_wrap=True)
        table.add_column("Role", style="green", no_wrap=True)
        table.add_column("Content", overflow="fold")

        for i, (role, content) in enumerate(rows):
            # Show one marker line per turn, before the item it
            # precedes, numbered by its order in the turn list.
            for ordinal in markers.get(i, []):
                table.add_row("", f"\u25c9 turn {ordinal}", "", style="bold yellow")

            # Truncate long content for display
            if len(content) > 200:
                content_preview = content[:200] + "..."
            else:
                content_preview = content

            # Replace newlines for cleaner display
            content_preview = content_preview.replace("\n", "\\n")

            table.add_row(str(i), role, content_preview)

        Console(markup=False).print(table)


# Register this handler
_handler = HistoryCmdHandler()
register_command(_handler)
