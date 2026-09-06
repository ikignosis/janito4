# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.39.0...HEAD)

Changes since `v4.39.0` (2026-09-05).

### Added
- Built-in system prompt encourages parallel background tasks via
  `StartTask` when safe (closes #107).
- Search grounding for Meta `muse-spark` models (closes #131):
  Responses-only `web_search` builtin tool plus `on_web_search_call` /
  `on_web_search_done` observer events (CLI shows `Searching the web...`;
  web loop emits `web_search` / `sources` events).  Citation markers are
  intentionally not rendered: the model's cited sources are low quality.

### Fixed
- Corrected `ARCHITECTURE.md` path in `AGENTS.md` to `dev-docs/ARCHITECTURE.md`.
