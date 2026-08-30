"""Terminal (Rich) presentation of the agent turn loop.

The API clients (:mod:`janito.llm_clients`) drive the LLM turn pipeline and
stay UI-free: every user-visible effect of a turn is injected at the
composition point (``cli/chat.py``) and rendered from here:

- :mod:`~janito.ui.observer` -- the CLI's default ``RichTurnObserver``
  (reasoning/content panels, verbose dumps, error explainers and the
  end-of-turn report + overall-use accounting row).
- :mod:`~janito.ui.stream_runner` -- the per-round ``_run_with_progress_bar``
  (Rich spinner, elapsed time and Enter-to-cancel detection).
- :mod:`~janito.ui.display` -- the verbose banners/panels and the reasoning /
  content renderers.
- :mod:`~janito.ui.usage` -- the token-usage summary line and the end-of-turn
  report (used files + usage).
- :mod:`~janito.ui.errors` -- the auth / not-found error explainers.

The public entry points the CLI wires in are ``RichTurnObserver`` (the turn
observer carried by ``UIConfig.observer``) and ``_run_with_progress_bar``
(the per-round stream runner carried by ``UIConfig.stream_runner``).
"""

from .observer import RichTurnObserver
from .stream_runner import _run_with_progress_bar

__all__ = [
    "RichTurnObserver",
    "_run_with_progress_bar",
]
