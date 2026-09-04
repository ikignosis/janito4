"""
Tests for the shell ``/help`` command.

The command prints every registered command with its description in a rich
table, and splits the prompt tool modes (read-only / read + execute /
write-only / no tools) into their own table.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito.shell.cmds.help import HelpCmdHandler


class _DummyShell:
    """Minimal stand-in for InteractiveShell (not used by this command)."""


if pytest is not None:

    def test_command_matches_only_its_name():
        handler = HelpCmdHandler()
        shell = _DummyShell()
        assert handler.name == "/help"
        assert handler.handle(shell, "/help") is True
        assert handler.handle(shell, "/HELP") is True
        assert handler.handle(shell, "  /help  ") is True
        assert handler.handle(shell, "/tools") is False
        assert handler.handle(shell, "hello") is False

    def test_command_is_registered():
        from janito.shell.cmds import get_registered_commands

        names = [cmd.name for cmd in get_registered_commands()]
        assert "/help" in names

    def test_every_command_has_a_description():
        """/help shows the description after each command, so every registered
        command must expose one."""
        from janito.shell.cmds import get_registered_commands

        for cmd in get_registered_commands():
            assert cmd.description.strip(), f"{cmd.name} has no description"

    def test_help_output_shows_descriptions(capfd):
        handler = HelpCmdHandler()
        handler.handle(_DummyShell(), "/help")
        out = capfd.readouterr().out
        assert "Available Commands" in out
        assert "/tools" in out
        assert "List all loaded tools" in out
        assert "/exit" in out
        assert "Exit the chat session" in out

    def test_help_output_splits_tool_modes(capfd):
        """The prompt tool modes are split by their tool type."""
        handler = HelpCmdHandler()
        handler.handle(_DummyShell(), "/help")
        out = capfd.readouterr().out
        assert "Prompt tool modes" in out
        assert "read-only" in out
        assert "read + execute" in out
        assert "write-only" in out
        assert "/notools <message>" in out
        # The non-tool features stay in their own section.
        assert "Additional features" in out
        assert "!<command>" in out

else:  # pragma: no cover - fallback runner without pytest

    def _main():
        class _MP:
            def setattr(self, obj, name, value):
                setattr(obj, name, value)

        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn(_MP())
                print(f"OK {name}")

    if __name__ == "__main__":
        _main()
