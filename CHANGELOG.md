# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.33.0...HEAD)

Changes since `v4.33.0` (2026-08-29).

### Fixed

- `--version` and the startup banner now show the version derived from the
  latest git tag (e.g. `4.33.0.post1+g6412eb8`) when running from a git
  checkout (editable install / `uv sync`), instead of the stale hard-coded
  `0.2.0`. Installed wheels/sdists keep showing the released version from
  the distribution metadata.
