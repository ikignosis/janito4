# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.32.0...HEAD)

Changes since `v4.32.0` (2026-08-28).

## [v4.32.0](https://github.com/joaompinto/janito/compare/v4.31.0...v4.32.0) - 2026-08-28

Changes since `v4.31.0` (2026-08-25).

### Added

- `feat(usage)`: consolidate token usage into a shared `TokenStats` object
  (`janito.agent.usage`) and expose cumulative turn totals on the web
  `UsageEvent` payload: `turn_input` / `turn_cached` / `turn_output` sum the
  counters across every API round of a turn (tool-call rounds included),
  while the existing `total` / `input` / `output` / `cached` keep reporting
  the final round only. The web agent loop folds each round's usage into
  these totals (uniform `usage_object()` accessor added to every
  accumulator); the CLI turn report's `Cost` estimate bills them so
  tool-call rounds are included.
- `feat(tools)`: `ReadFile` accepts a negative `start_line` to read the last
  N lines of a file (tail semantics): `start_line=-5` returns lines from
  5-from-the-end to EOF and `max_lines` is ignored. Offsets deeper than the
  file are clamped to the first line (whole file returned, no error),
  `start_line=0` is rejected with an explanatory message, and an invalid
  `max_lines` is still validated even in tail mode. The CLI `--start-line`
  help, the ReadFile parameter docstring (and therefore the function-calling
  schema) and the web tool chip ("last 5 lines") were updated to match.
- `feat(alibaba)`: add the `qwen3.8-flash` model to the Alibaba provider's
  built-in config and cost estimation ($0.15 / $0.016 cache-hit / $0.47
  output per 1M tokens; 991K max input, 131K max output; built-in tools on
  the Responses API).
- `feat(zai)`: add the `glm-5.3-flash` model to the Z.ai provider's built-in
  config and cost estimation, and make it the provider default (closes
  #58).  GLM-5.3-Flash is the fast/cheap GLM-5 model (1M input / 128K
  output); its cost rates reflect the 50% launch-promotion price ($0.075 /
  $0.015 cache-hit / $0.25 output per 1M tokens) until 2026-09-09 24:00
  (UTC+8), after which the list price ($0.15 / $0.03 / $0.50) applies.

### Changed

- `refactor(usage)`: the conversation turn number is display-only, so it no
  longer rides through the API clients.  The `turn` parameter is gone from
  `Client.send` and every module-level `send_prompt` (Completions,
  Responses, Anthropic, DashScope, Gemini) and from the `TurnUsage`
  out-param; instead `display_turn_usage` now takes the turn number as a
  required keyword-only parameter, and
  `wrap_send_prompt_with_turn_report` consumes the caller-facing `turn`
  kwarg (interactive shell `Turn: #<n>` count, `1` for one-shot runs) and
  passes it to the renderer — it never reaches the API request.  Callers
  that do not track turns (e.g. /compact's side call) pass `None` and keep
  the legacy `{label}: {message_count}` display.
- `feat(usage)`: the CLI turn report's `Cost` estimate is now billed against
  the turn-specific cumulative counters (`turn_input` / `turn_output` /
  `turn_cached`), so tool-call rounds inside a turn are included; the
  displayed `In` / `Out` / `Cached` parts still mirror the final request's
  counters.
- `refactor(usage)`: move the CLI's end-of-turn reports (used files +
  token-usage summary) out of the per-client `_finalize` hooks and render
  them once, after `send_prompt` returns, from a `TurnUsage` out-param that
  `Client.send` populates (it folds every round's usage into a `TokenStats`,
  tool-call rounds included).  `_finalize` now only records the assistant
  message and returns.  A single wrapper
  (`client_support.wrap_send_prompt_with_turn_report`, applied in
  `cli/chat.py`) is responsible for "call the API + display usage", so the
  interactive shell, `/ask`, `/compact` and one-shot `janito <prompt>` all
  share the same report path; the duplicated per-API
  `input_attr`/`output_attr`/`cached_details_attr` plumbing is gone
  (`normalize_usage` now also accepts a `TokenStats`).  Cumulative turn
  totals are now available to the CLI too, matching the web loop.
- `feat(cli)`: accept `-m` as a shorthand for `--model` (mirroring the
  existing `-p`/`--provider`), and document it in the CLI usage text.
- `feat(config)`: enforce the documented model-selection restriction.
  `--set model=<name>`, the `--model` flag, the shell `/model` command and
  the web Settings drawer now validate the model name against the
  provider's built-in models (the base provider's models for variants) and
  reject unknown names with the available models listed; `openrouter` and
  `custom` still accept any model name. Matching names are stored/used in
  their canonical built-in casing. New helper
  `janito.provider_validation.validate_model_name`.
- `AGENTS.md`: require the docs to be updated whenever a change is
  user facing, so the documentation does not drift from the code.

### Documentation

- Sync the docs with the new model-selection restriction: for every provider
  except `openrouter` and `custom`, `--model` / `--set model=...` accept only
  the provider's built-in models (model-scoped settings are restricted to
  them too), while `openrouter` and `custom` still accept any model name
  (`docs/configuration/providers.md`, `docs/configuration/variants.md`,
  `docs/reference/cli-options.md`, `docs/usage/interactive-mode.md`, the CLI
  `--help` examples, and the `/model` shell command docs).
- Sync the docs with the current implementation: scoped configuration keys
  (flat / provider-scoped / model-scoped) and the `--api-type`,
  `--list-models`, `--uninstall-plugin` and `--web` options
  (`docs/reference/cli-options.md`, `docs/configuration/index.md`); the
  Gemini native-SDK client as the fifth API client (`ARCHITECTURE.md`,
  `docs/usage/web-ui.md`); the interactive shell commands, model-named
  prompt and exit behaviour (`docs/usage/interactive-mode.md`,
  `docs/usage/cli-vs-web.md`, `README.md`); pipe-mode semantics (stdin
  replaces the positional prompt) and the "do not combine `--set` with a
  prompt" warning (`docs/usage/single-prompt.md`); the current built-in
  models per provider and the `custom`-provider local-LLM setup
  (`docs/configuration/providers.md`, `docs/getting-started/quick-start.md`);
  and the `FindFiles` tool (`docs/tools/files.md`).
