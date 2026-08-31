# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.35.0...HEAD)

Changes since `v4.35.0` (2026-08-31).

### Added

- `--no-tasks` CLI flag: disables the tasks toolset (`StartTask`, `StopTask`,
  `WaitForTask`) while leaving every other toolset (files, system, net) and
  the skill tools enabled. Works in both the terminal CLI and `--web` mode.
