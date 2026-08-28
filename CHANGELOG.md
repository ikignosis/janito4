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

- `scripts/promote_changelog.py`: write a single trailing newline even when
  `[Unreleased]` is the last section of the file (post-release-reset state);
  the missing newline made pre-commit's `end-of-file-fixer` rewrite the file
  and abort the release commit.

Changes since `v4.32.0` (2026-08-28).
