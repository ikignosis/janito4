# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.37.0...HEAD)

Changes since `v4.37.0` (2026-09-03).

### Changed

- Narrowed blind `except Exception` handlers to specific types (`OSError`,
  `UnicodeError`, `ValueError`, ...) and enabled ruff `BLE001`.

### Added

- `/clear` shows running tasks count (issue #112): after clearing the
  conversation, prints "<count> task(s) still running, use /tasks for
  viewing." when background tasks are still running.
- Anthropic Fable 5.1 support (issue #103): replaced `claude-fable-5` with
  `claude-fable-5-1` (1M in / 128k out, Completions + native Anthropic).
- HTTP 429 rate-limit retry (issue #116): `Client.run_turn` retries the
  streaming round after a 429 instead of failing the turn, with exponential
  backoff from 1s (plus jitter) and `Retry-After` honored when present. The
  wait is delivered through a new `TurnObserver.on_limits` event; the Rich
  observer shows a spinner (`Requests`/`Tokens`/`Rate` limit was reached,
  retrying in (n)s.) while waiting. Gives up after 5 minutes of consecutive
  429 waits and re-raises the last error.

## [v4.37.0](https://github.com/joaompinto/janito/compare/v4.36.0...v4.37.0) - 2026-09-03

Changes since `v4.36.0` (2026-09-01).

### Added

- `responses_include` provider/model config option: extra `include` values
  to request on every Responses API call. `meta`'s Muse Spark models
  declare `["reasoning.encrypted_content"]` -- Muse Spark exposes its chain
  of thought only in encrypted form, and the `reasoning` output items
  returned in each response must be replayed verbatim in the next request's
  `input` to preserve cross-turn reasoning.
- `meta` provider (issue #115): support for Meta Model API and the Muse Spark
  models. The provider ships two built-in models -- `muse-spark-1.3` (the
  standard tier, the default model) and the cheaper
  `muse-spark-1.3-contributor` tier -- served by a single OpenAI-compatible
  base URL (`https://api.meta.ai/v1`) through the Responses (built-in
  default) and Chat Completions API types, with the 1M-token context window
  declared per model. Reasoning depth is configurable via
  `--reasoning-effort` (the `minimal`/`low`/`medium`/`high` levels declared
  per the Meta Model API reasoning cookbook; no built-in default, since
  Meta's own default effort is still being finalized). A cost module bills
  input / cached-input / output tokens at each tier's published rates.
- `ListTasks` tool (issue #101): a blocking-free snapshot of every task known
  to the manager -- running *and* finished -- so the model can discover what
  has been started (including tasks orphaned by a mid-turn rollback, where
  the `StartTask` call is lost from the conversation history) and how each
  one ended, without waiting on anything the way `WaitForTask` does. Each
  entry carries the task id, one-line summary, state (`running` while alive,
  then `finished` / `timeout` / `stopped` / `killed` / `error`), pid, working
  directory and duration; running tasks are listed first (in start order).
  An optional `running_only` flag restricts the result to the tasks still in
  flight. Standalone check: `python -m janito.tools.tasks.list_tasks [--json]`.
- End-of-turn notice in the interactive shell (issue #101): after each turn
  that leaves parallel tasks running, janito prints "The following (n) tasks
  are still running:" with a `Task ID | Summary` table, whether the turn
  finished normally or was rolled back.
- Ctrl+C confirm-quit flow in the interactive shell (issue #101): when tasks
  are running, the quit confirmation shows the running-tasks notice and asks
  "Do you want to exit and terminate all tasks?"; answering "y" (or pressing
  Ctrl+C again at the prompt) kills them immediately. Without running tasks
  the original "Do you want to quit the conversation?" prompt is shown.

### Fixed

- Server-side Responses instructions persistence: the ``instructions``
  parameter was only sent on the first turn of a server-side conversation,
  but some providers (e.g. Meta) do not persist it across
  ``previous_response_id`` turns and require it on every request. It is now
  re-sent on every server-side round (also correct for providers that fold
  it into the stored conversation, like OpenAI). Covered by
  ``test_run_turn_sends_instructions_on_every_server_side_turn``.
- Privilege-override note in the interactive shell (issue #109): the
  ``/read`` ``/write`` ``/rx`` ``/rw`` ``/rwx`` commands printed a hardcoded
  ``Note: this turn runs with privileges (-r/-w/-x)`` regardless of the
  command used -- e.g. ``/rx`` (read + execute only) wrongly claimed write
  privileges. The note now renders the flags the command actually grants:
  ``/rx`` prints ``(-r/-x)``, ``/read`` ``(-r)``, ``/write`` ``(-w)``,
  ``/rw`` ``(-r/-w)`` and ``/rwx`` ``(-r/-w/-x)``. Covered by new cases in
  ``tests/test_privileges.py``.

### Changed

- `janito/taskmanager.py` split into the `janito/taskmanager/` package
  (issue #104): `constants` (exit-reason vocabulary, grace periods),
  `process` (output reading, timeout validation, exit-code mapping and
  termination), `command` (child command-line construction), `task` (the
  `Task` dataclass) and `manager` (`TaskManager` + the `task_manager`
  singleton). The package `__init__` re-exports the full public surface, so
  the `janito.tools.tasks.*` tools, the interactive shell and every other
  consumer keep importing `janito.taskmanager` unchanged. Test patch targets
  moved to the defining modules (`tm.command.config_cli_args`,
  `tm.process.TERM_GRACE_SECONDS`).
- Token-usage summary line style (issue #105): the end-of-turn
  ``=== Time | In | Out | Cached | Cost ===`` line is now rendered with a
  **dark green** background (``bright_white on dark_green``, xterm-256 color
  22) instead of magenta. Also pinned by a new ``tests/test_usage_line_style.py``
  covering both render paths (``_display_usage`` and ``display_turn_usage``).
- `TaskManager.cleanup()` (the `atexit` hook, issue #101) now delegates
  killing to the new `TaskManager.kill_all()`, which sends **SIGKILL
  immediately** instead of the previous SIGTERM: at interpreter exit there is
  no event loop left to service a clean shutdown, so the SIGTERM grace period
  only delayed the inevitable force-kill. The same `kill_all()` backs the
  shell's confirm-quit path, so tasks are terminated on every exit path.
- Builtin system prompt: the planner guidance now asks the model to produce
  a **concise** plan (instead of "create one") before implementing, keeping
  the requirement to get the user's approval or feedback first.
