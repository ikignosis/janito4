"""
Tests for the ``/use_stats`` shell command.

The command reads the per-turn usage rows from the SQLite ``accounting.db``
database (see :mod:`janito.tooling.accounting`), aggregates them by calendar
day and renders the last 10 days as a ``rich`` table, followed by a second
table breaking the same period down by day/provider/model. These tests point
the config dir at a temporary directory, record some usage across several
days, and verify the command's matching, table construction and rendered
output.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

import janito.config_dir as config_dir_mod
import janito.tooling.accounting as accounting
from janito.shell.cmds.use_stats import UseStatsCmdHandler


def _point_at(monkeypatch, tmp_path):
    """Point the global config dir at a temp directory and return it."""
    config_dir = tmp_path / "custom_janito"
    monkeypatch.setattr(config_dir_mod, "_config_dir", config_dir)
    return config_dir


def _record_day(day: str, n: int, *, cost: float | None = None) -> None:
    """Record ``n`` turns on the given ``YYYY-MM-DD`` day."""
    for i in range(n):
        accounting.record_turn(
            "openai",
            "gpt-5.6-luna",
            input_tokens=1000 + i,
            cached_tokens=100,
            output_tokens=500,
            cost=cost,
            timestamp=f"{day}T12:00:00+00:00",
        )


class _DummyShell:
    """Minimal stand-in for InteractiveShell (not used by this command)."""


def _render(handler, stats):
    """Render the stats table to a plain string via a rich Console."""
    from rich.console import Console

    console = Console(width=100, file=None)
    with console.capture() as capture:
        console.print(handler._build_table(stats))
    return capture.get()


def _render_model_table(handler, stats):
    """Render the per-model stats table to a plain string via a rich Console."""
    from rich.console import Console

    console = Console(width=120, file=None)
    with console.capture() as capture:
        console.print(handler._build_model_table(stats))
    return capture.get()


if pytest is not None:

    def test_command_matches_only_its_name():
        handler = UseStatsCmdHandler()
        shell = _DummyShell()
        assert handler.name == "/use_stats"
        assert handler.handle(shell, "/use_stats") is True
        assert handler.handle(shell, "/USE_STATS") is True
        assert handler.handle(shell, "  /use_stats  ") is True
        assert handler.handle(shell, "/tools") is False
        assert handler.handle(shell, "hello") is False

    def test_command_is_registered():
        from janito.shell.cmds import get_registered_commands

        names = [cmd.name for cmd in get_registered_commands()]
        assert "/use_stats" in names

    def test_table_built_from_recorded_usage(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)

        _record_day("2026-08-27", 2, cost=0.01)
        _record_day("2026-08-28", 3, cost=0.02)

        handler = UseStatsCmdHandler()
        stats = accounting.get_daily_stats()
        assert [row["day"] for row in stats] == ["2026-08-28", "2026-08-27"]

        table = handler._build_table(stats)
        # One row per day.
        assert table.row_count == len(stats)
        assert table.columns[0].header == "Day"
        # The per-day turn counters are incremental transaction numbers and
        # are deliberately not shown in the report (daily stats no longer
        # expose a ``turns`` key at all).
        headers = [col.header for col in table.columns]
        assert "Turns" not in headers

        output = _render(handler, stats)
        assert "2026-08-27" in output
        assert "2026-08-28" in output
        assert "Usage Statistics" in output
        # Newest day is rendered first.
        assert output.index("2026-08-28") < output.index("2026-08-27")

    def test_cached_percentage_after_cached_tokens():
        handler = UseStatsCmdHandler()
        # ``input_tokens`` already includes the cached tokens (the API
        # reports prompt/input tokens with cached counted inside them), so
        # 600 cached of 2,400 total input -> 25%.
        assert handler._format_cached_tokens(600, 2400) == "600 (25%)"
        # 300 cached of 1,200 total input -> 25%.
        assert handler._format_cached_tokens(300, 1200) == "300 (25%)"
        # 1 cached of 999 total input -> 0.1% -> rounds to 0%.
        assert handler._format_cached_tokens(1, 999) == "1 (0%)"
        # No input at all: plain count without a percentage.
        assert handler._format_cached_tokens(0, 0) == "0"
        assert handler._format_cached_tokens(0, 0) != "0 (0%)"
        # Cached without any reported input cannot be expressed as a
        # percentage either.
        assert handler._format_cached_tokens(10, 0) == "10"

    def test_cached_percentage_regression_almost_all_cached():
        handler = UseStatsCmdHandler()
        # Regression test for the reported bug: a day where ~99% of the
        # input was served from cache must not show ~50%.
        cached = 16_011_904
        total_input = 16_166_625
        assert handler._format_cached_tokens(cached, total_input) == "16,011,904 (99%)"

    def test_cached_percentage_rendered_in_table():
        handler = UseStatsCmdHandler()
        stats = [
            {
                "day": "2026-08-29",
                "input_tokens": 2400,
                "cached_tokens": 600,
                "output_tokens": 1600,
                "cost": 0.0024,
            },
            {
                "day": "2026-08-28",
                "input_tokens": 1200,
                "cached_tokens": 300,
                "output_tokens": 800,
                "cost": 0.0017,
            },
        ]
        output = _render(handler, stats)
        assert "600 (25%)" in output
        assert "300 (25%)" in output
        # Costs are rendered with the adaptive, magnitude-aware format the
        # end-of-turn ``Cost:`` summary uses (issue #67), not a fixed
        # 4-decimal dollar string: 0.0024$ -> 0.240\u00a2, 0.0017$ -> 0.170\u00a2.
        assert "0.240\u00a2" in output
        assert "0.170\u00a2" in output

    def test_cost_rendered_with_adaptive_format():
        handler = UseStatsCmdHandler()
        stats = [
            {
                "day": "2026-08-29",
                "input_tokens": 2400,
                "cached_tokens": 600,
                "output_tokens": 1600,
                "cost": 0.0024,  # 0.240\u00a2
            },
            {
                "day": "2026-08-28",
                "input_tokens": 1200,
                "cached_tokens": 300,
                "output_tokens": 800,
                "cost": 1.2,  # 1.2$
            },
            {
                "day": "2026-08-27",
                "input_tokens": 1200,
                "cached_tokens": 300,
                "output_tokens": 800,
                "cost": None,  # N/A
            },
        ]
        output = _render(handler, stats)
        # Sub-cent values grow their significant digits (3 decimals).
        assert "0.240\u00a2" in output
        # Dollar values show one decimal.
        assert "1.2$" in output
        # Unknown cost keeps the N/A fallback.
        assert "N/A" in output
        # The old fixed 4-decimal dollar format is no longer used.
        assert "$0.0024" not in output

    def test_model_table_cost_rendered_with_adaptive_format():
        handler = UseStatsCmdHandler()
        stats = [
            {
                "day": "2026-08-28",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "input_tokens": 300,
                "cached_tokens": 30,
                "output_tokens": 150,
                "cost": 0.0001,  # 0.010\u00a2
            },
            {
                "day": "2026-08-28",
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "input_tokens": 1000,
                "cached_tokens": 100,
                "output_tokens": 500,
                "cost": 0.01,  # 1.0\u00a2
            },
        ]
        output = _render_model_table(handler, stats)
        assert "0.010\u00a2" in output
        assert "1.0\u00a2" in output

    def test_model_table_built_from_recorded_usage(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)

        accounting.record_turn(
            "deepseek",
            "deepseek-v4-flash",
            input_tokens=300,
            cached_tokens=30,
            output_tokens=150,
            cost=0.003,
            timestamp="2026-08-28T12:00:00+00:00",
        )
        accounting.record_turn(
            "openai",
            "gpt-5.6-luna",
            input_tokens=1000,
            cached_tokens=100,
            output_tokens=500,
            cost=0.01,
            timestamp="2026-08-28T13:00:00+00:00",
        )
        accounting.record_turn(
            "openai",
            "gpt-5.6-luna",
            input_tokens=2000,
            cached_tokens=200,
            output_tokens=600,
            cost=0.02,
            timestamp="2026-08-29T12:00:00+00:00",
        )

        handler = UseStatsCmdHandler()
        stats = accounting.get_per_model_stats()
        assert [row["model"] for row in stats] == [
            "gpt-5.6-luna",
            "deepseek-v4-flash",
            "gpt-5.6-luna",
        ]

        table = handler._build_model_table(stats)
        assert table.row_count == len(stats)
        headers = [col.header for col in table.columns]
        assert headers == [
            "Day",
            "Provider",
            "Model",
            "Input tokens",
            "Cached tokens",
            "Output tokens",
            "Cost",
        ]

        output = _render_model_table(handler, stats)
        assert "Per Model Statistics" in output
        assert "deepseek-v4-flash" in output
        assert "gpt-5.6-luna" in output
        # input_tokens already includes the cached tokens: 30 cached of 300
        # total input -> 10%; 100 cached of 1,000 -> 10%.
        assert "30 (10%)" in output
        assert "100 (10%)" in output

    def test_model_table_unknown_provider_and_model(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)

        accounting.record_turn(
            None,
            None,
            input_tokens=5,
            cached_tokens=1,
            output_tokens=2,
            cost=None,
            timestamp="2026-08-28T12:00:00+00:00",
        )

        handler = UseStatsCmdHandler()
        stats = accounting.get_per_model_stats()
        output = _render_model_table(handler, stats)
        assert "unknown" in output
        assert "N/A" in output

    def test_print_stats_prints_table_and_db_path(monkeypatch, tmp_path, capsys):
        _point_at(monkeypatch, tmp_path)
        _record_day("2026-08-28", 1, cost=0.005)

        handler = UseStatsCmdHandler()
        handler._print_stats()
        out = capsys.readouterr().out
        assert "2026-08-28" in out
        # The caption names the accounting database file (rich may wrap the
        # full path across lines at narrow widths, so check the file name).
        assert "accounting.db" in out

    def test_print_stats_prints_both_tables(monkeypatch, tmp_path, capsys):
        _point_at(monkeypatch, tmp_path)
        _record_day("2026-08-28", 1, cost=0.005)

        handler = UseStatsCmdHandler()
        handler._print_stats()
        out = capsys.readouterr().out
        assert "Usage Statistics" in out
        assert "Per Model Statistics" in out
        # The daily table is printed first, then the per-model table.
        assert out.index("Usage Statistics") < out.index("Per Model Statistics")
        assert "gpt-5.6-luna" in out
        assert "openai" in out

    def test_empty_stats_prints_message(monkeypatch, tmp_path, capsys):
        _point_at(monkeypatch, tmp_path)

        handler = UseStatsCmdHandler()
        handler._print_stats()
        out = capsys.readouterr().out
        assert "No usage recorded yet" in out

else:  # pragma: no cover - fallback runner without pytest

    def _main():
        import tempfile

        class _MP:
            def setattr(self, obj, name, value):
                setattr(obj, name, value)

        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                with tempfile.TemporaryDirectory() as d:
                    if "tmp_path" in fn.__code__.co_varnames:
                        fn(_MP(), Path(d))
                    else:
                        fn()
                print(f"OK {name}")

    if __name__ == "__main__":
        _main()
