# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.36.0...HEAD)

Changes since `v4.36.0` (2026-09-01).

### Added

- `ListTasks` tool (issue #101): a blocking-free snapshot of every task known
  to the manager -- running *and* finished -- so the model can discover what
  has been started (including tasks orphaned by a mid-turn rollback, where
  the `StartTask` call is lost from the conversation history) and how each
  one ended, without waiting on anything the way `WaitForTask` does. Each
  entry carries the task id, one-line summary, state (`running` while alive,
  then `finished` / `timeout` / `stopped` / `killed` / `error`), pid, working
  directory and duration; running tasks are listed first (in start order).
  An optional `running_only` flag restricts the result to the tasks still in
  flight. Standalone check: `python -m janito.tools.tasks.list_tasks [--json]`.
- End-of-turn notice in the interactive shell (issue #101): after each turn
  that leaves parallel tasks running, janito prints "The following (n) tasks
  are still running:" with a `Task ID | Summary` table, whether the turn
  finished normally or was rolled back.
- Ctrl+C confirm-quit flow in the interactive shell (issue #101): when tasks
  are running, the quit confirmation shows the running-tasks notice and asks
  "Do you want to exit and terminate all tasks?"; answering "y" (or pressing
  Ctrl+C again at the prompt) kills them immediately. Without running tasks
  the original "Do you want to quit the conversation?" prompt is shown.

### Fixed

- Privilege-override note in the interactive shell (issue #109): the
  ``/read`` ``/write`` ``/rx`` ``/rw`` ``/rwx`` commands printed a hardcoded
  ``Note: this turn runs with privileges (-r/-w/-x)`` regardless of the
  command used -- e.g. ``/rx`` (read + execute only) wrongly claimed write
  privileges. The note now renders the flags the command actually grants:
  ``/rx`` prints ``(-r/-x)``, ``/read`` ``(-r)``, ``/write`` ``(-w)``,
  ``/rw`` ``(-r/-w)`` and ``/rwx`` ``(-r/-w/-x)``. Covered by new cases in
  ``tests/test_privileges.py``.

### Changed

- Token-usage summary line style (issue #105): the end-of-turn
  ``=== Time | In | Out | Cached | Cost ===`` line is now rendered with a
  **dark green** background (``bright_white on dark_green``, xterm-256 color
  22) instead of magenta. Also pinned by a new ``tests/test_usage_line_style.py``
  covering both render paths (``_display_usage`` and ``display_turn_usage``).
- `TaskManager.cleanup()` (the `atexit` hook, issue #101) now delegates
  killing to the new `TaskManager.kill_all()`, which sends **SIGKILL
  immediately** instead of the previous SIGTERM: at interpreter exit there is
  no event loop left to service a clean shutdown, so the SIGTERM grace period
  only delayed the inevitable force-kill. The same `kill_all()` backs the
  shell's confirm-quit path, so tasks are terminated on every exit path.
