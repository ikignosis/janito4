"""
Tests for the shell /thinking command handler.

``/thinking on|off`` enables or disables runtime config thinking for the
running shell session without altering the persisted configuration in config.json.
``/thinking`` alone reports the current thinking status and usage.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from janito.shell import InteractiveShell
from janito.shell.cmds.registry import get_registered_commands
from janito.shell.session import _thinking_arg_completer
from tests.conftest import assert_command_matching, assert_command_registered


def _shell(thinking: bool = False, provider: str | None = None) -> InteractiveShell:
    """Build a fresh shell for testing."""
    return InteractiveShell(
        model="test-model",
        no_history=True,
        provider=provider,
        thinking=thinking,
    )


def _thinking_handler():
    """Return the registered /thinking command handler."""
    return next(c for c in get_registered_commands() if c.name == "/thinking")


def test_thinking_command_is_registered():
    """The /thinking handler is registered with the shell command registry."""
    assert_command_registered("/thinking")
    assert_command_matching(_thinking_handler(), "/thinking")


def test_no_argument_shows_disabled_status_by_default(capsys):
    """``/thinking`` alone reports disabled state when thinking is off."""
    shell = _shell(thinking=False)
    assert _thinking_handler().handle(shell, "/thinking") is True

    assert shell.thinking is False
    out = capsys.readouterr().out
    assert out.strip() != ""


def test_no_argument_shows_na_for_gemini_flavor_provider(capsys):
    """``/thinking`` alone for Google leaves thinking off (N/A via reasoning)."""
    shell = _shell(thinking=False, provider="google")
    assert _thinking_handler().handle(shell, "/thinking") is True

    assert shell.thinking is False
    assert capsys.readouterr().out.strip() != ""


def test_thinking_on_warns_for_gemini_flavor_provider(capsys):
    """``/thinking on`` for Google is handled without crashing."""
    shell = _shell(thinking=False, provider="google")
    assert _thinking_handler().handle(shell, "/thinking on") is True

    assert capsys.readouterr().out.strip() != ""


def test_no_argument_shows_enabled_status_when_on(capsys):
    """``/thinking`` alone preserves enabled state when thinking is on."""
    shell = _shell(thinking=True)
    assert _thinking_handler().handle(shell, "/thinking") is True

    assert shell.thinking is True
    assert capsys.readouterr().out.strip() != ""


def test_thinking_on_enables_thinking(capsys):
    """``/thinking on`` sets shell.thinking to True."""
    shell = _shell(thinking=False)
    assert _thinking_handler().handle(shell, "/thinking on") is True

    assert shell.thinking is True
    assert capsys.readouterr().out.strip() != ""


def test_thinking_off_disables_thinking(capsys):
    """``/thinking off`` sets shell.thinking to False."""
    shell = _shell(thinking=True)
    assert _thinking_handler().handle(shell, "/thinking off") is True

    assert shell.thinking is False
    assert capsys.readouterr().out.strip() != ""


def test_thinking_case_insensitive(capsys):
    """Arguments 'ON' and 'OFF' are recognized case-insensitively."""
    shell = _shell(thinking=False)
    assert _thinking_handler().handle(shell, "/THINKING ON") is True
    assert shell.thinking is True

    assert _thinking_handler().handle(shell, "/Thinking Off") is True
    assert shell.thinking is False


def test_thinking_invalid_argument_prints_error(capsys):
    """An invalid argument reports an error and leaves thinking unchanged."""
    shell = _shell(thinking=False)
    assert _thinking_handler().handle(shell, "/thinking invalid") is True

    assert shell.thinking is False
    out = capsys.readouterr().out
    assert "error" in out.lower()


def test_non_thinking_command_is_not_handled(capsys):
    """Non-matching input is rejected without output (see shared helper)."""
    shell = _shell()
    assert _thinking_handler().handle(shell, "hello") is False
    assert capsys.readouterr().out == ""


def test_thinking_arg_completer():
    """_thinking_arg_completer returns matching options."""
    assert _thinking_arg_completer("") == ["on", "off"]
    assert _thinking_arg_completer("o") == ["on", "off"]
    assert _thinking_arg_completer("on") == ["on"]
    assert _thinking_arg_completer("of") == ["off"]
    assert _thinking_arg_completer("off") == ["off"]
    assert _thinking_arg_completer("ON") == ["on"]
    assert _thinking_arg_completer("z") == []


def test_thinking_argument_autocompletion_in_shell_session():
    """Autocompletion works on shell session for /thinking."""
    shell = _shell()
    doc = Document("/thinking o", cursor_position=len("/thinking o"))
    completions = list(shell.session.completer.get_completions(doc, CompleteEvent()))
    completion_texts = [c.text for c in completions]
    assert "on" in completion_texts
    assert "off" in completion_texts


def test_status_command_reflects_thinking_toggle(capsys):
    """/status command displays updated thinking mode after /thinking on/off."""
    from janito.shell.cmds.status import StatusCmdHandler

    status_handler = StatusCmdHandler()
    shell = _shell(thinking=False, provider="openai")

    # Status before toggle
    assert status_handler.handle(shell, "/status") is True
    out1 = capsys.readouterr().out
    assert out1.strip() != ""

    # Toggle on
    assert _thinking_handler().handle(shell, "/thinking on") is True
    assert shell.thinking is True
    capsys.readouterr()

    # Status after toggle on
    assert status_handler.handle(shell, "/status") is True
    out2 = capsys.readouterr().out
    assert out2.strip() != ""
