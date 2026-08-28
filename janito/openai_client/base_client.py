"""
Shared agent-loop pipeline for the API client modules.

The four clients (``completions_api``, ``conversations_api``,
``anthropic_api`` and ``dashscope_api``) each implemented the same ~300-line
turn pipeline: clear the changes log, reset the used-files tracker, resolve
the runtime config, create the SDK client, load MCP tools, build the
:class:`~janito.tooling.executor.ToolExecutor`, resolve the model settings,
then loop *stream -> display -> tool calls -> finalize*.

This module extracts that pipeline into a :class:`Client` base class as a
template method (:meth:`send`).  Subclasses implement the API-specific hooks;
the module-level ``send_prompt`` functions remain as thin wrappers that
construct the subclass and call :meth:`send`, so existing call sites (the
interactive shell, ``cli/chat.py`` and the tests) are unaffected.

Test-coupling note
------------------
The tests monkeypatch module-level names in each client module
(``resolve_runtime_config``, ``OpenAI``, ``ToolExecutor``,
``get_all_tool_schemas``, ...).  A function's globals are
looked up in the module it is *defined* in, so every hook that can be
monkeypatched must resolve through the **subclass module's** global
namespace at call time.  That is why each subclass implements its hooks as
thin forwarders to its own module's globals instead of the base importing
those names directly (e.g. ``CompletionsClient._resolve_runtime_config``
calls the ``resolve_runtime_config`` global of ``completions_api``).

The per-round stream runner is the one hook that is **not** resolved through
module globals: it is a UI-side concern (the TUI progress bar +
Enter-to-cancel detection) injected through the constructor
(``Client(stream_runner=...)``), so ``send_prompt``/``Client.send`` stay
purely API-side and tests inject a fake runner via the constructor instead of
monkeypatching a module global.
"""

import logging
from collections.abc import Callable
from typing import Any

from janito.agent.observer import NullObserver, TurnObserver
from janito.agent.usage import TokenStats
from janito.config_store import get_config_value
from janito.general_config import get_active_provider
from janito.tooling.changes import clear_changes
from janito.tooling.used_files import reset_used_files

from .client_support import _display_usage, _load_mcp

# Configure logger for this module
logger = logging.getLogger(__name__)


def _fold_turn_usage(
    turn_stats: TokenStats | None, usage_info: Any
) -> TokenStats | None:
    """Fold one round's usage into the turn-level cumulative totals.

    Tool-call rounds would otherwise be lost when the round state is
    discarded; ``TokenStats`` keeps the final round's counters and sums
    input/cached/output across every round of the turn (mirrors the web
    agent loop's ``_fold_turn_usage``).
    """
    if usage_info is None:
        return turn_stats
    if turn_stats is None:
        return TokenStats.from_usage(usage_info)
    turn_stats.add_round(usage_info)
    return turn_stats


