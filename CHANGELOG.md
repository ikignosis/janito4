# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.32.0...HEAD)

### Added

- Overall-use accounting (issue #72): every completed LLM turn that reports
  token usage is appended as one row to `<config dir>/accounting.db` (a
  SQLite database, default `~/.janito/accounting.db`) recording the working
  directory, a per-process turn ordinal, a UTC timestamp, the provider/model
  and the turn-wide token counters (`input_tokens`, `cached_tokens`,
  `output_tokens`, tool-call rounds included) plus the estimated cost as a
  numeric dollar value. Both the CLI (interactive shell, `/ask`, `/compact`,
  one-shot prompts, via the turn-report wrapper) and the web UI (the
  `stream_prompt` loop) feed the log; it is best-effort and never raises.
  A new `get_provider_cost_value()` accessor returns the numeric cost (the
  display path keeps its adaptive format), and the database can be inspected
  with `python -m janito.tooling.accounting` (see `docs/usage/accounting.md`).

- `--set system-prompt="..."` and `--set system-prompt-file=path` config keys
  (issue #60): the configured text/file becomes the system prompt's `start`
  section, replacing the built-in base prompt while `skills`, `agents.md` and
  plugin sections stay unchanged. `system-prompt-file` wins when both are
  set; `-S`/`--system-prompt` still overrides the config for a run and
  `-Z`/`--no-system-prompt` disables it. The start section is applied
  per-`effective_system_prompt()` call (never mutating the shared
  `SYSTEM_PROMPT_MANAGER`, so web-mode sessions stay isolated) and the
  display paths (`--show-system-prompt`, shell `/prompt`) show it through the
  same config-aware resolver.
- When the `system-prompt-file` config key is set, janito validates that the
  file exists — both when the value is set (`janito --set
  system-prompt-file=...` rejects a missing file with exit code 1) and at
  startup (CLI chat, single prompt or web mode) — failing with an actionable
  error (exit code 1) naming the key and path instead of surfacing a bare
  error deep inside the system-prompt render.
- `/notools <message>` shell command: send a prompt through the **main**
  conversation history while offering the model no tools for that message
  only (the per-message equivalent of `--no-tools`); the next prompt goes
  back to the session's default tool configuration.
- `/rx <question>` shell command: send a prompt through the **main**
  conversation history while restricting `tools=` to the read and execute
  (`"r"`/`"x"` permission) built-in tools — the model can read/search/fetch
  and run commands but cannot write or modify anything (issue #63).
- The interactive shell now shows a rich horizontal rule labeled with the
  upcoming conversation turn (`Turn N`) right above the prompt (issue #69),
  so the turn number is visible *before* each submission instead of in the
  trailing token-usage summary.

### Changed

- Restructure the API layer around an immutable per-session `APIConfig`
  (issue #70): a new `janito/openai_client/api_config.py` defines the frozen
  `APIConfig` dataclass (provider, api type, model, endpoint, api key,
  resolved max-output/input tokens, reasoning level, `preserve_thinking`,
  `use_mcp`, `verbose`, `stream_runner`, `observer`) and `build_api_config` —
  the **single resolution point** that hoists `resolve_runtime_config`, the
  `load_*`/`get_default_*` token & reasoning reads, `preserve_thinking` and
  `get_active_provider`. The five `send_prompt` entry points
  (`completions_api`, `conversations_api`, `anthropic_api`, `dashscope_api`,
  `gemini_api`) became thin `send_prompt(config, prompt, *, ...)` wrappers and
  `Client.send` now reads everything from `config` — the turn pipeline makes
  no config-store / auth-store reads and is a pure function of
  `(config, request)`. The CLI composition point (`cli/chat.py`) builds the
  config once per session / `/provider` switch (injecting the TUI stream
  runner and Rich observer) and `_make_send_prompt_func` became a single
  `{api_type: client}` dispatch with one union-signature closure; the
  interactive shell no longer forwards `verbose` per call (it stays a session
  default, with an optional per-call override on `Client.send` used by
  `/ask` and `/compact`). `thinking` stayed a per-call flag at that point
  (resolved against the static provider registry; it moved onto the config
  in the next entry). Old `send_prompt` signatures are broken
  (project convention: no backward compatibility); tests build a config via
  the new `tests/conftest.py::make_config` helper and `tests/test_api_config.py`
  pins the builder.
- Thinking mode moved onto the `APIConfig`: `thinking` is no longer a
  per-call argument of the five `send_prompt` entry points or `Client.send`.
  `build_api_config` now resolves it at build time (`--thinking` /
  `/thinking` flag, else the provider's static built-in default — a `True`
  flag or a pass-through dict such as MiniMax-M3's `{'type': 'adaptive'}`)
  into `config.thinking`, so the pipeline performs no static-registry reads
  at all (`_resolve_model_settings` became a pure passthrough of config
  values and the `get_default_thinking_from_provider` calls inside the
  clients were removed). The shell's `/thinking` toggle now takes effect by
  rebuilding the send function through the session's send factory
  (`thinking_override`), the same cheap rebuild `/provider` and `/model`
  perform, and `/provider` / `/model` preserve the runtime toggle across
  switches; `/ask` and `/compact` no longer forward a `thinking` flag (the
  config carries it). CLI semantics are unchanged: `-t` forces thinking on,
  a falsy flag leaves it to the provider's built-in default.
- The `Cost:` estimate in the end-of-turn usage summary and the `/price`
  table is now rendered with an adaptive, magnitude-aware format (issue
  #67) instead of six fixed decimals: sub-cent costs show as `0.abc¢`,
  sub-dollar costs as `X.a¢`, costs under 100$ as `X.a$` and larger
  ones as rounded integer dollars `X$`. Values that round across a unit
  boundary are promoted to the next unit (e.g. `99.96$` -> `100$`,
  `0.009999$` -> `1.0¢`), and DeepSeek's rate-band annotation
  (`(off-peak)`/`(peak)`) is preserved. `get_provider_cost()` now returns
  the adaptive string, so all display paths (turn summary, `/price`)
  pick it up automatically; `/price` sorting still parses the numeric
  value (both `$` and `¢` forms).
- Renamed the per-turn history bookmarks from `Checkpoint`/`checkpoint` to
  `Turn`/`turn` throughout the code and user-facing strings (issue #65):
  `history_checkpoints` → `history_turns`, `conversation_checkpoint` →
  `conversation_turn`, `response_checkpoint` → `response_turn`,
  `mirrored_checkpoint` → `mirrored_turn`, `KEEP_CHECKPOINTS` →
  `KEEP_TURNS`; the `/history` markers now render as `◎ turn N` and
  `/rewind` reports "History is already at the last turn.".
- `/help` now shows a short description right after every registered command
  and splits the prompt tool modes into their own rich table: `/read`
  (read-only tools), `/rx` (read + execute tools), `/write` (write-only
  tools) and `/notools` (no tools) (issue #66).
- `ReadMultipleFiles` tool: removed the `max_lines` parameter — files are now
  always read in full. For partial reads use `ReadFile` with
  `start_line`/`max_lines`.
- `ReadMultipleFiles` tool: when only some files succeed, the result message
  now also reports how many failed
  (`Read X/Y files successfully, Z failed.`).
- Extract the per-round stream runner out of the API clients
  (`openai_client/completions_api.py`) into `openai_client/client_support.py`
  (issue #61). The runner — thread creation, the Rich spinner and the
  Enter-to-cancel detection (`_run_with_progress_bar` + `_is_enter_pressed`)
  — is now a UI-side concern **injected** through `Client(stream_runner=...)`:
  with the default `None`, `send_prompt`/`Client.send` call each streaming
  round directly (no thread, no spinner, no Enter-to-cancel), keeping them
  purely API-side and reusable in non-TUI contexts. The CLI wires in the TUI
  runner for every entry point through `_make_send_prompt_func`
  (`cli/chat.py`), so the shell, `/ask`, `/compact` and one-shot prompts keep
  the exact same spinner/Enter-to-cancel behaviour (the runner is invoked per
  round, so the spinner is never shown during tool execution).
  `RequestCancelled` moved to `client_support` (still re-exported from
  `completions_api`); tests now inject a fake runner via the constructor
  instead of monkeypatching a module global.
- Extract every user-visible turn event into a pluggable **turn observer**
  (`janito/agent/observer.py`): `Client.send` and the five `send_prompt`
  modules (Completions, Responses, Anthropic, DashScope, Gemini) now route
  the reasoning/message fragments, the verbose call/response dumps and the
  error explainers through an injected `TurnObserver` instead of printing
  directly. The default is the headless `NullObserver`, so
  `send_prompt`/`Client.send` produce no terminal output (the web loop
  already emits structured events); the CLI injects the `RichTurnObserver`
  through `_make_send_prompt_func` (`cli/chat.py`), keeping the rendered
  output byte-for-byte. The end-of-turn report is delivered to the same
  observer by `wrap_send_prompt_with_turn_report` (which knows the
  display-only turn number). Error explainers are dispatched by an explicit
  `error_kind`: the OpenAI SDK clients pass `"not_found"` / `"auth"` from
  their typed `except` blocks, and the native-SDK clients derive it via the
  new `_classify_error` helper in `client_support` (the observer holds no
  message-matching heuristics). The per-client `_handle_not_found_error`
  explainers were merged into one unified helper in `client_support`, and
  the now-dead copies in the helpers modules were removed.
- Removed the `Turn: #N` part from the end-of-turn token-usage summary, and
  the summary no longer shows the `{label}: {message_count}` part either
  (e.g. `Responses: 1` / `Messages: N`) -- it now runs from the token and
  cost parts only (`Total:` / `In:` / `Out:` / `Cached:` / `Cost:`), with
  the message count still reported on the `INFO` log line (issue #68).
  The conversation-turn number now lives only in the shell's pre-prompt rule
  (see the Added entry above), so the display-only `turn` kwarg was dropped
  from `wrap_send_prompt_with_turn_report`, `TurnObserver.on_turn_complete`,
  `display_turn_usage` and `_display_usage`, and from the `turn=1` /
  `turn=self.turn_count` call sites in `run_single_prompt`, the interactive
  shell and `/ask`.

### Fixed

- `/status` shows a **Model** row with the session's effective model
  (`--model`, `/model` or the startup resolution); the provider's built-in
  default is marked `(default)`. Model-scoped settings (API type, max output
  tokens, reasoning level, thinking, Responses-in-server) are now resolved
  for that model instead of silently using the provider's built-in default —
  previously an Alibaba variant running e.g. `qwen3.8-flash` showed no model
  at all and reported the default model's settings.
- `scripts/promote_changelog.py`: write a single trailing newline even when
  `[Unreleased]` is the last section of the file (post-release-reset state);
  the missing newline made pre-commit's `end-of-file-fixer` rewrite the file
  and abort the release commit.

Changes since `v4.32.0` (2026-08-28).
