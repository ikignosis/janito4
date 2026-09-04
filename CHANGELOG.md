# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.38.0...HEAD)

Changes since `v4.38.0` (2026-09-04).

### Changed
- Expand `--no-tools` scope: it now disables skill tools, plugin tools, and server-side/builtin provider tools in addition to the autoload toolsets and MCP tools (close #127).

### Fixed
- Propagate `--reasoning-effort` CLI flag to the interactive session and `/status` display (previously `/status` showed the built-in default, e.g. `minimal (default)`, instead of the CLI value like `high`; the API call itself already used the CLI value via `build_api_config`).
- Show `Model Default` instead of `disabled` for Thinking in `/status` and `--show-config` when thinking is not forced (a falsy value means the model's own default applies, not forced off).
