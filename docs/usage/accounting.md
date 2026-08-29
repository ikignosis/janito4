# Overall-use accounting

Janito keeps a **local SQLite log of every completed LLM turn** (issue #72) so
you can see how many tokens and how much money each provider/model has cost
you, per working directory, over time.

## Where the data lives

The log is stored at:

```
<config dir>/accounting.db
```

where the config dir is `~/.janito` by default (honoring `-c/--config-dir`
and `-l/--local` like every other config file, mirroring the `tools_use.db`
pattern).

## What is recorded

One row is appended per **completed turn that reported token usage** — from
the interactive shell, `/ask`, `/compact`, one-shot prompts and the web UI.
Each row has:

| Column         | Meaning                                                        |
|----------------|----------------------------------------------------------------|
| `cwd`          | Working directory the turn ran in                              |
| `turn_count`   | Turn ordinal within the janito process (1, 2, 3, …)            |
| `timestamp`    | UTC time the turn completed (ISO-8601)                         |
| `provider`     | Provider that served the turn (e.g. `deepseek`)                |
| `model`        | Model used (e.g. `deepseek-v4-flash`)                          |
| `input_tokens` | Turn-wide input tokens (all API rounds of the turn included)   |
| `cached_tokens`| Turn-wide cached input tokens (`NULL` when not reported)       |
| `output_tokens`| Turn-wide output tokens (all API rounds of the turn included)  |
| `cost`         | Estimated cost in dollars (REAL), `NULL` when unknown          |

The token counters are the **turn-wide cumulative** values (tool-call rounds
included) and the cost is the numeric dollar estimate — the same estimate the
end-of-turn `Cost:` summary shows, but stored as a plain number so it can be
summed and aggregated.

!!! note
    Accounting is a best-effort side feature: it never raises and can never
    break tool execution or the agent loop. Rows are only written for turns
    that completed successfully and reported usage.

## Inspecting the log

The module ships a small command-line inspector:

```bash
python -m janito.tooling.accounting            # last 10 rows
python -m janito.tooling.accounting --limit 50 # last 50 rows
python -m janito.tooling.accounting --json     # JSON output
```

Example output:

```
2026-08-28T17:47:34.778205+00:00  turn    1  /home/me/proj  deepseek/deepseek-v4-flash  in=180 cached=10 out=120  cost=0.0001$
2026-08-28T17:47:34.778580+00:00  turn    2  /home/me/proj  openai/gpt-5.6-luna          in=50000 cached=5000 out=4000  cost=0.0150$
```

## Querying with SQL

`accounting.db` is a plain SQLite database, so you can query it directly:

```bash
sqlite3 ~/.janito/accounting.db \
  "SELECT provider, model, SUM(input_tokens) AS in, SUM(output_tokens) AS out,
          ROUND(SUM(cost), 4) AS total_cost
   FROM accounting GROUP BY provider, model ORDER BY total_cost DESC;"
```

```bash
sqlite3 ~/.janito/accounting.db \
  "SELECT cwd, COUNT(*) AS turns, ROUND(SUM(cost), 4) AS total_cost
   FROM accounting GROUP BY cwd ORDER BY total_cost DESC;"
```
