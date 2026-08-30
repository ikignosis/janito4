"""Injected, immutable per-session UI configuration (composition-point injection).

The turn pipeline is purely API-side: every user-visible effect of a turn is
routed through an injected :class:`~janito.agent.observer.TurnObserver`, and
the per-round blocking work runs through an injected stream runner.  Those
two objects travel together as a frozen :class:`UIConfig`, built once per
session (or per provider/model/thinking switch) at the composition point
(``cli/chat.py``'s ``_make_turn_factory``) and handed to the client
alongside the resolved
:class:`~janito.llm_clients.api_config.APIConfig`.

``verbose`` deliberately lives **outside** this structure: it is a per-call
emission gate (``Client.run_turn(verbose=...)``) -- a boolean, not an
injected object -- so the session default is captured in the CLI turn
closure instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from janito.agent.observer import NullObserver, TurnObserver


@dataclass(frozen=True)
class UIConfig:
    """Injected per-session UI behaviour (stream runner + turn observer).

    Frozen -- built once per session at the composition point and never
    mutated; per-call variance is handled by per-call args (``verbose`` on
    ``Client.run_turn``), never by mutating this config.

    Attributes:
        stream_runner: The per-round stream runner (a UI-side concern, e.g.
            the TUI ``_run_with_progress_bar``); ``None`` = headless (each
            streaming round runs directly in the calling thread).
        observer: The turn observer (a
            :class:`~janito.agent.observer.TurnObserver`); defaults to the
            headless :class:`~janito.agent.observer.NullObserver`.
    """

    stream_runner: Callable | None = None
    observer: TurnObserver = NullObserver()


__all__ = ["UIConfig"]
