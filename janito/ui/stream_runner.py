"""Per-round stream runner: Rich spinner + Enter-to-cancel (UI-side).

The blocking work of each streaming round -- thread creation, the Rich
spinner and Enter-to-cancel detection -- lives here.  It is a UI-side
concern **injected** by the caller through the ``UIConfig.stream_runner``:
``None`` runs each stream worker directly in the calling thread -- no thread,
no spinner, no Enter-to-cancel -- keeping ``run_turn``/``Client.run_turn``
purely API-side.  ``_make_turn_factory`` in ``cli/chat.py`` wires this runner
in when it builds the ``UIConfig``.
"""

import logging
import sys
import threading

from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

# The control-flow exception the runner raises on Enter-to-cancel lives with
# the clients that catch it (its ``partial_result`` carries the conversation
# state forward), so it stays in the LLM domain.
from ..llm_clients.client_support import RequestCancelled

logger = logging.getLogger(__name__)


def _is_enter_pressed() -> bool:
    """Return True if the user pressed Enter on stdin (non-blocking).

    Only meaningful when stdin is an interactive TTY; returns False for
    piped/redirected input so streamed data is never consumed here.

    POSIX: after prompt_toolkit's prompt ends, the terminal is back in
    canonical mode, so a full line (i.e. an Enter press) becomes available at
    once; ``select`` reports readability and ``readline`` consumes the line.

    Windows: ``msvcrt.kbhit``/``getwch`` report the raw key press.
    """
    if not sys.stdin.isatty():
        return False
    try:
        if sys.platform == "win32":
            import msvcrt

            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\r", "\n"):
                    # Drain any keys buffered after the Enter press.
                    while msvcrt.kbhit():
                        msvcrt.getwch()
                    return True
                return False
            return False
        import select

        if select.select([sys.stdin], [], [], 0)[0]:
            # A full line is available in canonical mode => Enter was pressed.
            sys.stdin.readline()
            return True
        return False
    except Exception:  # noqa: BLE001 - intentional boundary, log/convert and continue
        # Never let input detection break the request flow.
        return False


def _run_with_progress_bar(func, *args, **kwargs):
    """Run a function with a Rich progress bar in a separate thread.

    While the worker runs, stdin is polled non-blockingly for an Enter press:
    if the user presses Enter, the in-flight request is aborted through a
    shared ``cancel_event`` and :class:`RequestCancelled` is raised (an
    interrupt without rolling the conversation history back, unlike Ctrl+C).
    The spinner renders the elapsed waiting time via Rich's
    ``TimeElapsedColumn`` (``0:00:12`` style).

    This is the **UI-side** per-round stream runner injected by the CLI (see
    ``UIConfig.stream_runner``): it creates the ``cancel_event`` and passes
    it to ``func`` as a keyword argument, which is why the stream consumers
    (``_stream_response`` in each client module) accept ``cancel_event``.
    """
    result = [None]
    exception = [None]
    cancel_event = threading.Event()

    def target():
        try:
            result[0] = func(*args, **kwargs, cancel_event=cancel_event)
        except Exception as e:  # noqa: BLE001 - intentional boundary, log/convert and continue
            exception[0] = e

    # Create and start the thread
    thread = threading.Thread(target=target)
    thread.start()

    # Show progress bar while waiting
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task("Waiting for response from the API server...", total=None)
        while thread.is_alive():
            if _is_enter_pressed():
                cancel_event.set()
                break
            progress.update(task, advance=0.1)
            thread.join(timeout=0.1)

    cancelled = cancel_event.is_set()
    if not cancelled:
        thread.join()
    else:
        # Give the worker a moment to honour the cancel (break out of the
        # stream and close the connection); if it is stuck in the initial
        # connect it finishes in the background, mirroring Ctrl+C behaviour.
        thread.join(timeout=2.0)

    if cancelled:
        if exception[0]:
            logger.debug("Worker exception while cancelling request: %s", exception[0])
        exc = RequestCancelled("Request cancelled by user (pressed Enter).")
        # Keep the worker's partial return value (e.g. the aborted response's
        # id) so callers can carry the conversation forward without losing
        # the user's message.
        exc.partial_result = result[0]
        raise exc
    if exception[0]:
        raise exception[0]
    return result[0]
