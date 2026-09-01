"""Pluggable user-prompt handler for interactive tools (web mode).

Tools that need interactive input (e.g. the AskUser tool) call
``BaseTool.prompt_user``, which by default renders the question in a Rich
table and reads the answer from stdin. In web mode there is no console, so
the web backend installs a *prompt handler* through the context variable
below: the handler presents the question as a modal in the browser and
returns the answer typed by the user.

This mirrors the design of :mod:`janito.tooling.reporter`: a ``ContextVar``
that defaults to ``None`` (console behaviour) and accessor functions the web
backend uses to install/restore a handler for the duration of a turn.
``asyncio.to_thread`` copies the current context into the worker thread that
executes the tool, so a handler installed on the async side is visible to
``prompt_user`` running inside the tool.
"""

from collections.abc import Callable
from contextvars import ContextVar

# A prompt handler receives a question (str) and returns the user's answer
# (str). ``None`` means "no handler installed" -> the CLI stdin fallback in
# BaseTool.prompt_user applies.
PromptHandler = Callable[[str], str]

# Whether the run has a mid-turn question surface (the web UI's in-browser
# question cards, or the interactive shell's stdin prompting). The AskUser
# tool consults this in its ``should_load()`` gate: the flag is declared at
# startup (``janito.__main__._declare_prompt_surface``) for web mode
# (including headless deployments with no TTY stdin) and for the interactive
# shell, and stays off for single-prompt runs (positional or piped), where
# nobody can answer a question raised mid-turn -- the tool is then skipped
# during discovery and never advertised to the model.
_browser_prompts_enabled: bool = False


def enable_browser_prompts() -> None:
    """Declare that questions can be answered in this run.

    Called at startup before tool discovery (and again, idempotently, in
    ``create_app``): the AskUser tool's ``should_load()`` gate then loads it.
    """
    global _browser_prompts_enabled
    _browser_prompts_enabled = True


def browser_prompts_enabled() -> bool:
    """Whether the run has a surface that can answer mid-turn questions."""
    return _browser_prompts_enabled


class browser_prompts:
    """Context manager temporarily declaring browser prompts available.

    Test convenience: scopes the flag to a block and restores the previous
    value afterwards (production code calls :func:`enable_browser_prompts`
    once at startup instead).
    """

    def __enter__(self) -> None:
        global _browser_prompts_enabled
        self._previous = _browser_prompts_enabled
        _browser_prompts_enabled = True

    def __exit__(self, *exc_info) -> None:
        global _browser_prompts_enabled
        _browser_prompts_enabled = self._previous


_prompt_handler: ContextVar[PromptHandler | None] = ContextVar(
    "_prompt_handler", default=None
)


def set_prompt_handler(handler: PromptHandler | None) -> None:
    """Install a prompt handler for the current async context.

    Pass ``None`` to restore the default console-based prompting.
    """
    _prompt_handler.set(handler)


def get_prompt_handler() -> PromptHandler | None:
    """Return the currently installed prompt handler, or ``None``."""
    return _prompt_handler.get()
