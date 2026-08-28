# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.32.0...HEAD)

### Added

- `/notools <message>` shell command: send a prompt through the **main**
  conversation history while offering the model no tools for that message
  only (the per-message equivalent of `--no-tools`); the next prompt goes
  back to the session's default tool configuration.
- `/rx <question>` shell command: send a prompt through the **main**
  conversation history while restricting `tools=` to the read and execute
  (`"r"`/`"x"` permission) built-in tools — the model can read/search/fetch
  and run commands but cannot write or modify anything (issue #63).

### Changed

- Extract the per-round stream runner out of the API clients
  (`openai_client/completions_api.py`) into `openai_client/client_support.py`
  (issue #61). The runner — thread creation, the Rich spinner and the
  Enter-to-cancel detection (`_run_with_progress_bar` + `_is_enter_pressed`)
  — is now a UI-side concern **injected** through `Client(stream_runner=...)`:
  with the default `None`, `send_prompt`/`Client.send` call each streaming
  round directly (no thread, no spinner, no Enter-to-cancel), keeping them
  purely API-side and reusable in non-TUI contexts. The CLI wires in the TUI
  runner for every entry point through `_make_send_prompt_func`
  (`cli/chat.py`), so the shell, `/ask`, `/compact` and one-shot prompts keep
  the exact same spinner/Enter-to-cancel behaviour (the runner is invoked per
  round, so the spinner is never shown during tool execution).
  `RequestCancelled` moved to `client_support` (still re-exported from
  `completions_api`); tests now inject a fake runner via the constructor
  instead of monkeypatching a module global.

### Fixed

- `/status` shows a **Model** row with the session's effective model
  (`--model`, `/model` or the startup resolution); the provider's built-in
  default is marked `(default)`. Model-scoped settings (API type, max output
  tokens, reasoning level, thinking, Responses-in-server) are now resolved
  for that model instead of silently using the provider's built-in default —
  previously an Alibaba variant running e.g. `qwen3.8-flash` showed no model
  at all and reported the default model's settings.
- `scripts/promote_changelog.py`: write a single trailing newline even when
  `[Unreleased]` is the last section of the file (post-release-reset state);
  the missing newline made pre-commit's `end-of-file-fixer` rewrite the file
  and abort the release commit.

Changes since `v4.32.0` (2026-08-28).
