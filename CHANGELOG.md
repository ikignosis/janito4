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
