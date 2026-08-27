# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.31.0...HEAD)

Changes since `v4.31.0` (2026-08-25).

### Added

- `feat(alibaba)`: add the `qwen3.8-flash` model to the Alibaba provider's
  built-in config and cost estimation ($0.15 / $0.016 cache-hit / $0.47
  output per 1M tokens; 991K max input, 131K max output; built-in tools on
  the Responses API).
