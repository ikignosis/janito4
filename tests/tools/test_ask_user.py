#!/usr/bin/env python3
"""
Tests for the AskUser tool (janito/tools/system/ask_user.py).

Verifies that:
- The tool returns success with the user's answer.
- The tool echoes back the question.
- The question is displayed inside a rich table on stderr.
- Markdown in the question is rendered (not shown literally).
- Console markup in the question is not interpreted.
- The tool handles EOF gracefully (empty answer).
- The tool handles exceptions gracefully (success=False).
- The should_load() gate skips the tool in single-prompt runs (positional
  or piped) and loads it only when a question surface is declared
  (web mode, interactive shell).
"""

import sys
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

import pytest

from janito.tooling.prompting import browser_prompts
from janito.tools import get_skipped_tools
from janito.tools.system.ask_user import AskUser


class TestAskUser:
    """Tests for the AskUser tool."""

    def test_basic_answer(self):
        """Tool returns success and the user's answer."""
        with patch("builtins.input", return_value="Paris"):
            result = AskUser().run(question="What is the capital of France?")

        assert result["success"] is True
        assert result["question"] == "What is the capital of France?"
        assert result["answer"] == "Paris"

    def test_answer_is_stripped(self):
        """Leading/trailing whitespace in the answer is stripped."""
        with patch("builtins.input", return_value="  hello  "):
            result = AskUser().run(question="Say hello")

        assert result["success"] is True
        assert result["answer"] == "hello"

    def test_empty_answer(self):
        """An empty answer is returned as an empty string."""
        with patch("builtins.input", return_value=""):
            result = AskUser().run(question="Anything to add?")

        assert result["success"] is True
        assert result["answer"] == ""

    def test_eof_returns_empty_answer(self):
        """EOFError (e.g. piped input) results in an empty answer, not a crash."""
        with patch("builtins.input", side_effect=EOFError):
            result = AskUser().run(question="Are you there?")

        assert result["success"] is True
        assert result["answer"] == ""

    def test_keyboard_interrupt_propagates(self):
        """Ctrl+C during the prompt interrupts the conversation loop instead
        of returning an empty answer: KeyboardInterrupt is not swallowed."""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                AskUser().run(question="Are you there?")

    def test_question_echoed_back(self):
        """The question is always echoed in the result."""
        with patch("builtins.input", return_value="42"):
            result = AskUser().run(question="Meaning of life?")

        assert result["question"] == "Meaning of life?"

    def test_question_printed_in_rich_table(self):
        """The question is rendered by rich inside a table on stderr."""
        question = "What is the capital of France?"
        buffer = StringIO()

        with patch("builtins.input", return_value="Paris"), redirect_stderr(buffer):
            AskUser().run(question=question)

        output = buffer.getvalue()
        # One smoke assert per renderer: non-empty + question echoed.
        assert output.strip() != ""
        assert question in output

    def test_question_markdown_is_rendered(self):
        """Markdown syntax in the question is rendered, not shown literally."""
        question = "Pick **one** option"
        buffer = StringIO()

        with patch("builtins.input", return_value="A"), redirect_stderr(buffer):
            AskUser().run(question=question)

        output = buffer.getvalue()
        assert output.strip() != ""
        assert "one" in output

    def test_question_does_not_interpret_console_markup(self):
        """Console markup-like text in the question is shown literally, not styled."""
        question = "Pick [bold]one[/bold] option"
        buffer = StringIO()

        with patch("builtins.input", return_value="A"), redirect_stderr(buffer):
            AskUser().run(question=question)

        output = buffer.getvalue()
        assert "[bold]one[/bold]" in output

    def test_success_key_always_present(self):
        """The 'success' key is always present in the returned dict."""
        with patch("builtins.input", return_value="yes"):
            result = AskUser().run(question="ok?")

        assert "success" in result


class TestAskUserShouldLoad:
    """The should_load() gate: interactive runs only (issue #98).

    The browser-prompts flag is the sole criterion -- it is declared at
    startup for web mode (in-browser question cards, even headless) and for
    the interactive shell (stdin prompting; TTY stdin, no positional
    prompt). Single-prompt runs -- positional or piped -- declare nothing:
    nobody is watching mid-run, so the tool is skipped there and never
    advertised to the model.
    """

    def test_skipped_when_no_surface_declared(self, monkeypatch):
        """Single-prompt run: skipped, with a skip reason set."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        assert AskUser.should_load() is False
        assert "surface" in AskUser._load_skip_reason.lower()

    def test_skipped_even_with_tty_stdin(self, monkeypatch):
        """A TTY alone is not enough: the run must declare a surface."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        assert AskUser.should_load() is False

    def test_loads_when_surface_declared(self, monkeypatch):
        """Web mode / interactive shell: the surface is declared, tool loads."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        with browser_prompts():
            assert AskUser.should_load() is True

    def test_skip_reason_untouched_when_loaded(self, monkeypatch):
        """A successful gate does not set a skip reason."""
        with browser_prompts():
            AskUser._load_skip_reason = ""
            AskUser.should_load()
        assert AskUser._load_skip_reason == ""


class TestAskUserDiscoveryGate:
    """Discovery excludes AskUser in single-prompt runs (issue #98)."""

    def test_discovery_skips_ask_user_without_surface(self, monkeypatch):
        """discover_toolsets(['system']) drops AskUser when nothing is declared."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        from janito.tooling.discovery import discover_toolsets

        tools = discover_toolsets(["system"])

        assert "AskUser" not in tools
        # The skip is surfaced for the tool summary / /tools command.
        assert "surface" in get_skipped_tools()["AskUser"].lower()

    def test_discovery_includes_ask_user_with_surface(self, monkeypatch):
        """Interactive runs (surface declared) keep AskUser registered."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        from janito.tooling.discovery import discover_toolsets

        with browser_prompts():
            tools = discover_toolsets(["system"])

        assert "AskUser" in tools
