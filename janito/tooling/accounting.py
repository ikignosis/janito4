"""
Overall-use accounting backed by an SQLite database.

Every completed LLM turn that reports token usage (from either the CLI agent
loop or the web backend) is appended as one row to ``accounting.db`` inside
the Janito config directory (see :mod:`janito.config_dir`).  Each row records
the working directory, a turn ordinal, the timestamp, the provider/model and
the turn-wide token counters plus the estimated cost, so usage can be summed
and queried per directory / provider / model over time (issue #72).

:class:`AccountingStore` implements the database access (the module functions
below delegate to a module-level singleton).  Like :mod:`janito.tooling.tools_usage`,
the functions in this module are deliberately defensive: they never raise.
Accounting is a best-effort side feature and must not be able to break tool
execution or the agent loop, so every database access is wrapped and failures
are swallowed (optionally logged).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config_dir import get_config_dir

logger = logging.getLogger(__name__)

# Name of the SQLite database file stored in the config directory.
DB_FILENAME = "accounting.db"


class AccountingStore:
    """SQLite-backed overall-use log stored in the config directory.

    Args:
        db_path: Optional explicit path to the SQLite database file. When
            ``None`` (the default) the database lives at
            ``<config_dir>/accounting.db``.
    """

    def __init__(self, db_path=None):
        self._db_path = db_path
        # Serialises access from the multiple threads the web backend uses to
        # run turns concurrently. SQLite itself is also thread-safe with
        # ``check_same_thread = False``, but a lock keeps inserts atomic and
        # cheap.
        self._lock = threading.Lock()
        # Per-process turn ordinal: every recorded turn gets the next number
        # (1, 2, 3, ...) so the ``turn_count`` column is a stable, unique
        # sequence within this janito process regardless of the caller.
        self._turn_counter = 0

    @property
    def db_path(self) -> Path:
        """Return the path to the ``accounting.db`` file in the config directory.

        Returns:
            pathlib.Path: ``<config_dir>/accounting.db`` (or the explicit path
            given to the constructor).
        """
        if self._db_path is not None:
            return self._db_path
        return get_config_dir() / DB_FILENAME

    def _connect(self) -> sqlite3.Connection:
        """Open a connection to the accounting database, creating the schema if needed.

        The parent config directory is created on demand so the database can be
        written even on a fresh installation.

        Returns:
            sqlite3.Connection: An open connection with the ``accounting`` table
                guaranteed to exist.
        """
        db_path = self.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounting (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cwd TEXT NOT NULL,
                turn_count INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                input_tokens INTEGER,
                cached_tokens INTEGER,
                output_tokens INTEGER,
                cost REAL
            )
            """
        )
        conn.commit()
        return conn

    def record_turn(
        self,
        provider: str | None,
        model: str | None,
        input_tokens: int | None,
        cached_tokens: int | None,
        output_tokens: int | None,
        *,
        cwd: str | Path | None = None,
        turn_count: int | None = None,
        timestamp: str | None = None,
        cost: float | None = None,
    ) -> None:
        """Append one accounting row for a completed turn.

        ``cwd`` defaults to the process' current working directory,
        ``timestamp`` to the current UTC time (ISO-8601) and ``turn_count``
        to the next per-process ordinal.  This method never raises; any
        database error is logged and ignored.

        Args:
            provider: The provider that served the turn (may be ``None``).
            model: The model that served the turn (may be ``None``).
            input_tokens: Turn-wide input token count (may be ``None`` when
                the API did not report it).
            cached_tokens: Turn-wide cached input token count (``None`` when
                the API does not report cache details, e.g. the native
                Anthropic / DashScope / Gemini SDKs).
            output_tokens: Turn-wide output token count (may be ``None``).
            cwd: Working directory to record; defaults to ``Path.cwd()``.
            turn_count: Turn ordinal; defaults to the next per-process value.
            timestamp: ISO-8601 timestamp; defaults to the current UTC time.
            cost: Estimated cost in dollars (REAL); defaults to ``None``.
        """
        working_dir = Path.cwd() if cwd is None else Path(cwd)
        if cwd is not None and not str(cwd).strip():
            return
        stamp = (
            datetime.now(timezone.utc).isoformat() if timestamp is None else timestamp
        )

        try:
            with self._lock:
                self._turn_counter += 1
                count = self._turn_counter if turn_count is None else turn_count
                conn = self._connect()
                try:
                    conn.execute(
                        """
                        INSERT INTO accounting (
                            cwd, turn_count, timestamp, provider, model,
                            input_tokens, cached_tokens, output_tokens, cost
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(working_dir),
                            count,
                            stamp,
                            provider,
                            model,
                            input_tokens,
                            cached_tokens,
                            output_tokens,
                            cost,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:  # noqa: BLE001 - accounting must never break execution
            logger.debug(f"Failed to record accounting entry: {e}")

    def all_records(self, limit: int | None = None) -> list[dict]:
        """Return the recorded accounting rows, newest first.

        Args:
            limit: Optional maximum number of rows to return (most recent).

        Returns:
            list[dict]: Each dict maps column name to value (``cost`` is a
            float or ``None``). Empty if nothing has been recorded or the
            database cannot be read.
        """
        try:
            with self._lock:
                conn = self._connect()
                try:
                    query = (
                        "SELECT cwd, turn_count, timestamp, provider, model, "
                        "input_tokens, cached_tokens, output_tokens, cost "
                        "FROM accounting ORDER BY id DESC"
                    )
                    if limit is not None:
                        query += " LIMIT ?"
                        cursor = conn.execute(query, (limit,))
                    else:
                        cursor = conn.execute(query)
                    columns = [
                        "cwd",
                        "turn_count",
                        "timestamp",
                        "provider",
                        "model",
                        "input_tokens",
                        "cached_tokens",
                        "output_tokens",
                        "cost",
                    ]
                    return [dict(zip(columns, row)) for row in cursor.fetchall()]
                finally:
                    conn.close()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Failed to read accounting entries: {e}")
            return []

    def prune_old_entries(self, days: int = 10) -> int:
        """Delete accounting rows older than ``days`` days (default 10).

        Keeps the database from growing unbounded: rows whose ``timestamp``
        is older than ``now(UTC) - days`` are removed.  Timestamps are stored
        as UTC ISO-8601 strings and every row uses the same format, so a
        lexicographic comparison against the cutoff string is equivalent to a
        chronological one.

        Best-effort, like every other access in this module: never raises,
        failures are logged and ``0`` is returned.

        Args:
            days: Maximum age (in days) an entry may have before it is pruned.

        Returns:
            int: Number of deleted rows (``0`` when there is nothing to
            remove or the database cannot be written).
        """
        if days < 0:
            days = 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cursor = conn.execute(
                        "DELETE FROM accounting WHERE timestamp < ?",
                        (cutoff,),
                    )
                    conn.commit()
                    return cursor.rowcount
                finally:
                    conn.close()
        except Exception as e:  # noqa: BLE001 - accounting must never break execution
            logger.debug(f"Failed to prune accounting entries: {e}")
            return 0


# Module-level singleton store backing the functions below.
_store = AccountingStore()


def get_db_path() -> Path:
    """Return the path to the ``accounting.db`` file in the config directory.

    Returns:
        pathlib.Path: ``<config_dir>/accounting.db``.
    """
    return _store.db_path


def record_turn(
    provider: str | None,
    model: str | None,
    input_tokens: int | None,
    cached_tokens: int | None,
    output_tokens: int | None,
    *,
    cwd: str | Path | None = None,
    turn_count: int | None = None,
    timestamp: str | None = None,
    cost: float | None = None,
) -> None:
    """Append one accounting row for a completed turn.

    Thin wrapper over :meth:`AccountingStore.record_turn` (the module-level
    singleton); see it for the parameter documentation.  This function never
    raises; any database error is logged and ignored.
    """
    _store.record_turn(
        provider,
        model,
        input_tokens,
        cached_tokens,
        output_tokens,
        cwd=cwd,
        turn_count=turn_count,
        timestamp=timestamp,
        cost=cost,
    )


def get_records(limit: int | None = None) -> list[dict]:
    """Return the recorded accounting rows, newest first.

    Args:
        limit: Optional maximum number of rows to return (most recent).

    Returns:
        list[dict]: Each dict maps column name to value. Empty if nothing has
            been recorded or the database cannot be read.
    """
    return _store.all_records(limit)


def prune_old_entries(days: int = 10) -> int:
    """Delete accounting rows older than ``days`` days (default 10).

    Thin wrapper over :meth:`AccountingStore.prune_old_entries` (the
    module-level singleton); see it for the parameter documentation.  Called
    at startup so the database does not grow unbounded.  Never raises; any
    database error is logged and ``0`` is returned.

    Returns:
        int: Number of deleted rows.
    """
    return _store.prune_old_entries(days)


def main() -> None:
    """Command line interface for inspecting the accounting database."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Inspect the Janito overall-use accounting database."
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output in JSON format.",
    )
    parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=10,
        help="Maximum number of rows to show (most recent), default 10.",
    )
    args = parser.parse_args()

    records = get_records(args.limit)
    if args.json:
        print(json.dumps(records, indent=2))
        return

    if not records:
        print("No accounting entries recorded yet.")
        return

    for record in records:
        cost = record["cost"]
        cost_text = f"{cost:.4f}$" if cost is not None else "N/A"
        print(
            f"{record['timestamp']}  turn {record['turn_count']:>4}  "
            f"{record['cwd']}  {record['provider']}/{record['model']}  "
            f"in={record['input_tokens']} cached={record['cached_tokens']} "
            f"out={record['output_tokens']}  cost={cost_text}"
        )


if __name__ == "__main__":
    main()
