"""
Tests for the post-call turn report.

The CLI no longer prints the token-usage summary inside the per-client
``_finalize`` helpers.  Instead ``Client.send`` folds every round's usage
into a :class:`~janito.agent.usage.TokenStats` carried out on a
:class:`~janito.openai_client.client_support.TurnUsage` out-param, and the
CLI renders it once ``send_prompt`` returns via
:func:`~janito.openai_client.client_support.display_turn_usage` -- wired up
by :func:`~janito.openai_client.client_support.wrap_send_prompt_with_turn_report`
in ``janito/cli/chat.py``.  These tests pin that contract.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from io import StringIO  # noqa: E402

import pytest  # noqa: E402
from rich.console import Console  # noqa: E402

import janito.config_dir as config_dir_mod  # noqa: E402
import janito.tooling.tools_registry as tools_registry  # noqa: E402
import janito.tooling.used_files as used_files  # noqa: E402
from janito.agent.usage import TokenStats, normalize_usage  # noqa: E402
from janito.openai_client.client_support import (  # noqa: E402
    TurnUsage,
    display_turn_usage,
    wrap_send_prompt_with_turn_report,
)


@pytest.fixture(autouse=True)
def _isolate_config_dir(tmp_path, monkeypatch):
    """Point the config dir at a temp dir so accounting.db writes stay local."""
    monkeypatch.setattr(config_dir_mod, "_config_dir", tmp_path / "janito")


def _register(monkeypatch, name, permissions):
    """Register a fake tool so the used-files tracker knows its permission."""
    monkeypatch.setattr(tools_registry, "_tools_initialized", True)
    fake = lambda **kwargs: {"success": True}  # noqa: E731
    fake._tool_permissions = permissions
    monkeypatch.setitem(tools_registry.AVAILABLE_TOOLS, name, fake)


def _token_stats(**kw):
    defaults = dict(
        total=100,
        input=60,
        output=40,
        cached=5,
        turn_input=180,
        turn_cached=10,
        turn_output=120,
    )
    defaults.update(kw)
    return TokenStats(**defaults)


class TestNormalizeUsageWithTokenStats:
    def test_passes_token_stats_through(self):
        stats = _token_stats()
        assert normalize_usage(stats) == {
            "total": 100,
            "input": 60,
            "output": 40,
            "cached": 5,
        }

    def test_raw_usage_still_normalizes(self):
        raw = SimpleNamespace(
            prompt_tokens=60,
            completion_tokens=40,
            total_tokens=100,
            prompt_tokens_details=SimpleNamespace(cached_tokens=5),
        )
        assert normalize_usage(raw) == {
            "total": 100,
            "input": 60,
            "output": 40,
            "cached": 5,
        }


class TestDisplayTurnUsage:
    def _render(self, usage_out):
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)
        display_turn_usage(usage_out, console=console)
        return buf.getvalue()

    def test_renders_usage_line_from_populated_turn_usage(self):
        u = TurnUsage(
            stats=_token_stats(),
            provider="deepseek",
            model="deepseek-v4-flash",
            max_input_tokens=65536,
            max_output_tokens=8192,
            message_count=3,
            label="Messages",
            show_cached=True,
        )
        text = self._render(u)
        assert "Total: 100" in text
        assert "In: 60/65.5k" in text
        assert "Out: 40/8.2k" in text
        assert "Cached: 5" in text
        # The conversation turn number is no longer part of the summary
        # (it lives in the shell's pre-prompt rule instead).
        assert "Turn" not in text

    def test_omits_label_count(self):
        u = TurnUsage(
            stats=_token_stats(),
            message_count=4,
            label="Responses",
            show_cached=False,
        )
        text = self._render(u)
        assert "Responses:" not in text
        assert "Messages:" not in text

    def test_show_cached_false_omits_cached_part(self):
        u = TurnUsage(stats=_token_stats(), message_count=1, show_cached=False)
        text = self._render(u)
        assert "Cached:" not in text

    def test_prints_used_files_before_usage_line(self, monkeypatch):
        _register(monkeypatch, "ReadFile", "r")
        used_files.reset_used_files()
        used_files.record_used_file("ReadFile", {"filepath": "/a.py"})
        try:
            u = TurnUsage(stats=_token_stats(), message_count=1)
            text = self._render(u)
            assert text.index("Used files") < text.index("===")
            assert "1 read : /a.py" in text
        finally:
            used_files.reset_used_files()

    def test_no_usage_prints_nothing(self):
        used_files.reset_used_files()
        assert self._render(TurnUsage()) == ""


class TestWrapSendPromptWithTurnReport:
    def _make_send(self, holder):
        def fake_send(
            prompt,
            verbose=False,
            previous_messages=None,
            previous_response_id=None,
            previous_items=None,
            instructions=None,
            tools=None,
            usage_out=None,
        ):
            usage_out.stats = _token_stats()
            usage_out.provider = "deepseek"
            usage_out.model = "deepseek-v4-flash"
            usage_out.message_count = 2
            usage_out.label = "Messages"
            usage_out.show_cached = True
            holder["usage_out"] = usage_out
            holder["kwargs"] = dict(
                verbose=verbose,
                previous_messages=previous_messages,
                previous_response_id=previous_response_id,
                previous_items=previous_items,
                instructions=instructions,
                tools=tools,
            )
            return "final answer"

        return fake_send

    def _make_observer(self, recorded):
        class FakeObserver:
            def on_turn_complete(self, usage_out):
                recorded.append(usage_out)

        return FakeObserver()

    def test_wrapper_calls_api_then_displays_report(self):
        holder = {}
        recorded = []
        wrapped = wrap_send_prompt_with_turn_report(
            self._make_send(holder), observer=self._make_observer(recorded)
        )
        result = wrapped("hi")
        assert result == "final answer"
        assert recorded == [holder["usage_out"]]

    def test_wrapper_can_suppress_report(self):
        holder = {}
        recorded = []
        wrapped = wrap_send_prompt_with_turn_report(
            self._make_send(holder), observer=self._make_observer(recorded)
        )
        result = wrapped("hi", display_turn_report=False)
        assert result == "final answer"
        assert recorded == []

    def test_wrapper_without_observer_renders_nothing(self):
        holder = {}
        wrapped = wrap_send_prompt_with_turn_report(self._make_send(holder))
        result = wrapped("hi")
        assert result == "final answer"

    def test_wrapper_forwards_api_kwargs(self):
        holder = {}
        recorded = []
        wrapped = wrap_send_prompt_with_turn_report(
            self._make_send(holder), observer=self._make_observer(recorded)
        )
        wrapped(
            "hello",
            verbose=True,
            previous_messages=[{"role": "user", "content": "hello"}],
            tools=[],
        )
        kw = holder["kwargs"]
        assert kw["verbose"] is True
        assert kw["previous_messages"] == [{"role": "user", "content": "hello"}]
        assert kw["tools"] == []
        # The usage out-param reaches the observer's on_turn_complete...
        assert recorded == [holder["usage_out"]]

    def test_rich_observer_on_turn_complete_renders_report(self):
        """The CLI's RichTurnObserver renders the report through
        display_turn_usage (byte-for-byte the historical output)."""
        from janito.openai_client.client_support import RichTurnObserver

        buf = StringIO()
        observer = RichTurnObserver(
            console=Console(file=buf, width=120, force_terminal=False)
        )
        u = TurnUsage(
            stats=_token_stats(),
            provider="deepseek",
            model="deepseek-v4-flash",
            max_input_tokens=65536,
            max_output_tokens=8192,
            message_count=3,
            label="Messages",
            show_cached=True,
        )
        observer.on_turn_complete(u)
        text = buf.getvalue()
        assert "Total: 100" in text
        assert "In: 60/65.5k" in text
