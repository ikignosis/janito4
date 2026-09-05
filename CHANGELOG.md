# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.38.0...HEAD)

Changes since `v4.38.0` (2026-09-04).

### Added
- Request Meta reasoning summaries on the Responses API (`reasoning.summary="auto"` via new `thinking_summary` model config, enabled for both Muse Spark models; streamed as `response.reasoning_summary_text` deltas and surfaced via `on_reasoning`) (close #134).
- Add `GetTaskInfo` tool (`task_id`): full detail snapshot of a single task, including description and stdout/stderr filenames (close #117).
- Add `/push` and `/pop` shell commands: nestable conversation-thread stack with deep-copy snapshots per level, `[n]` depth in the prompt, `Last Message:` replay on `/pop`, and full-stack clear on `clear`/`F2` (close #124).
- Support `/push <msg>`: after cloning the history into a new thread, immediately start a turn with `<msg>` as the prompt (refs #124).
- Restore `F12` "Do It" keybinding (auto-sends a `Do It` prompt to continue an existing plan) and print `Keys: F2 - Clear conversation, F12 - Send "Do It"` with rich styling before the interactive startup line.

### Changed
- Change `/read`, `/write`, `/rx`, `/rw` and `/rwx` from single-turn `/cmd <msg>` prompt overrides into bare session privilege switches: each command sets `running_privileges` for the whole session (all subsequent prompts), with extra text ignored, and the switch is reflected in `/priv`, the `/help` session-switches table and the Turn-rule privilege badge (close #141).
- Show reasoning effort (`effort: <level>`) in the shell status bar instead of `[F2] clear [/exit] end`, using the same style as the provider segment.
- Change task ids to incremental integers starting at 1 instead of random hex strings (close #111).
- Expand `--no-tools` scope: it now disables skill tools, plugin tools, and server-side/builtin provider tools in addition to the autoload toolsets and MCP tools (close #127).
- Add `/effort <level>` shell command: show or switch the session reasoning effort at runtime, validated against the current model's supported levels (`/effort clear` restores config/default) (close #121).
- Add `-C`/`--continue` to resume the previous interactive conversation in a working directory: the shell now mirrors its conversation to `./.janito/session.json` after every interaction (disabled by `--no-history`), and `-C` restores it together with its provider/model/API type (server-side Responses conversations resume from their last response id).
- `janito -C` / `--continue` now prints a `Resumed conversation` recap on resume (display-only): the latest user prompt plus the replies that followed, shown in full text — tool-call/reasoning rows are hidden — so you can see where the previous session left off; the full restored context is still sent to the model.
- Break all intra-package import cycles (close #110): new `tests/test_circular_deps.py` detector-guard (top-level + lazy imports, Tarjan SCCs, fails on any cycle); provider-name/label leaves (`providers/variant_names.py`, `system_labels.py`) so `providers/*` never imports `config_*`; `ConversationResult` moved to the `responses_items` leaf; `find_files_cli` merged back into the `FindFiles` tool module; web `stream_prompt` passed as an explicit `stream_fn` argument through the chat turn helpers.

### Removed
- Drop backwards-compat shims: legacy top-level `endpoint` config fallback (use `providers.<name>.endpoint`), `get_skills_dir` alias (use `get_default_skills_dir`), `MCP_CONFIG_PATH` constant (use `get_mcp_config_path()`), legacy web session `<id>.jsonl` files (sessions are now only `<id>/metadata.json`), and the two-source `/skills` summary format (always `home, agents, local`).

### Fixed
- Propagate `--reasoning-effort` CLI flag to the interactive session and `/status` display (previously `/status` showed the built-in default, e.g. `minimal (default)`, instead of the CLI value like `high`; the API call itself already used the CLI value via `build_api_config`).
- Show `Model Default` instead of `disabled` for Thinking in `/status` and `--show-config` when thinking is not forced (a falsy value means the model's own default applies, not forced off).
- Tests: behavior-over-strings pilots — shared `assert_command_registered` / `assert_command_matching` helpers, registry-driven `/help` smoke test, state-only `/provider` switch/history assertions, plus `docs/development/testing.md` guideline.
- Replace blind `except Exception` with specific error tuples in the plugin-install and MCP-tools-listing CLI handlers, the `FindFiles` tool, and the web chat router/helpers (request JSON, socket close, SSE encoding, turn cleanup).
