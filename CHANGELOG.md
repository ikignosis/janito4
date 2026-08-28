# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.31.0...HEAD)

Changes since `v4.31.0` (2026-08-25).

### Added

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

### Changed

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
