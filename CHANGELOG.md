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

### Changed

- `run_turn` no longer takes a caller-supplied `usage_out` out-param: the
  client now owns the `TurnUsage`, folds every round's usage into it and
  always delivers the end-of-turn report to the injected observer's
  `on_turn_complete` (issue #82). `Client.run_turn` and the
  `_init_conversation_state` hook also gained explicit, typed
  conversation-context parameters (`previous_messages`,
  `previous_response_id`, `previous_items`, `instructions`) instead of an
  opaque `**kwargs`.
- The CLI turn report no longer carries a `show_cached` flag on `TurnUsage`
  (and the `cached_details_attr` toggle on `_display_usage`/`_cost_counters`
  is gone): whether cached tokens are shown -- and billed at the provider's
  cache-hit rate -- is now derived from the normalized usage stats, which
  already carry `cached=None` for APIs that do not report cached-token
  details (the native Anthropic / DashScope / Gemini SDKs).

### Fixed

- `--version` and the startup banner now show the version derived from the
  latest git tag (e.g. `4.33.0.post1+g6412eb8`) when running from a git
  checkout (editable install / `uv sync`), instead of the stale hard-coded
  `0.2.0`. Installed wheels/sdists keep showing the released version from
  the distribution metadata.