class Client:
    """Shared agent-loop pipeline for a single API backend.

    Subclasses implement the API-specific hooks; :meth:`send` runs the common
    turn pipeline (template method).  The class is stateless across turns: the
    per-call values (SDK client, resolved config, conversation state) are
    locals of :meth:`send` and are threaded into the hooks explicitly, so a
    single client instance can be reused for many prompts.

    Attributes:
        cli_model: Model passed via ``--model`` (overrides the provider's config).
        cli_provider: Provider passed via ``--provider`` (overrides config/auth).
        reasoning_level: Reasoning depth passed via ``--reasoning-level``.
        use_mcp: Whether to load and use MCP tools.
        stream_runner: Optional per-round stream runner (a UI-side concern,
            e.g. the TUI ``_run_with_progress_bar``). ``None`` (default)
            calls each streaming round directly in the calling thread -- no
            thread, no progress spinner, no Enter-to-cancel -- keeping the
            client purely API-side. The CLI injects its runner here.
        observer: Optional UI observer (a
            :class:`~janito.agent.observer.TurnObserver`) receiving every
            user-visible event of the turn (reasoning/message fragments,
            verbose dumps, error explainers). ``None`` (default) resolves to
            the headless :class:`~janito.agent.observer.NullObserver`, so
            the client produces no terminal output; the CLI injects the Rich
            observer through ``_make_send_prompt_func``.
        api_type: Canonical API type name (e.g. ``"Completions"``).
        backend_default: Fallback backend label for verbose output when
            ``base_url`` is ``None``.
    """

    #: Canonical API type name (e.g. ``"Completions"``, ``"Responses"``).
    api_type: str = "Completions"

    #: Fallback backend label shown in verbose mode when ``base_url`` is None.
    backend_default: str = "api.openai.com"

    def __init__(
        self,
        cli_model: str | None = None,
        cli_provider: str | None = None,
        reasoning_level: str | None = None,
        use_mcp: bool = True,
        stream_runner: Callable | None = None,
        observer: TurnObserver | None = None,
    ) -> None:
        self.cli_model = cli_model
        self.cli_provider = cli_provider
        self.reasoning_level = reasoning_level
        self.use_mcp = use_mcp
        self.stream_runner = stream_runner
        self.observer = observer or NullObserver()

    # ------------------------------------------------------------------
    # Template method: the shared turn pipeline
    # ------------------------------------------------------------------

    def send(
        self,
        prompt: str,
        *,
        verbose: bool = False,
        tools: list[dict[str, Any]] | None = None,
        thinking: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Run one full turn: setup, stream loop, tool calls, finalize.

        ``kwargs`` carries the conversation-context parameters of the concrete
        ``send_prompt`` signature (e.g. ``previous_messages``,
        ``previous_response_id``, ``previous_items``, ``instructions``); each
        subclass's :meth:`_init_conversation_state` picks the ones it needs.
        The optional ``usage_out`` kwarg (a
        :class:`~janito.openai_client.client_support.TurnUsage`) is populated
        with the turn's usage and display metadata so the caller can render
        the end-of-turn reports after ``send`` returns.  The conversation
        turn number is never passed here: it is display-only caller
        knowledge, supplied directly to the renderer by the caller.

        Returns:
            The API-specific turn result: the assistant text (``str``) for the
            stateless clients, or a ``ConversationResult`` for the Responses
            client.
        """
        # Reset per-prompt tracking so ./janito/changes.jsonl and the
        # "Used files" report only describe the current prompt.
        clear_changes()
        reset_used_files()

        # Out-param for the post-call turn report (see TurnUsage): populated
        # as the rounds stream so the caller can render the usage summary
        # after send() returns instead of inside the _finalize hooks.
        usage_out = kwargs.pop("usage_out", None)

        base_url, api_key, model = self._resolve_runtime_config()
        client = self._create_sdk_client(base_url, api_key)
        logger.debug(f"{type(self).__name__} client created with base_url={base_url}")

        # Initialize MCP manager and load services if enabled; the tool
        # executor routes tool calls to the MCP manager or the built-in
        # registry and tracks usage/used-files/changes around each call.
        mcp_manager, mcp_tools = _load_mcp(self.use_mcp)
        tool_executor = self._create_tool_executor(mcp_manager)
        tools_schemas = self._resolve_tools(tools, mcp_tools)

        logger.debug(f"Using {len(tools_schemas)} tools total")

        provider = self._active_provider()
        (
            thinking,
            max_output_tokens,
            max_input_tokens,
            reasoning_level,
        ) = self._resolve_model_settings(
            provider, model, thinking, self.reasoning_level
        )
        preserve_thinking = self._get_config("preserve_thinking")
        if preserve_thinking is not None:
            logger.debug(f"Using preserve_thinking from config: {preserve_thinking}")

        # Print model and backend info only in verbose mode
        if verbose:
            self.observer.on_verbose_info(
                base_url=base_url,
                model=model,
                mcp_manager=mcp_manager,
                backend_default=self.backend_default,
            )

        # Conversation-state model depends on the client (client-owned
        # messages list vs server-side response id vs client-side items).
        state = self._init_conversation_state(prompt, provider, model, **kwargs)

        # Per-turn usage accumulator: folds every round (tool-call rounds
        # included) into a TokenStats so the caller can render the summary
        # after send() returns (see TurnUsage).  Metadata that can only be
        # resolved here is recorded on the out-param up front.
        turn_stats: TokenStats | None = None
        if usage_out is not None:
            usage_out.provider = provider
            usage_out.model = model
            usage_out.max_input_tokens = max_input_tokens
            usage_out.max_output_tokens = max_output_tokens

        while True:
            # Build the base call parameters for one round.
            call_kwargs = self._build_call_kwargs(
                model,
                state,
                max_output_tokens,
                reasoning_level,
                preserve_thinking,
                thinking,
            )

            # In verbose mode, show the request that is about to be sent
            # (messages/input truncated to their tail, tools by name).
            if verbose:
                self.observer.on_verbose_call(call_kwargs, tools_schemas)

            # Consume the full stream through the injected per-round stream
            # runner (the CLI's TUI runner runs the blocking work in a worker
            # thread while the main thread drives the spinner; the default
            # ``None`` calls it directly -- no thread, no UI).
            (
                full_content,
                reasoning_content,
                tool_calls,
                usage_info,
                raw_attrs,
            ) = self._run_stream_round(
                client,
                call_kwargs,
                tools_schemas,
                state,
                base_url=base_url,
                api_key=api_key,
                model=model,
            )

            # In verbose mode, show a compact summary of the response.  The
            # server-side response id (Responses client) is extracted from the
            # API-specific state dict; the other clients keep their history as
            # a ``messages`` list, from which nothing extra is shown.
            if verbose:
                response_id = None
                if isinstance(state, dict):
                    response_id = state.get("response_id")
                self.observer.on_verbose_response(
                    full_content,
                    reasoning_content,
                    tool_calls,
                    usage_info,
                    response_id,
                    raw_attrs=raw_attrs,
                )

            logger.debug("API streaming response completed")
            self.observer.on_reasoning(reasoning_content)

            # Fold this round's usage into the turn totals (the round state
            # is discarded on tool-call rounds, so the usage would otherwise
            # be lost).
            if usage_out is not None:
                turn_stats = _fold_turn_usage(turn_stats, usage_info)
                usage_out.stats = turn_stats

            # Display the assembled response (markdown in the CLI)
            self.observer.on_message(full_content)

            # Check if the model wants to call tools
            if tool_calls:
                # Record the assistant's tool calls, execute every call and
                # append the tool responses to the history, then loop to get
                # the final response after the tool calls.
                state = self._handle_tool_calls(
                    tool_calls, full_content, reasoning_content, state, tool_executor
                )
                continue

            # No more tool calls, return the final response.
            return self._finalize(full_content, reasoning_content, state, usage_out)

    # ------------------------------------------------------------------
    # Shared helpers (base implementation; not monkeypatched by tests)
    # ------------------------------------------------------------------

    def _active_provider(self) -> str:
        """The provider in effect: ``--provider`` or the configured default."""
        return self.cli_provider or get_active_provider()

    def _get_config(self, key: str) -> Any:
        """Read a config value (e.g. ``preserve_thinking``)."""
        return get_config_value(key)

    def _invoke_stream_runner(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Run the per-round worker through the injected stream runner.

        ``stream_runner`` is a UI-side hook (e.g. the CLI's ``_run_with_progress_bar``
        Rich spinner + Enter-to-cancel runner).  With the default ``None``
        ``func`` is called directly in the calling thread -- no thread, no
        UI -- so the client stays purely API-side.  When a runner is injected
        it owns the worker's execution (it creates the ``cancel_event`` and
        injects it into ``func`` as a keyword argument, which is why the
        stream consumers accept ``cancel_event``).
        """
        if self.stream_runner is not None:
            return self.stream_runner(func, *args, **kwargs)
        return func(*args, **kwargs)

    # ------------------------------------------------------------------
    # Hooks every subclass must implement (forwarding to its module globals)
    # ------------------------------------------------------------------

    def _resolve_runtime_config(self):
        """Resolve ``(base_url, api_key, model)`` (module-global forwarder)."""
        raise NotImplementedError

    def _create_sdk_client(self, base_url, api_key):
        """Create the SDK client (module-global forwarder, e.g. ``OpenAI``)."""
        raise NotImplementedError

    def _create_tool_executor(self, mcp_manager):
        """Create the ToolExecutor (module-global forwarder)."""
        raise NotImplementedError

    def _resolve_tools(self, tools, mcp_tools):
        """Resolve the tool schemas (built-in + MCP), API-specific format."""
        raise NotImplementedError

    def _resolve_model_settings(self, provider, model, thinking, reasoning_level):
        """Resolve ``(thinking, max_output_tokens, max_input_tokens, reasoning_level)``
        for the resolved ``model``."""
        raise NotImplementedError

    def _init_conversation_state(self, prompt, provider, model, **kwargs):
        """Build the per-turn conversation state from ``**kwargs``."""
        raise NotImplementedError

    def _build_call_kwargs(
        self,
        model,
        state,
        max_output_tokens,
        reasoning_level,
        preserve_thinking,
        thinking,
    ):
        """Build the API call parameters for one round."""
        raise NotImplementedError

    def _run_stream_round(
        self,
        client,
        call_kwargs,
        tools_schemas,
        state,
        *,
        base_url,
        api_key,
        model,
    ):
        """Run one streaming round; return ``(content, reasoning, tool_calls, usage)``.

        Subclasses route the blocking ``_stream_response`` worker through
        :meth:`_invoke_stream_runner` (the injected per-round stream runner;
        ``None`` = direct call) and own the API-specific exception handling:
        error explainers are rendered through ``self.observer.on_error`` and
        the exception is always re-raised.
        """
        raise NotImplementedError

    def _handle_tool_calls(
        self, tool_calls, full_content, reasoning_content, state, tool_executor
    ):
        """Record + execute tool calls; return the updated conversation state."""
        raise NotImplementedError

    def _finalize(
        self,
        full_content,
        reasoning_content,
        state,
        usage_out=None,
    ):
        """Record the final assistant message and return the result.

        ``usage_out`` (a
        :class:`~janito.openai_client.client_support.TurnUsage`, or ``None``)
        receives the display metadata the caller needs to render the
        end-of-turn reports after ``send`` returns (message count / label /
        cached reporting); the token counters were already folded onto
        ``usage_out.stats`` by :meth:`send`.
        """
        raise NotImplementedError


# Re-export the shared display helper used by subclasses' finalizers so the
# module is a single import point for the common client machinery.
__all__ = [
    "Client",
    "_display_usage",
]
