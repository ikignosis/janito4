# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.33.0...HEAD)

Changes since `v4.33.0` (2026-08-29).

### Added

- `--set privileges=rwx` persists the session's default privileges in
  `config.json` (issue #89): sessions that pass no `-r`/`-w`/`-x` flag
  start with the configured privileges instead of the built-in read-only
  default. The value accepts any combination/order of `r`/`w`/`x` and is
  validated and canonicalized at set time; explicit `-r`/`-w`/`-x` flags
  always take priority over the configured default, and `--unset
  privileges` restores read-only.
- `GetUrl` now accepts a `skip_llms_txt` parameter (default `False`). When set
  to `True`, the tool fetches the requested URL as-is without probing for an
  `llms.txt` site map. Also exposed as the `--skip-llms-txt` CLI flag.
- The "Waiting for response from the API server..." spinner now renders the
  elapsed waiting time via Rich's `TimeElapsedColumn` (issue #88).

### Changed

- The execution-time privilege gate in `run_tool` now distinguishes a tool
  that does not exist at all from one that exists but was not offered in
  the current turn: an unknown name (e.g. a typo like `Grep`) is reported
  as `Tool 'X' not found.` and the tool result carries an `available_tools`
  list the model can pick from, instead of blaming the session privileges
  (`-r`/`-w`/`-x`).
- `ARCHITECTURE.md` now documents *why* the CLI is fully synchronous while
  the web backend is fully asyncio (issue #83): a new "Two runtimes, one
  engine" section explains the single-user-terminal vs.
  many-sessions-event-loop drivers, the three `asyncio.to_thread` seams where
  the web loop bridges back into the sync engine (native-SDK stream
  pumping, tool execution, in-browser prompting), and why keeping
  `async def` out of the shared layers is what lets both loops reuse the
  same adapters.
- `preserve_thinking` is no longer a configurable setting (a legacy
  `--set preserve_thinking=...` key in `config.json` is ignored). It is now
  a built-in model default in the provider config: the Alibaba/Qwen
  hybrid-thinking models (`qwen3.8-max`, `qwen3.8-flash`) declare it
  `True`, so the OpenAI-compatible Completions / Responses calls send
  `extra_body={'preserve_thinking': True}` automatically (per the
  QwenCloud Thinking guide) -- the API appends the assistant messages'
  `reasoning_content` to the next input so the model can reference its own
  prior reasoning across multi-turn conversations. `APIConfig` and the web
  agent loop now resolve it from the provider config (`Provider
  .preserve_thinking(model)`), and the native-SDK API types
  (Anthropic/DashScope/Gemini) continue to drop it.
- Renamed the shared per-API adapter layer `janito/agent/` ->
  `janito/llm_adapters/` (the package name no longer suggests orchestration
  nor collides with `janito/web/backend/agent/`, the actual web agent
  loop). The domain matrix in `tests/test_import_graph.py` and
  `ARCHITECTURE.md` were updated accordingly (`llm_clients`, `ui` and
  `web` depend one-way on `llm_adapters`, which must never import from
  `llm_clients`).
  - The web agent event dataclasses moved back to their historical home
    `janito/web/backend/events.py` (from `llm_adapters/events.py`): they
    are the browser wire format and only the web loop consumes them.
    `usage_event_from_usage` now lives there too (built on
    `janito.llm_adapters.usage.normalize_usage`), and the web-only
    `.usage_event()` methods were removed from the five shared
    accumulators in `llm_adapters` -- the web loop builds the `UsageEvent`
    via `usage_event_from_usage(acc.usage_object(), ...)` instead.
    `llm_adapters/usage.py` keeps only the CLI/UI-shared
    `TokenStats` / `normalize_usage` / `format_tokens`.
  - `TurnObserver` / `NullObserver` remain in `llm_adapters/observer.py`:
    the turn pipeline (`llm_clients`) drives the observer, so it cannot
    move into `ui/` without creating the forbidden `llm_clients -> ui`
    edge (issue #90).
- Reviewed the package boundaries (issue #90) and enforced them with a new
  static import-graph test (`tests/test_import_graph.py`):
  - `SessionSetup` moved from `janito/cli/session_setup.py` to the package
    root (`janito/session_setup.py`) so the web backend never imports from
    the `cli` package.
  - The concrete `UIConfig` moved from `janito/ui_config.py` to
    `janito/ui/config.py`; the turn pipeline now depends only on a
    structural `UIConfig` protocol in `llm_clients/base_client.py`, so the
    API clients never import the UI package.
  - The `agent` <-> `llm_clients` cycle was broken: the stream converters
    (`_convert_tools_to_anthropic_format`,
    `_convert_tools_to_responses_format`), `_ModelEndpointMismatch`, the
    Gemini wire-format conversions + `GeminiStreamConsumer` and the SDK
    raw-attrs helpers (`janito/agent/sdk.py`) moved into the shared
    `agent` adapter layer; `llm_clients` now depends on `agent` one-way.
  - The `tooling` <-> `tools` cycle was broken: tool discovery and the
    privilege predicates moved to `janito/tooling/discovery.py`; `tools/`
    is now a one-way consumer of the framework.
  - The remaining `root` <-> `providers` lazy cycle is documented at each
    import site as accepted-by-design.
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
- The web backend no longer imports from `llm_clients` (issue #90 boundary
  follow-up): the last per-API objects it needed from the CLI client
  packages — the DashScope endpoint-routing helpers `_is_multimodal_model`
  and `_to_multimodal_messages` (`_ModelEndpointMismatch` already lived
  there) — moved to the shared `janito/llm_adapters/dashscope.py`, where the
  web DashScope runner and the CLI `dashscope_stream` both pick them up. The
  `llm_clients` target was removed from the `web` row of the
  `ALLOWED_EDGES` matrix in `tests/test_import_graph.py` (so any future
  `web -> llm_clients` import fails the suite) and the matrix + layering
  notes in `ARCHITECTURE.md` updated: `web`'s per-API code now depends
  exclusively on `llm_adapters`, keeping the web agent runners thin
  async glue.

### Fixed

- `--version` and the startup banner now show the version derived from the
  latest git tag (e.g. `4.33.0.post1+g6412eb8`) when running from a git
  checkout (editable install / `uv sync`), instead of the stale hard-coded
  `0.2.0`. Installed wheels/sdists keep showing the released version from
  the distribution metadata.
