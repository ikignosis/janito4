# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.33.0...HEAD)

Changes since `v4.33.0` (2026-08-29).

### Added

- `GetUrl` now accepts a `skip_llms_txt` parameter (default `False`). When set
  to `True`, the tool fetches the requested URL as-is without probing for an
  `llms.txt` site map. Also exposed as the `--skip-llms-txt` CLI flag.
- The "Waiting for response from the API server..." spinner now renders the
  elapsed waiting time via Rich's `TimeElapsedColumn` (issue #88).

### Changed

- The UI-side per-session behaviour moved out of `APIConfig` into a new
  frozen `UIConfig` (`janito/ui_config.py`) carrying the per-round
  `stream_runner` and the `TurnObserver`. `build_api_config` no longer
  accepts `verbose` / `stream_runner` / `observer`: callers pass the
  `UIConfig` to the client constructors and the module-level `run_turn`
  wrappers instead. `verbose` is now an explicit per-call emission gate on
  `Client.run_turn(verbose=...)` (default `False`); the CLI captures the
  session flag in the turn closure built by `_make_turn_factory`.
- `run_turn` no longer takes a caller-supplied `usage_out` out-param: the
  client now owns the `TokenStats` (issue #82), folds every round's usage
  into it and always delivers the end-of-turn report to the injected
  observer's `on_turn_complete`. `Client.run_turn` and the
  `_init_conversation_state` hook also gained explicit, typed
  conversation-context parameters (`previous_messages`,
  `previous_response_id`, `previous_items`, `instructions`) instead of an
  opaque `**kwargs`.
- The end-of-turn report carrier is now a single `TokenStats`
  (`janito/agent/usage.py`): the client-owned `TurnUsage` wrapper is gone
  (no nested `.stats`). `Client.run_turn` hands the `TokenStats` to the
  observer's `on_turn_complete` together with the turn's resolved
  `APIConfig`, so the report's `provider` / `model` / `max_input_tokens` /
  `max_output_tokens` always come from the session config. The `label` and
  `message_count` fields are dropped -- they only fed the INFO log line,
  which no longer carries the `{label}: {message_count}` part (the summary
  line itself already omitted it). The `_finalize` hooks no longer receive
  any usage object.
- The CLI turn report no longer carries a `show_cached` flag (and the
  `cached_details_attr` toggle on `_display_usage`/`_cost_counters` is
  gone): whether cached tokens are shown -- and billed at the provider's
  cache-hit rate -- is now derived from the normalized usage stats, which
  already carry `cached=None` for APIs that do not report cached-token
  details (the native Anthropic / DashScope / Gemini SDKs).

### Fixed

- `--version` and the startup banner now show the version derived from the
  latest git tag (e.g. `4.33.0.post1+g6412eb8`) when running from a git
  checkout (editable install / `uv sync`), instead of the stale hard-coded
  `0.2.0`. Installed wheels/sdists keep showing the released version from
  the distribution metadata.
