"""Terminal (Rich) presentation of the agent turn loop.

The API clients (:mod:`janito.llm_clients`) stay UI-free: every user-visible
effect of a turn is injected at the composition point (``cli/chat.py``) and
rendered from the submodules below.  The public entry points are
``RichTurnObserver`` (:mod:`~janito.ui.observer`) and
``_run_with_progress_bar`` (:mod:`~janito.ui.stream_runner`).
"""
