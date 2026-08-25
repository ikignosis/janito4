# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.30.0...HEAD)

Changes since `v4.30.0` (2026-08-24).

### Added

- `GetUrl` now discovers and returns `llms.txt` site maps: before fetching a
  site URL it probes `<origin>/llms.txt` and `<origin>/.well-known/llms.txt`
  with lightweight `HEAD` requests and, when one answers `200 OK`, fetches it
  with a `GET` request and returns the content as-is (no Markdown parsing,
  never truncated by `max_length`/`max_lines`) instead of the requested page. Discovery probes are silent; only a
  successful retrieval is reported, and if no `llms.txt` exists the tool falls
  back to its regular fetch behavior.

### Changed

- `GetUrl` no longer stores oversized `llms.txt` site maps in a temporary
  file: llms.txt content is exempt from the `threshold`/temp-file behaviour
  and is always returned inline in full (regular fetches keep storing
  oversized content to a temp file as before).
- The `RunGitHubCLI` tool schema now exposes a clear `cmdline` parameter
  description stating that the value is appended after the `gh` command and
  that `gh` itself must not be included (it is prepended automatically).

### Fixed

- Update the GPT-5.6 Sol cost estimate to OpenAI's promotional pricing
  ($4.00 input / $0.40 cached / $20.00 output per 1M tokens, down from
  $5.00 / $0.50 / $30.00), matching the official rate card.
