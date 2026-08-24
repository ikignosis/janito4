# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.29.0...HEAD)

Changes since `v4.29.0` (2026-08-20).

### Added

- Interactive chat now prints the resolved provider, model and API type
  (colorized) before starting the session, annotated with `(server-side)` or
  `(client-side)` depending on the `responses-in-server` ("keep in server")
  config.

- New `/compact` shell command compresses older conversation history: the
  last 3 turns (checkpoints) are kept verbatim and everything before them
  (after the system prompt) is replaced by a single `[RECAP OF PRIOR WORK]`
  assistant message produced by a dedicated LLM call using the Context
  Compression Engine system prompt (strict JSON extraction of goal,
  completed steps, blockers, constraints, code state and open questions).
  The command works in every API mode (Completions/Anthropic/DashScope/
  Gemini client-side history, stateless Responses items and server-side
  Responses conversations) and is disabled with a "Conversation too short to
  compact effectively." warning when there is nothing worth compacting
  (fewer than 3 turns or under 2,000 estimated tokens to replace). Tool-call
  rounds in the compacted zone are sent to the compression call in their
  native format (Completions `tool_calls`/`tool` messages, Responses
  `function_call`/`function_call_output` items) so the provider never
  receives an invalid message role.

### Changed

- DeepSeek cost estimates now apply the peak/off-peak split only on
  weekdays (Monday to Friday in Beijing Time).  On weekends (Saturday and
  Sunday in Beijing Time) the peak-hour divisions no longer apply: every
  call is charged uniformly at the off-peak rate for the whole day.
  Reference requests are unaffected (they keep billing at the peak rates
  regardless of the request time and day).

- Conversation history checkpoints are now kept as a list instead of a
  single value: a checkpoint is recorded every time a new user prompt is
  about to be sent, holding the number of rows `/history` would render at
  that moment (so the value indexes directly into the displayed history in
  every API mode). `/history` shows a numbered marker (`◉ checkpoint N`) on
  the position where each checkpoint was added, and `/rewind` steps back
  one turn at a time through the checkpoint list. The web backend mirrors
  the same list-based checkpoint behaviour for Ctrl+C / error rollback.
