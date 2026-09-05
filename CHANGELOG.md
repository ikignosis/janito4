# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.39.0...HEAD)

Changes since `v4.39.0` (2026-09-05).

### Added
- Hosted deferred tool loading (`tool_search`, closes #128): every tool
  carries a namespace (its toolset); Meta `muse-spark` models group tools
  into `namespace` entries with `defer_loading` plus `{"type":
  "tool_search"}` (Responses-only, off for non-Meta). New observer events
  `on_tool_search_call` / `on_tool_search_output` render as `Searching for
  tools on <paths>` / `Loaded (n) tools`.
