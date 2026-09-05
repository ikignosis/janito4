"""
Tests for the /notools shell command.

``/notools <message>`` sends the prompt to the LLM using the **main**
conversation history (unlike ``/ask``, which starts a fresh history) but with
``tools=[]`` -- i.e. no tools offered at all, the per-message equivalent of
``--no-tools``. These tests verify the command is registered, dispatches
correctly, and routes the prompt through the shell's main-prompt path with an
empty tool list that affects only the current message.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito.shell import InteractiveShell
from janito.shell.cmds.notools import NoToolsCmdHandler

# A fake tool schema, used to check the command never forwards any tools.
READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ReadFile",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _shell():
    """Build a fresh shell for testing."""
    return InteractiveShell(model="test-model", no_history=True)


# ---------------------------------------------------------------------------
# Registry / dispatch
# ---------------------------------------------------------------------------


def test_notools_command_is_registered():
    from tests.conftest import assert_command_registered

    assert_command_registered("/notools")


def test_handler_name():
    assert NoToolsCmdHandler().name == "/notools"


def test_handle_dispatches_only_notools_command(monkeypatch):
    handler = NoToolsCmdHandler()
    shell = _shell()
    shell.turn_func = lambda **kw: None
    sent = {}

    def fake_run_turn(prompt, tools=None):
        sent["prompt"] = prompt
        sent["tools"] = tools

    monkeypatch.setattr(shell, "_run_turn", fake_run_turn)

    assert handler.handle(shell, "/notools what is this?") is True
    assert sent["prompt"] == "what is this?"
    assert sent["tools"] == []

    # Case-insensitive command, message preserved.
    assert handler.handle(shell, "/NOTOOLS tell me more") is True
    assert sent["prompt"] == "tell me more"

    # Bare '/notools' matches but shows usage (no send).
    assert handler.handle(shell, "/notools") is True

    # Non-matching inputs are not handled.
    assert handler.handle(shell, "/notoolsx") is False
    assert handler.handle(shell, "/tools") is False
    assert handler.handle(shell, "hello") is False


def test_notools_without_message_shows_usage(monkeypatch, capfd):
    handler = NoToolsCmdHandler()
    shell = _shell()
    called = {"n": 0}

    def fake_run_turn(prompt, tools=None):
        called["n"] += 1

    monkeypatch.setattr(shell, "_run_turn", fake_run_turn)

    assert handler.handle(shell, "/notools") is True
    out = capfd.readouterr().out
    assert out.strip() != ""
    assert called["n"] == 0


def test_notools_requires_turn_func(monkeypatch, capfd):
    """Without turn_func an error is printed instead of crashing."""
    handler = NoToolsCmdHandler()
    shell = _shell()
    # The shell has no turn_func until run() sets it.
    assert handler.handle(shell, "/notools hello") is True
    out = capfd.readouterr().out
    assert "error" in out.lower() or out.strip() != ""


# ---------------------------------------------------------------------------
# End-to-end routing through the main conversation
# ---------------------------------------------------------------------------


def test_notools_routes_through_main_history_without_tools(monkeypatch):
    """/notools goes through _run_turn, which uses the main history and
    offers no tools for this turn only."""
    handler = NoToolsCmdHandler()
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.verbose = False
    shell.thinking = False
    shell.no_tools = False

    kwargs = {}

    def capture(prompt, **kw):
        kwargs["prompt"] = prompt
        kwargs.update(kw)
        return "assistant"

    shell.turn_func = capture

    handler.handle(shell, "/notools summarize the project")

    # The main history is used (mutated in place by the Completions client)
    # and no tools are offered for this message.
    assert kwargs["prompt"] == "summarize the project"
    assert kwargs["previous_messages"] is shell.messages_history
    assert kwargs["instructions"] == "sys"
    assert kwargs["tools"] == []


def test_notools_overrides_session_tools_for_one_message(monkeypatch):
    """The tool suppression applies to the current message only: the next
    regular prompt goes back to the session's default tools."""
    handler = NoToolsCmdHandler()
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.verbose = False
    shell.thinking = False
    shell.no_tools = False

    sent = []

    def capture(prompt, tools=None, **kw):
        sent.append(tools)
        return "assistant"

    shell.turn_func = capture

    handler.handle(shell, "/notools do this without tools")
    # A regular prompt afterwards uses the session default (tools=None),
    # even though the shell itself has tools enabled.
    shell._run_turn("now do it normally")

    assert sent[0] == []
    assert sent[1] is None


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-v"]))
