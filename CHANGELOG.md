# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.32.0...HEAD)

### Added

- `--set used-files=True` config key (issue #74): a flat boolean flag that
  controls whether the end-of-turn `Used files` report (the list of files the
  tools read/wrote during the prompt) is printed by the CLI/shell. Defaults to
  `False` when unset, so the report is opt-in; when enabled it is rendered
  right before the token-usage summary for every turn (interactive shell,
  one-shot prompts, `/ask`, `/compact`). Accepts `true`/`false`/`1`/`0`/
  `yes`/`no`/`on`/`off` in any case and tolerates hand-written string forms.
- Overall-use accounting (issue #72): every completed LLM turn that reports
  token usage is appended as one row to `<config dir>/accounting.db` (a
  SQLite database, default `~/.janito/accounting.db`) recording the working
  directory, a UTC timestamp, the provider/model
  and the turn-wide token counters (`input_tokens`, `cached_tokens`,
  `output_tokens`, tool-call rounds included) plus the estimated cost as a
  numeric dollar value. Both the CLI (interactive shell, `/ask`, `/compact`,
  one-shot prompts; the observer's `on_turn_complete` records the row) and
  the web UI (the
  `stream_prompt` loop) feed the log; it is best-effort and never raises.
  A new `get_provider_cost_value()` accessor returns the numeric cost (the
  display path keeps its adaptive format), and the database can be inspected
  with `python -m janito.tooling.accounting` (see `docs/usage/accounting.md`).
  On every startup the database is pruned of entries older than 10 days
  (issue #76) so it does not grow unbounded — best-effort, never raises.
- `/use_stats` shell command (issue #75): reads the accounting database,
  groups the rows **by calendar day** and prints the **last 10 days** as a
  rich table — one row per day with the summed input/cached/output tokens
  and the summed estimated cost (`N/A` when no cost was reported). The
  cached-token value is followed by the percentage of the day's total input
  tokens that was served from cache (the `input_tokens` column already
  includes the cached tokens), e.g. `600 (25%)`.
  Backed by a new best-effort `accounting.get_daily_stats(days=10)` accessor
  that aggregates per-day totals (tokens default to 0, cost stays `None`
  when unknown) and returns only the most recent days that have recorded
  usage, oldest first. The command is auto-discovered by `/help` and the
  completer, and the empty state prints a friendly message plus the
  database path.
- `/use_stats` now also prints a **Per Model Statistics (last 10 days)**
  table (issue #75): the same period broken down **by day, provider and
  model**, with one row per day/provider/model group summing the
  input/cached/output tokens and the estimated cost (same cached-percentage
  formatting and `N/A` cost fallback as the daily table; unknown
  provider/model values are grouped and rendered as `unknown`). Backed by a
  new best-effort `accounting.get_per_model_stats(days=10)` accessor that
  aggregates per day/provider/model (tokens default to 0, cost stays `None`
  when unknown) and returns only the most recent days that have recorded
  usage, ordered oldest day first then provider/model.

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
- `/rw <question>` and `/rwx <question>` shell commands (issue #84): like
  `/rx`, they send a prompt through the **main** conversation history while
  restricting `tools=` to a permission subset of the built-in tools — `/rw`
  offers the read + write tools (permissions `"r"`, `"w"` and `"rw"`, e.g.
  `move_file`/`replace_text_in_file`; no execute) and `/rwx` offers the
  read + write + execute tools (every built-in tool that declares a
  permission). Both mirror the subset semantics of the `-r`/`-w`/`-x`
  privilege model and, like the other restricted modes, exclude tools that
  declare no permission (skills, MCP).
- The interactive shell now shows a rich horizontal rule labeled with the
  upcoming conversation turn (`Turn N`) right above the prompt (issue #69),
  so the turn number is visible *before* each submission instead of in the
  trailing token-usage summary.
- `/read`, `/write`, `/rx`, `/rw` and `/rwx` now override the runtime
  `-r`/`-w`/`-x` privilege restrictions for a single turn (issue #87): the
  tool registry loads **every** tool whose `should_load()` gate passes, and
  the session privileges are applied by a new *session tool selector*
  (`get_session_tool_schemas` / `get_session_tool_names`) instead of at
  discovery time, so under `janito -r`, `/write <msg>` still offers the
  write-only tools and `/rwx` the full toolset. The shell prints a one-line
  `Note:` when a turn overrides the session privileges; `/tools` and the web
  tools panel list the loaded-but-restricted tools separately. A new
  execution-time gate (`allowed_tools` on `run_tool` / `ToolExecutor`, fed
  from the schemas actually offered in the turn) rejects calls to any tool
  that was not offered, with a structured error, so the model can only call
  what the current turn advertised.

### Changed

- The end-of-turn report (`on_turn_complete`) is now delivered by
  `Client.run_turn` itself at the end of the turn, like every other observer
  event, instead of by the CLI's `wrap_turn_with_report` wrapper: `run_turn`
  hands the populated `TurnUsage` out-param to the injected observer's
  `on_turn_complete` when the turn finishes. The overall-use accounting row
  (`_record_accounting`, the `accounting.db` write) moved into the observer
  too -- the CLI's `RichTurnObserver.on_turn_complete` records it before
  rendering the used-files + token-usage summary -- so neither the API
  clients nor the CLI carry end-of-turn bookkeeping. The wrapper and its
  `display_turn_report` suppression flag were removed; `_make_turn_func` in
  `cli/chat.py` now just creates the `TurnUsage` out-param per call (the
  suppression use case is expressed by injecting a headless observer).
- Dropped the backward-compatibility re-exports from the client API modules
  (project convention: no backward compatibility — the repo controls all
  callers): the five `openai_client` modules (`completions_api`,
  `conversations_api`, `anthropic_api`, `dashscope_api`, `gemini_api`) no
  longer re-export stream/helper functions from their canonical homes. The
  stream consumers (`_consume_stream`, `_consume_response_stream`,
  `_handle_*`, ...) are imported from `janito.openai_client.*_stream`, the
  shared client helpers (`RequestCancelled`, `_display_usage`, `_load_mcp`,
  `format_tokens`) from `janito.openai_client.client_support` / the
  `janito.agent.usage` / `janito.agent` layers, and `janito.openai_client`
  now imports `RequestCancelled` from `client_support` directly.
- Removed the web-layer re-export shims: `janito.web.backend.events` and
  `janito.web.backend.agent.call` are deleted (callers import from
  `janito.agent.events` and `janito.agent.completions` instead, and the
  `StreamAccumulator` alias is gone). The per-API web runner modules
  (`janito.web.backend.agent.{responses,anthropic,dashscope,gemini}`) now
  keep only the web-only glue (`create_client`, `stream_turn_events`);
  `loop.py` builds call kwargs and accumulators straight from the shared
  `janito.agent` adapters through a small `_Runner` dataclass instead of
  module re-exports.

- Renamed the reasoning-depth concept to **reasoning effort** and consolidated
  the built-in provider config onto a single `default_reasoning_effort` key
  (issue #77): the model entries in `janito/providers/<name>/config.py`
  declare `default_reasoning_effort` / `supported_reasoning_efforts` only --
  the old `reasoning_level` / `default_effort_level` builtin keys and the
  `get_default_effort_level_from_provider` alias are gone. All accessors,
  config-store keys, the `APIConfig` field and the API-call kwargs are now
  `reasoning_effort` (matching the API field), and the user-facing flag is
  `--reasoning-effort` with the model-scoped config key `reasoning-effort`
  (breaking rename of the previous `--reasoning-level` / `reasoning-level`
  key). The template, google (`medium`), moonshot (`max`) and alibaba
  (`low`) configs now declare the consolidated keys.
- The built-in reasoning-effort default is now the **lowest supported**
  level for the OpenAI GPT and Alibaba Qwen models (instead of the API's
  own default): the `gpt-5.6-*` models now declare configurable reasoning
  (`low`/`medium`/`high`, default `low`), `qwen3.8-max`'s default dropped
  from `xhigh` to `low`, and `qwen3.8-flash` now also declares reasoning
  levels (`low`/`medium`/`xhigh`, default `low`).
- The built-in base system prompt moved from a code constant to the packaged
  resource `janito/system-prompt.txt` (issue #73): it is installed as package
  data (`[tool.setuptools.package-data]`) and read lazily from the resource
  location each time the default prompt is resolved
  (`default_system_prompt_manager()` / `get_builtin_system_prompt()`), so
  importing `janito` never embeds or reads the prompt text.
- The `alibaba` provider's built-in default model is now `qwen3.8-flash`
  instead of `qwen3.8-max` (issue #59): `janito --provider alibaba` without
  an explicit model resolves to the fast, cost-effective flash model. The
  flagship `qwen3.8-max` remains a built-in model (its configurable
  reasoning levels `low`/`medium`/`xhigh`, built-in tools and multimodal
  DashScope endpoint are unchanged) and can still be selected explicitly
  with `--set model=qwen3.8-max`. Both Qwen models keep configurable
  reasoning depth (`reasoning_effort`, levels `low`/`medium`/`xhigh`).
- Renamed the final-round token counters on `TokenStats`
  (`janito.agent.usage`) and the web `UsageEvent` from `input`/`output`/
  `cached` to `last_input`/`last_output`/`last_cached` to make explicit that
  they mirror the **last** request of the turn (the one that produced the
  final answer), as opposed to the `turn_*` cumulative counters. This is a
  breaking change to the WebSocket `usage` event wire format: `to_dict()`
  now emits `last_input`/`last_output`/`last_cached` instead of
  `input`/`output`/`cached` (the in-repo frontend `chatEvents.js` and the
  `chat_messages`/`status_bar` templates were updated in lockstep; usage is
  never persisted, so no migration is needed). The `normalize_usage()` dict
  keys and the `total`/`max_tokens`/`turn_*` fields are unchanged.
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
- Renamed the LLM-turn entry points from `send_prompt` to `run_turn`
  (dropping the `llm_` prefix) throughout the codebase to match the
  established "turn" vocabulary (the web loop's `_run_turn` /
  `_run_prompt_turn`, the observer's `on_turn_complete`, the shell's
  `conversation_turn` bookkeeping): the five module-level
  `send_prompt(config, prompt, *, ...)` functions became
  `run_turn(config, prompt, *, ...)`, `Client.send` became
  `Client.run_turn`, `wrap_send_prompt_with_turn_report` became
  `wrap_turn_with_report`, `_make_send_prompt_func` became
  `_make_turn_func`, `_make_send_factory` / `shell.send_factory` became
  `_make_turn_factory` / `shell.turn_factory`, the shell's
  `send_prompt_func` attribute became `turn_func` and its `_send_prompt`
  became `_run_turn`. The `janito.openai_client.send_prompt_responses`
  alias was dropped (project convention: no backward compatibility).
  `web/backend/prompts.py`'s `_send_prompt` (a WebSocket frame sender, a
  different concept) is intentionally unchanged.

### Removed

- Removed the `turn_count` column from the overall-use accounting database
  (issue #80): `accounting.db` rows no longer carry the per-process turn
  ordinal (it reset on every process start and was never used by `/use_stats`
  or the aggregations). The schema, `record_turn` signatures, `get_records`
  output, the `python -m janito.tooling.accounting` inspector output and the
  docs no longer reference it. No migration is performed -- the old database
  files are removed by the user (project convention: no backward
  compatibility).

### Fixed

- `/use_stats` cached-token percentage no longer double-counts the cached
  tokens: the accounting database stores `input_tokens` as the **total**
  input (the API reports `prompt_tokens`/`input_tokens` with the cached
  tokens counted inside them, and the provider cost modules bill
  `input - cached` at the miss rate), so the percentage is now
  `cached / input` instead of `cached / (input + cached)`. A day where ~99%
  of the input was served from cache previously showed `50%`; it now shows
  `99%` (issue #75). When no input was reported the plain cached count is
  still shown without a percentage.
- The interactive shell's pre-prompt `Turn N` rule no longer counts turns
  that are rolled back (issue #78): the turn number is now derived from the
  recorded turn list (`history_turns`, one entry per submitted turn), so a
  turn interrupted by Ctrl+C, failed by an unexpected error, or undone with
  `/rewind` no longer counts (its recorded start is dropped together with
  the rollback) and the same number is shown again for the retry.
  Enter-cancelled turns (`RequestCancelled`) keep their count, matching
  their no-rollback semantics. The redundant `turn_count` attribute was
  removed — the recorded turns are the single source of truth (the `/compact`
  reset of `history_turns` also restarts the counter, keeping it consistent
  with the `/history` markers).
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
