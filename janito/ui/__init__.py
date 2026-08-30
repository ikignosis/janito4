"""Terminal (Rich) presentation of the agent turn loop.

The API clients (:mod:`janito.llm_clients`) stay UI-free: every user-visible
effect of a turn is injected at the composition point (``cli/chat.py``) and
rendered from the submodules below:

- :mod:`~janito.ui.observer` -- the CLI's ``RichTurnObserver``.
- :mod:`~janito.ui.stream_runner` -- the per-round ``_run_with_progress_bar``.
- :mod:`~janito.ui.config` -- the concrete frozen ``UIConfig`` bundle the CLI
  composes (stream runner + turn observer).

The API clients never import this package (issue #90): the turn pipeline
depends only on the structural ``UIConfig`` protocol in
``janito.llm_clients.base_client`` (``stream_runner`` + ``observer``); the
concrete :class:`~janito.ui.config.UIConfig` is composed by the CLI.
"""
