# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.34.0...HEAD)

Changes since `v4.34.0` (2026-08-31).

### Added

- `--web-session-ttl SECONDS` gives the web backend real TTL-based session
  expiry (issue #93): sessions idle longer than `SECONDS` are evicted from
  memory *lazily* (on access — no background task) and transparently
  reloaded from `.janito/sessions/` on the next lookup, so the sidebar list
  shrinks without ever surfacing a 404. `0` (the default) disables TTL and
  keeps today's behaviour; `--no-history` force-disables it (there is no
  disk mirror to reload from). Sending a prompt now counts as activity, so
  an open tab is never reaped mid-conversation.
