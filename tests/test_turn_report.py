"""
Tests for the end-of-turn report.

The CLI no longer prints the token-usage summary inside the per-client
``_finalize`` helpers.  Instead ``Client.run_turn`` folds every round's usage
into a :class:`~janito.agent.usage.TokenStats` carried out on a
:class:`~janito.openai_client.client_support.TurnUsage` out-param, and
delivers it to the injected observer's ``on_turn_complete`` when the turn
finishes (the CLI's ``RichTurnObserver`` renders it via
:func:`~janito.openai_client.client_support.display_turn_usage` and records
the overall-use accounting row).  These tests pin that contract.
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
        last_input=60,
        last_output=40,
        last_cached=5,
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
        from janito.config_store import set_config_value, unset_config_value

        _register(monkeypatch, "ReadFile", "r")
        used_files.reset_used_files()
        used_files.record_used_file("ReadFile", {"filepath": "/a.py"})
        set_config_value("used-files", True)
        try:
            u = TurnUsage(stats=_token_stats(), message_count=1)
            text = self._render(u)
            assert text.index("Used files") < text.index("===")
            assert "1 read : /a.py" in text
        finally:
            used_files.reset_used_files()
            unset_config_value("used-files")

    def test_used_files_report_suppressed_when_disabled(self, monkeypatch):
        """The used-files report is hidden by default (issue #74)."""
        from janito.config_store import unset_config_value

        _register(monkeypatch, "ReadFile", "r")
        used_files.reset_used_files()
        used_files.record_used_file("ReadFile", {"filepath": "/a.py"})
        unset_config_value("used-files")
        try:
            u = TurnUsage(stats=_token_stats(), message_count=1)
            text = self._render(u)
            assert "Used files" not in text
            assert "1 read : /a.py" not in text
            # The token-usage summary is still printed.
            assert "===" in text
        finally:
            used_files.reset_used_files()

    def test_used_files_report_printed_when_enabled(self, monkeypatch):
        """With ``used-files=True`` the report is printed (issue #74)."""
        from janito.config_store import set_config_value, unset_config_value

        _register(monkeypatch, "ReadFile", "r")
        used_files.reset_used_files()
        used_files.record_used_file("ReadFile", {"filepath": "/a.py"})
        set_config_value("used-files", True)
        try:
            u = TurnUsage(stats=_token_stats(), message_count=1)
            text = self._render(u)
            assert "Used files" in text
            assert "1 read : /a.py" in text
        finally:
            used_files.reset_used_files()
            unset_config_value("used-files")

    def test_no_usage_prints_nothing(self):
        used_files.reset_used_files()
        assert self._render(TurnUsage()) == ""


class TestRunTurnDeliversTurnReport:
    """``Client.run_turn`` delivers the end-of-turn report to the injected
    observer's ``on_turn_complete`` when the turn finishes (the CLI wrapper
    that used to do it is gone)."""

    def _client(self, monkeypatch, observer):
        from conftest import make_config

        from janito.openai_client.completions_api import CompletionsClient

        def fake_run(func, client, call_kwargs, tools_schemas):
            return (
                "final answer",
                None,
                {},
                SimpleNamespace(
                    prompt_tokens=60,
                    completion_tokens=40,
                    total_tokens=100,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=5),
                ),
                {"id": "chatcmpl-1"},
            )

        monkeypatch.setattr(
            "janito.openai_client.client_support._load_mcp",
            lambda use_mcp: (None, []),
        )
        return CompletionsClient(
            make_config(
                model="gpt-4",
                use_mcp=False,
                stream_runner=fake_run,
                observer=observer,
            )
        )

    def test_run_turn_calls_on_turn_complete_with_populated_usage_out(
        self, monkeypatch
    ):
        recorded = []

        class Obs:
            def on_reasoning(self, content):
                pass

            def on_message(self, content):
                pass

            def on_turn_complete(self, usage_out):
                recorded.append(usage_out)

        client = self._client(monkeypatch, Obs())
        usage_out = TurnUsage()
        result = client.run_turn("hi", tools=[], usage_out=usage_out)
        assert result == "final answer"
        # on_turn_complete was invoked exactly once, with the populated
        # out-param: usage folded from the stream round, provider/model and
        # the display metadata set by the finalizer.
        assert len(recorded) == 1
        u = recorded[0]
        assert u is usage_out
        assert u.stats is not None
        assert u.stats.turn_input == 60
        assert u.stats.turn_output == 40
        assert u.provider == "openai"
        assert u.model == "gpt-4"
        assert u.message_count == 2  # user + assistant

    def test_run_turn_without_usage_out_skips_report(self, monkeypatch):
        """Without the usage_out out-param no report is delivered (the
        headless/CLI callers opt in through the out-param)."""
        called = []

        class Obs:
            def on_reasoning(self, content):
                pass

            def on_message(self, content):
                pass

            def on_turn_complete(self, usage_out):
                called.append(usage_out)

        client = self._client(monkeypatch, Obs())
        client.run_turn("hi", tools=[])
        assert called == []

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

    def test_rich_observer_on_turn_complete_records_accounting(self):
        """The observer's on_turn_complete also writes the overall-use
        accounting row (the end-of-turn bookkeeping lives in the observer,
        mirroring the web loop's own accounting)."""
        import janito.tooling.accounting as accounting
        from janito.openai_client.client_support import RichTurnObserver

        buf = StringIO()
        observer = RichTurnObserver(
            console=Console(file=buf, width=120, force_terminal=False)
        )
        u = TurnUsage(
            stats=_token_stats(),
            provider="deepseek",
            model="deepseek-v4-flash",
            show_cached=True,
        )
        observer.on_turn_complete(u)
        records = accounting.get_records()
        assert len(records) == 1
        row = records[0]
        # The turn-wide cumulative counters (tool-call rounds included).
        assert row["provider"] == "deepseek"
        assert row["model"] == "deepseek-v4-flash"
        assert row["input_tokens"] == 180
        assert row["cached_tokens"] == 10
        assert row["output_tokens"] == 120

    def test_rich_observer_on_turn_complete_without_usage_records_nothing(self):
        """No usage reported -> no accounting row and no rendering."""
        import janito.tooling.accounting as accounting
        from janito.openai_client.client_support import RichTurnObserver

        buf = StringIO()
        observer = RichTurnObserver(
            console=Console(file=buf, width=120, force_terminal=False)
        )
        observer.on_turn_complete(TurnUsage())
        assert accounting.get_records() == []
        assert buf.getvalue() == ""
