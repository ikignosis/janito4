"""
Tests for the shell ``/help`` command.

Behavior-first: matching/registration go through shared conftest helpers;
rendering is one smoke test driven by the command registry (not hardcoded
copy). See docs/development/testing.md.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito.shell.cmds.help import HelpCmdHandler
from tests.conftest import assert_command_matching, assert_command_registered


class _DummyShell:
    """Minimal stand-in for InteractiveShell (not used by this command)."""


if pytest is not None:

    def test_command_matches_only_its_name():
        assert_command_matching(HelpCmdHandler(), "/help")

    def test_command_is_registered():
        assert_command_registered("/help")

    def test_every_command_has_a_description():
        """Every registered command must expose a description for /help."""
        from janito.shell.cmds import get_registered_commands

        for cmd in get_registered_commands():
            assert cmd.description.strip(), f"{cmd.name} has no description"

    def test_help_output_lists_registered_commands(capfd):
        """Smoke test: one header + every registered name rendered.

        Names come from the registry so adding a command needs no edit here.
        """
        handler = HelpCmdHandler()
        assert handler.handle(_DummyShell(), "/help") is True
        out = capfd.readouterr().out
        assert out.strip(), "help printed nothing"
        assert "Available Commands" in out
        from janito.shell.cmds import get_registered_commands

        for cmd in get_registered_commands():
            assert cmd.name in out

    def test_help_output_lists_sections(capfd):
        """Tool-mode / feature sections render (headers only, not copy)."""
        handler = HelpCmdHandler()
        handler.handle(_DummyShell(), "/help")
        out = capfd.readouterr().out
        for section in ("Session privilege switches", "Additional features"):
            assert section in out

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
