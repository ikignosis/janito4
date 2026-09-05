# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.38.0...HEAD)

Changes since `v4.38.0` (2026-09-04).

### Added
- Add `/push` and `/pop` shell commands: nestable conversation-thread stack with deep-copy snapshots per level, `[n]` depth in the prompt, `Last Message:` replay on `/pop`, and full-stack clear on `clear`/`F2` (close #124).
- Support `/push <msg>`: after cloning the history into a new thread, immediately start a turn with `<msg>` as the prompt (refs #124).

### Changed
- Change task ids to incremental integers starting at 1 instead of random hex strings (close #111).
- Expand `--no-tools` scope: it now disables skill tools, plugin tools, and server-side/builtin provider tools in addition to the autoload toolsets and MCP tools (close #127).
- Add `/effort <level>` shell command: show or switch the session reasoning effort at runtime, validated against the current model's supported levels (`/effort clear` restores config/default) (close #121).
- Add `-C`/`--continue` to resume the previous interactive conversation in a working directory: the shell now mirrors its conversation to `./.janito/session.json` after every interaction (disabled by `--no-history`), and `-C` restores it together with its provider/model/API type (server-side Responses conversations resume from their last response id).
- `janito -C` / `--continue` now prints the 5 most recent conversation messages on resume (a `Resumed conversation` recap, display-only), so you can see where the previous session left off; the full restored context is still sent to the model.

### Removed
- Drop backwards-compat shims: legacy top-level `endpoint` config fallback (use `providers.<name>.endpoint`), `get_skills_dir` alias (use `get_default_skills_dir`), `MCP_CONFIG_PATH` constant (use `get_mcp_config_path()`), legacy web session `<id>.jsonl` files (sessions are now only `<id>/metadata.json`), and the two-source `/skills` summary format (always `home, agents, local`).

### Fixed
- Propagate `--reasoning-effort` CLI flag to the interactive session and `/status` display (previously `/status` showed the built-in default, e.g. `minimal (default)`, instead of the CLI value like `high`; the API call itself already used the CLI value via `build_api_config`).
- Show `Model Default` instead of `disabled` for Thinking in `/status` and `--show-config` when thinking is not forced (a falsy value means the model's own default applies, not forced off).
