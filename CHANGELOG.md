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
- Reorganized the LLM-related modules (issue #79): `janito/openai_client/`
  is gone, replaced by `janito/llm_clients/` holding the SDK-agnostic core
  (`api_config.py`, `base_client.py`, `client_support.py`) plus per-vendor
  subpackages `openai/` (Completions + Responses), `anthropic/`, `dashscope/`
  and `gemini/`. The root-level `dashscope_api.py` / `dashscope_helpers.py` /
  `gemini_api.py` / `gemini_helpers.py` moved into their vendor subpackages,
  and all imports, tests and docs were updated accordingly (no compat
  shims).
- The Rich/UI-side code moved out of `janito/llm_clients/client_support.py`
  into a new `janito/ui/` subpackage, keeping `llm_clients/` LLM-only:
  `ui/observer.py` (`RichTurnObserver` + `_record_accounting`),
  `ui/stream_runner.py` (`_run_with_progress_bar` + `_is_enter_pressed`),
  `ui/display.py` (verbose banners/panels, reasoning/content renderers),
  `ui/usage.py` (`_display_usage`, `display_turn_usage`, capacity warning)
  and `ui/errors.py` (auth / not-found explainers). `client_support.py` now
  holds only the LLM-domain helpers (`RequestCancelled`, `_load_mcp`,
  `_object_items`/`_extract_raw_attrs`, `_classify_error`); the CLI and the
  affected tests import the UI pieces from `janito.ui` (no compat shims).
- `resolve_runtime_config` (and its `get_env_config` alias) moved out of the
  LLM client domain into the config layer as `janito.runtime_config` (issue
  #79 follow-up). `janito/llm_clients` no longer resolves runtime config
  itself: `build_api_config` lazy-imports the resolver from
  `janito.runtime_config` and stays the only place in `llm_clients` that
  touches the config/auth stores, so every client receives the resolved
  values through the frozen `APIConfig`. The CLI setup check, the chat model
  display and the web agent loop import the resolver from its new home; the
  client modules no longer re-export it (no compat shims).
- The legacy `janito/provider_accessors.py` facade (the module-level
  `get_*_from_provider` API) is gone; callers now use the typed provider
  accessors directly through the new `get_provider(name)` entry point in
  `janito/provider_registry.py` (returns a `Provider` whose methods replace
  every former accessor, e.g. `get_default_model_from_provider(name)` ->
  `get_provider(name).default_model()`). The former payload helpers
  (`apply_thinking_to_extra_body`, `apply_builtin_tools_to_extra_body`,
  `builtin_tools_enable_flags`, `format_thinking_display`) moved to the new
  `janito/provider_payloads.py`, the cost subsystem (`get_provider_cost`,
  `get_provider_cost_value`, and the promoted public `format_cost`) to
  `janito/provider_cost.py` (with a cached cost-module loader), and the
  API-type availability functions (`get_all_api_types`,
  `ensure_api_type_available`, ...) into `janito/provider_validation.py`.
  The duplicate `get_provider_config` in `janito/providers/__init__.py` was
  deleted (its consumers read the raw dict via `get_provider(name).info`),
  and `ProviderRegistry._variant_base` was promoted to the public
  `variant_base` method (no compat shims).

### Fixed

- `--version` and the startup banner now show the version derived from the
  latest git tag (e.g. `4.33.0.post1+g6412eb8`) when running from a git
  checkout (editable install / `uv sync`), instead of the stale hard-coded
  `0.2.0`. Installed wheels/sdists keep showing the released version from
  the distribution metadata.
