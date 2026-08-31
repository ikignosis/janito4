# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.34.0...HEAD)

Changes since `v4.34.0` (2026-08-31).

### Added

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

### Changed

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
- Internal naming cleanup, no behaviour change: the turn observer's
  token-usage argument is `token_stats` everywhere (was `usage_out` /
  `turn_stats`), `TransportSpec.usage` became `usage_line`, and the
  `/compact` history strategy's parameters are `new_context` /
  `keep_zone_entries` (was `new_history` / `keep_zone_messages`).
