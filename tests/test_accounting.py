"""
Tests for the SQLite-backed overall-use accounting.

``janito.tooling.accounting`` appends one row per completed LLM turn to an
``accounting.db`` SQLite database inside the Janito config directory (issue
#72). These tests point the config dir at a temporary directory and verify
the database is created with the expected schema, values round-trip, the
turn ordinal auto-increments and the cost accessor returns numeric dollars.
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import janito.config_dir as config_dir_mod
import janito.tooling.accounting as accounting
from janito.provider_accessors import get_provider_cost_value

#: All columns of the ``accounting`` table (including the rowid alias).
EXPECTED_COLUMNS = {
    "id",
    "cwd",
    "turn_count",
    "timestamp",
    "provider",
    "model",
    "input_tokens",
    "cached_tokens",
    "output_tokens",
    "cost",
}


def _point_at(monkeypatch, tmp_path):
    """Point the global config dir at a temp directory and return it."""
    config_dir = tmp_path / "custom_janito"
    monkeypatch.setattr(config_dir_mod, "_config_dir", config_dir)
    # Reset the module singleton's per-process turn ordinal so each test
    # starts from 1 again (deterministic assertions).
    accounting._store._turn_counter = 0
    return config_dir


if pytest is not None:

    def test_db_path_is_in_config_dir(monkeypatch, tmp_path):
        config_dir = _point_at(monkeypatch, tmp_path)
        assert accounting.get_db_path() == config_dir / "accounting.db"

    def test_record_creates_db_with_expected_schema(monkeypatch, tmp_path):
        config_dir = _point_at(monkeypatch, tmp_path)
        accounting.record_turn(
            "openai",
            "gpt-5.6-luna",
            input_tokens=1000,
            cached_tokens=200,
            output_tokens=300,
        )

        db_path = config_dir / "accounting.db"
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        try:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(accounting)").fetchall()
            }
            assert cols == EXPECTED_COLUMNS
        finally:
            conn.close()

    def test_record_round_trips_values(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        accounting.record_turn(
            "deepseek",
            "deepseek-v4-flash",
            input_tokens=180,
            cached_tokens=10,
            output_tokens=120,
            cost=0.0088,
        )

        records = accounting.get_records()
        assert len(records) == 1
        row = records[0]
        assert row["cwd"] == str(Path.cwd())
        assert row["turn_count"] == 1
        assert row["timestamp"]
        assert row["provider"] == "deepseek"
        assert row["model"] == "deepseek-v4-flash"
        assert row["input_tokens"] == 180
        assert row["cached_tokens"] == 10
        assert row["output_tokens"] == 120
        assert row["cost"] == pytest.approx(0.0088)

    def test_turn_count_auto_increments_per_process(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        accounting.record_turn(
            "openai", "m", input_tokens=1, cached_tokens=0, output_tokens=1
        )
        accounting.record_turn(
            "openai", "m", input_tokens=1, cached_tokens=0, output_tokens=1
        )
        accounting.record_turn(
            "openai", "m", input_tokens=1, cached_tokens=0, output_tokens=1
        )

        counts = [r["turn_count"] for r in accounting.get_records()]
        assert counts == [3, 2, 1]

    def test_explicit_turn_count_is_stored(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        accounting.record_turn(
            "openai",
            "m",
            input_tokens=1,
            cached_tokens=0,
            output_tokens=1,
            turn_count=42,
        )
        assert accounting.get_records()[0]["turn_count"] == 42

    def test_none_counters_stored_as_null(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        accounting.record_turn(
            "anthropic",
            "claude-x",
            input_tokens=None,
            cached_tokens=None,
            output_tokens=None,
            cost=None,
        )
        row = accounting.get_records()[0]
        assert row["input_tokens"] is None
        assert row["cached_tokens"] is None
        assert row["output_tokens"] is None
        assert row["cost"] is None

    def test_get_records_newest_first_with_limit(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        for i in range(5):
            accounting.record_turn(
                "openai", "m", input_tokens=i, cached_tokens=0, output_tokens=i
            )
        limited = accounting.get_records(limit=2)
        assert [r["input_tokens"] for r in limited] == [4, 3]

    def test_empty_cwd_is_ignored(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        accounting.record_turn(
            "openai", "m", input_tokens=1, cached_tokens=0, output_tokens=1, cwd=""
        )
        assert accounting.get_records() == []

    def test_prune_removes_only_old_entries(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        now = datetime.now(timezone.utc)
        accounting.record_turn(
            "openai",
            "old",
            input_tokens=1,
            cached_tokens=0,
            output_tokens=1,
            timestamp=(now - timedelta(days=30)).isoformat(),
        )
        accounting.record_turn(
            "openai",
            "recent",
            input_tokens=1,
            cached_tokens=0,
            output_tokens=1,
            timestamp=now.isoformat(),
        )

        deleted = accounting.prune_old_entries()
        assert deleted == 1
        records = accounting.get_records()
        assert len(records) == 1
        assert records[0]["model"] == "recent"

    def test_prune_keeps_entries_younger_than_cutoff(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        now = datetime.now(timezone.utc)
        accounting.record_turn(
            "openai",
            "young",
            input_tokens=1,
            cached_tokens=0,
            output_tokens=1,
            timestamp=(now - timedelta(days=9)).isoformat(),
        )
        # Just under the 10-day cutoff (with margin so the test cannot race
        # against the "now" computed inside prune_old_entries).
        accounting.record_turn(
            "openai",
            "boundary",
            input_tokens=1,
            cached_tokens=0,
            output_tokens=1,
            timestamp=(now - timedelta(days=10) + timedelta(seconds=30)).isoformat(),
        )

        assert accounting.prune_old_entries() == 0
        assert len(accounting.get_records()) == 2

    def test_prune_empty_db_returns_zero(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        assert accounting.prune_old_entries() == 0

    def test_prune_custom_days(monkeypatch, tmp_path):
        _point_at(monkeypatch, tmp_path)
        now = datetime.now(timezone.utc)
        accounting.record_turn(
            "openai",
            "two-days",
            input_tokens=1,
            cached_tokens=0,
            output_tokens=1,
            timestamp=(now - timedelta(days=2)).isoformat(),
        )
        accounting.record_turn(
            "openai",
            "fresh",
            input_tokens=1,
            cached_tokens=0,
            output_tokens=1,
            timestamp=now.isoformat(),
        )

        assert accounting.prune_old_entries(days=1) == 1
        records = accounting.get_records()
        assert len(records) == 1
        assert records[0]["model"] == "fresh"

    def test_cost_value_from_known_provider():
        """get_provider_cost_value returns numeric dollars for known models."""
        # 100k input tokens (below OpenAI's high-context threshold) at the
        # $0.20/1M cache-miss rate -> $0.02.
        value = get_provider_cost_value("openai", "gpt-5.6-luna", 100_000, 0, 0)
        assert value == pytest.approx(0.02)

    def test_cost_value_none_for_unknown_provider():
        assert get_provider_cost_value("nope", "model", 1, 1, 0) is None

    def test_cost_value_none_for_unknown_model():
        assert get_provider_cost_value("openai", "not-a-real-model", 1, 1, 0) is None

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
