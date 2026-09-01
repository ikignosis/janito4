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
- `StartTask` gained a required `summary` argument: a one-line, human-readable
  summary of the task that is presented to the user (StartTask's progress line
  and WaitForTask's "Waiting for task n/total : <summary>" lines).  The summary
  is stored on the `Task` record, returned by `TaskManager.start_task()` and
  `wait_for_task()`, and echoed back in `StartTask`'s result.
- `WaitForTask` now returns each finished task's captured `stdout`/`stderr`
  content inline in its result dict (capped at `max_lines` lines, default 200,
  `max_lines=None` for the full output; a stream cut short is flagged with
  `stdout_truncated`/`stderr_truncated`), so the task's results can be checked
  directly without reading the temp output files.  The temp files still hold
  the complete, untruncated output.
- `StartTask` gained an optional `timeout` argument (seconds): a per-task
  lifetime cap armed at spawn.  When the deadline passes the task's child
  process is terminated (SIGTERM, then SIGKILL) by its own wait thread -- even
  if nobody is waiting on it -- and the task is recorded as having
  `exit_reason` `"timeout"`.  This is distinct from `WaitForTask`'s own
  `timeout`, which only bounds how long that call blocks and kills nothing.
- `WaitForTask` now reports each task's *exit status* rather than a bare
  return code: every result carries `exit_reason` (`"finished"`, `"timeout"`,
  `"stopped"`, `"killed"` or `"error"`), `exit_code` (the child's own status,
  or `None` when it was killed and produced none), `timeout` and
  `duration_seconds`, and the top-level result adds `terminated_task_ids`.  A
  task's completion line and the final summary now reflect this (e.g.
  `"task abc finished (exit 0, 41.2s)"` vs. `"task def TIMED OUT after 120s
  (killed, no exit code)"`).  Callers should read `exit_reason`, not
  `returncode`, to tell success from termination -- a child that traps SIGTERM
  can exit cleanly (even `0`) during the termination grace period.

### Changed

- Task manager: child task processes now always start with `--no-tasks` on
  the command line, so a task sub-process cannot spawn further tasks itself
  (prevents recursive task execution).
- `WaitForTask`: each waited task is now announced up front with its one-line
  summary ("Waiting for task n/total : <summary>") instead of a single
  "Waiting for N task(s)" line; `StartTask`'s start line now ends with a
  newline instead of `end=""`.
- `/price` table: split the combined cost column into per-type columns
  (`1M in` | `1M cache` | `1M output` | `Total`).  Each component is computed
  separately and `Total` is the exact dollar sum of the three (no longer the
  old combined 1M cache-hit + 1M output estimate).
