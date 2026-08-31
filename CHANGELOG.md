# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.35.0...HEAD)

Changes since `v4.35.0` (2026-08-31).

## [v4.35.0](https://github.com/joaompinto/janito/compare/v4.34.0...v4.35.0) - 2026-08-31

Changes since `v4.34.0` (2026-08-31).

### Added

- `StartTask`, `StopTask` and `WaitForTask` tools (new `tasks` toolset in
  `janito/tools/tasks/`, issue #94): a parallel-task entry point. The LLM
  calls `StartTask` when a request can be split into multiple tasks that can
  run in parallel, passing a `description` of what needs to be done and an
  optional `working_dir` / `privileges`; each task runs as a separate
  `janito` sub-process (description piped to stdin, stdout/stderr streamed to
  temp files). `StopTask` terminates a task and `WaitForTask` waits for a set
  of tasks and reports their exit codes. When `StartTask` is given no
  `privileges`, the child mirrors the running task's current (turn)
  privileges -- the session's `-r`/`-w`/`-x` flags plus any `/read`
  `/write` `/rx` `/rw` `/rwx` single-turn override (`janito/tooling/
  turn_privileges.py`) -- so a task spawned from a `/rwx` turn inherits full
  privileges instead of silently starting read-only.
- `WaitForTask` gained an optional `timeout` (seconds): the total budget to
  wait for the listed tasks. When it expires before every task has finished,
  the results collected so far are returned with `timed_out=True` and
  `pending_task_ids` (the tasks still running, which can then be stopped with
  `StopTask`); `None` (the default) keeps waiting indefinitely.
- `--web-session-ttl SECONDS` gives the web backend real TTL-based session
  expiry (issue #93): sessions idle longer than `SECONDS` are evicted from
  memory *lazily* (on access — no background task) and transparently
  reloaded from `.janito/sessions/` on the next lookup, so the sidebar list
  shrinks without ever surfacing a 404. `0` (the default) disables TTL and
  keeps today's behaviour; `--no-history` force-disables it (there is no
  disk mirror to reload from). Sending a prompt now counts as activity, so
  an open tab is never reaped mid-conversation.
- `janito/llm_clients/factory.py` (`create_client`) is the single
  `api_type` → client-class mapping for the shared turn pipeline (the CLI's
  `_make_turn_func` used to keep a private `_CLIENTS` dict); exported as
  `janito.llm_clients.create_client`.
- `janito/mcp_transports.py` is the single source for the `stdio`/`http`
  transport-type knowledge the CLI needs to *build* (`/mcp add`) and
  *display* (`/mcp list`, `--list-mcp`) service configs. It lives at the
  root level because the shell/CLI layers may not import `mcp_client` (the
  allowed-edge matrix in `tests/test_import_graph.py`).
- `janito/optional_packages.py` (`require_optional_package`) centralizes the
  optional-SDK install guards (Anthropic / DashScope / Gemini) shared by the
  CLI clients and the web runners.
- `janito/shell/conversation.py` centralizes where the conversation lives
  per API mode and the `(role, content)` display rows: `/history`,
  `/compact` and the interactive shell's `_history_row_count` all delegate
  to it (was triplicated logic).
- `janito/conversation_utils.py` (`rollback_to_last_turn` /
  `truncate_to_last_turn`) centralizes the turn truncation shared by the
  interactive shell rollback, `/rewind` and the web WebSocket rollback.
- `janito/llm_clients/openai/responses_items.py` (`message_item`) is the
  single builder for Responses `message` input items, replacing five inline
  copies across the OpenAI clients and the shell.
- `janito/web/backend/agent/stream_utils.py` (`_next_or_none`,
  `emit_stream_events`) centralizes the stream-consumption loop shared by
  the five web API runners (was duplicated in each runner; `_next_or_none`
  was copy-pasted between the Gemini and DashScope runners).

### Changed

- `WaitForTask` now animates its wait with a Rich spinner on an interactive
  terminal (issue #94): the `Waiting for N tasks...` line shows a spinner and
  an elapsed-time column, and the description counts down (`Waiting for 7
  tasks...`) as tasks finish. Each `✅ task X complete` line still prints the
  moment the task finishes (above the live spinner), followed by
  `✅ all N tasks finished`. Piped/CI output and web mode keep the previous
  plain progress lines (the browser already renders its own spinner on the
  tool card).
- Single-prompt runs (`janito "prompt"` or piped stdin) no longer print the
  read-only startup hint (`Started read-only, use /rwx <prompt> ...`): the
  `/rwx` command is an interactive-shell command, so the hint only appears
  when an *interactive* session starts (issue #85). The version banner is
  still printed (including the `--no-plugins` fallback path).
- `/compact`'s compression LLM call now runs silently: the session turn
  factory is re-invoked with `silent=True`, swapping the Rich observer for
  the new `SilentTurnObserver` (`janito/ui/observer.py`) — the raw recap
  JSON is no longer echoed to the terminal. The injected TUI stream runner
  is untouched (progress bar / Enter-to-cancel still work) and the
  end-of-turn accounting row is still recorded, so `/compact` keeps feeding
  `accounting.db` without printing anything.
- `/compact`'s per-mode history handling (rows, compact/keep zones, context
  application, compaction call args) is now a per-mode strategy
  (`_HistoryStrategy` + `_history_strategy()` factory in
  `janito/shell/cmds/compact.py`) instead of six repeated `if mode == ...`
  switches.
- The `/mcp add` and `/mcp list` / `--list-mcp` transport switches now
  delegate to `janito.mcp_transports` (`get_transport_spec`); user-facing
  output is byte-identical.
- The CLI startup banner and the Responses client now resolve the
  `responses_in_server` capability through one helper
  (`janito.llm_clients.openai.responses_state.responses_in_server`).
- `_responses_item_to_row` in `janito/shell/cmds/history.py` dispatches
  through a per-item-type renderer registry (`_ITEM_TO_ROW`).
- `Client._resolve_model_settings` now has a base default (reads the
  resolved `APIConfig`), removing the identical overrides in the
  Completions / Anthropic / Gemini clients; DashScope keeps its override
  (it drops `reasoning_effort`).
- Internal naming cleanup, no behaviour change: the turn observer's
  token-usage argument is `token_stats` everywhere (was `usage_out` /
  `turn_stats`), `TransportSpec.usage` became `usage_line`, and the
  `/compact` history strategy's parameters are `new_context` /
  `keep_zone_entries` (was `new_history` / `keep_zone_messages`).

### Fixed

- The stdio MCP transport no longer deadlocks when the server writes more
  to stderr than the OS pipe buffer can hold: an unread stderr pipe would
  make the child block on its write and never answer requests. stderr is
  now drained by a background thread into a bounded (200-line) debug buffer
  (`_stderr_lines`) and logged at `DEBUG`, so diagnostics are preserved
  (`janito/mcp_client/stdio.py`; regression test in
  `tests/test_mcp_client.py`).
