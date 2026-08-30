"""
Shared agent-loop pipeline for the API client modules.

The five clients (``completions_api``, ``conversations_api``,
``anthropic_api``, ``dashscope_api`` and ``gemini_api``) each implemented the
same ~300-line turn pipeline: clear the changes log, reset the used-files
tracker, resolve the runtime config, create the SDK client, load MCP tools,
build the :class:`~janito.tooling.executor.ToolExecutor`, resolve the model
settings, then loop *stream -> display -> tool calls -> finalize*.

This module extracts that pipeline into a :class:`Client` base class as a
template method (:meth:`run_turn`).  Subclasses implement the API-specific hooks;
the module-level ``run_turn`` functions remain as thin wrappers that
construct the subclass with a resolved
:class:`~janito.llm_clients.api_config.APIConfig` and call :meth:`run_turn`.

Config is resolved **once** at the composition point by
:func:`~janito.llm_clients.api_config.build_api_config` and handed to the
client as an immutable :class:`APIConfig` (issue #70): ``Client.run_turn`` is a
pure function of ``(api_config, request)`` and never reads the config store /
auth store / provider registry itself.  The thinking mode (``--thinking`` /
``/thinking`` flag against the provider's *static* built-in default) is
resolved into ``api_config.thinking`` at build time, so no resolution is left
inside the pipeline.

Test-coupling note
------------------
The tests monkeypatch module-level names in each client module
(``OpenAI``, ``ToolExecutor``, ``get_all_tool_schemas``, ...).  A function's
globals are looked up in the module it is *defined* in, so every hook that
can be monkeypatched must resolve through the **subclass module's** global
namespace at call time.  That is why each subclass implements its hooks as
thin forwarders to its own module's globals instead of the base importing
those names directly (e.g. ``CompletionsClient._resolve_tools`` calls the
``get_all_tool_schemas`` global of ``completions_api``).

The per-round stream runner is the one hook that is **not** resolved through
module globals: it is a UI-side concern (the TUI progress bar +
Enter-to-cancel detection) injected through the ``UIConfig``
(``stream_runner``), so ``run_turn``/``Client.run_turn`` stay purely
API-side and tests inject a fake runner via the UI config instead of
monkeypatching a module global.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from janito.agent.observer import NullObserver, TurnObserver
from janito.agent.usage import TokenStats
from janito.tooling.changes import clear_changes
from janito.tooling.executor import extract_tool_names
from janito.tooling.used_files import reset_used_files

from .api_config import APIConfig
from .client_support import _load_mcp

# Configure logger for this module
logger = logging.getLogger(__name__)


class UIConfig(Protocol):
    """Injected UI behaviour bundle (stream runner + turn observer).

    Structural protocol the pipeline depends on: the concrete frozen
    dataclass lives in :mod:`janito.ui.config` and is composed by the CLI at
    the composition point, so the API clients stay UI-free (issue #90).
    """

    stream_runner: Callable | None
    observer: TurnObserver


@dataclass(frozen=True)
class _HeadlessUIConfig:
    """Default UI behaviour when no config is injected (headless).

    Mirrors the headless defaults of :class:`janito.ui.config.UIConfig`:
    each streaming round runs directly in the calling thread (no runner) and
    every observer event is dropped by the ``NullObserver``.
    """

    stream_runner: Callable | None = None
    observer: TurnObserver = NullObserver()


_DEFAULT_UI_CONFIG = _HeadlessUIConfig()


def _fold_turn_usage(
    turn_stats: TokenStats | None, usage_info: Any
) -> TokenStats | None:
    """Fold one round's usage into the turn-level cumulative totals.

    Tool-call rounds would otherwise be lost when the round state is
    discarded; ``TokenStats`` keeps the final round's counters and sums
    last_input/last_cached/last_output across every round of the turn
    (mirrors the web agent loop's ``_fold_turn_usage``).
    """
    if usage_info is None:
        return turn_stats
    if turn_stats is None:
        return TokenStats.from_usage(usage_info)
    turn_stats.add_round(usage_info)
    return turn_stats


class Client:
    """Shared agent-loop pipeline for a single API backend.

    Subclasses implement the API-specific hooks; :meth:`run_turn` runs the common
    turn pipeline (template method).  The class is stateless across turns: the
    per-call values (SDK client, conversation state) are locals of
    :meth:`run_turn` and are threaded into the hooks explicitly, so a single
    client instance can be reused for many prompts.

    Attributes:
        api_config: The resolved, immutable
            :class:`~janito.llm_clients.api_config.APIConfig` for this
            session (provider, model, endpoint, api_key, token limits,
            reasoning level, preserve_thinking, use_mcp).
        ui_config: The injected, immutable
            :class:`~janito.ui.config.UIConfig` for this session (per-round
            stream runner + turn observer); defaults to a headless config.
        observer: Convenience alias for ``ui_config.observer`` (a
            :class:`~janito.agent.observer.TurnObserver`); kept so subclass
            hooks keep working unchanged.
        stream_runner: Convenience alias for ``ui_config.stream_runner`` (the
            per-round stream runner, ``None`` = headless).
        api_type: Canonical API type name (e.g. ``"Completions"``).
        backend_default: Fallback backend label for verbose output when
            ``base_url`` is ``None``.
    """

    #: Canonical API type name (e.g. ``"Completions"``, ``"Responses"``).
    api_type: str = "Completions"

    #: Fallback backend label shown in verbose mode when ``base_url`` is None.
    backend_default: str = "api.openai.com"

    def __init__(
        self, api_config: APIConfig, ui_config: UIConfig | None = None
    ) -> None:
        self.api_config = api_config
        # Convenience aliases (unchanged attribute names for the hooks).
        ui_config = ui_config or _DEFAULT_UI_CONFIG
        self.ui_config = ui_config
        self.observer = ui_config.observer
        self.stream_runner = ui_config.stream_runner

    # ------------------------------------------------------------------
    # Template method: the shared turn pipeline
    # ------------------------------------------------------------------

    def run_turn(
        self,
        prompt: str,
        *,
        verbose: bool = False,
        previous_messages: list[dict[str, Any]] | None = None,
        previous_response_id: str | None = None,
        previous_items: list[dict[str, Any]] | None = None,
        instructions: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Run one full turn: setup, stream loop, tool calls, finalize.

        The conversation-context parameters (``previous_messages``,
        ``previous_response_id``, ``previous_items``, ``instructions``) are
        the union of the concrete per-API signatures; each subclass's
        :meth:`_init_conversation_state` picks the ones it needs and ignores
        the rest:

          - Completions / Anthropic / DashScope / Gemini: the conversation
            history is owned client-side (``previous_messages`` mutated in
            place, ``instructions`` folded in); ``previous_response_id`` /
            ``previous_items`` are ignored.
          - Responses: ``previous_response_id`` (server-side providers) or
            ``previous_items`` (stateless providers, e.g. DeepSeek) chain the
            conversation; ``previous_messages`` is ignored.

        ``verbose`` is an explicit per-call emission gate for the verbose
        call/response dumps (the CLI's session default is captured in its
        turn closure); pass ``True``/``False`` to control it for one call.

        Thinking mode is resolved into ``api_config.thinking`` at build time
        (``--thinking`` / ``/thinking`` flag against the provider's built-in
        default); it is not a per-call argument.

        The end-of-turn report (used files + token-usage summary) is
        delivered by this method itself: it builds a
        :class:`~janito.agent.usage.TokenStats`, folds every round's usage
        into it (tool-call rounds included) and hands it -- together with the
        turn's resolved :class:`~janito.llm_clients.api_config.APIConfig`,
        whose provider / model / max tokens feed the report -- to the
        injected observer's ``on_turn_complete`` when the turn finishes.
        There is no caller-supplied out-param (see
        :class:`~janito.agent.observer.TurnObserver`).  The conversation
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

        base_url, api_key, model = (
            self.api_config.base_url,
            self.api_config.api_key,
            self.api_config.model,
        )
        client = self._create_sdk_client(base_url, api_key)
        logger.debug(f"{type(self).__name__} client created with base_url={base_url}")

        # Initialize MCP manager and load services if enabled; the tool
        # executor routes tool calls to the MCP manager or the built-in
        # registry and tracks usage/used-files/changes around each call.
        mcp_manager, mcp_tools = _load_mcp(self.api_config.use_mcp)
        tool_executor = self._create_tool_executor(mcp_manager)
        tools_schemas = self._resolve_tools(tools, mcp_tools)

        # Execution-time privilege gate (issue #87): the registry is complete
        # (all tools load regardless of -r/-w/-x), so the session restriction
        # is enforced here against the tools actually offered in this turn --
        # the model may only call tools whose schemas were passed above.
        tool_executor.allowed_tools = extract_tool_names(tools_schemas)

        logger.debug(f"Using {len(tools_schemas)} tools total")

        provider = self.api_config.provider
        (
            thinking,
            max_output_tokens,
            max_input_tokens,
            reasoning_effort,
        ) = self._resolve_model_settings(provider, model)
        preserve_thinking = self.api_config.preserve_thinking
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
        state = self._init_conversation_state(
            prompt,
            provider,
            model,
            previous_messages=previous_messages,
            previous_response_id=previous_response_id,
            previous_items=previous_items,
            instructions=instructions,
        )

        # Per-turn usage accumulator: folds every round (tool-call rounds
        # included) into a TokenStats for the end-of-turn report (issue #82).
        # The report's provider / model / max tokens come from self.api_config,
        # handed to the observer alongside the stats when the turn finishes.
        turn_stats: TokenStats | None = None

        while True:
            # Build the base call parameters for one round.
            call_kwargs = self._build_call_kwargs(
                model,
                state,
                max_output_tokens,
                reasoning_effort,
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
            turn_stats = _fold_turn_usage(turn_stats, usage_info)

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

            # No more tool calls, return the final response.  The end-of-turn
            # report is delivered to the injected observer here, at the end
            # of the turn, like every other observer event: the observer's
            # ``on_turn_complete`` renders the usage summary and records the
            # overall-use accounting row (see RichTurnObserver).
            return self._finish_turn(full_content, reasoning_content, state, turn_stats)

    def _finish_turn(self, full_content, reasoning_content, state, turn_stats):
        """Finalize the turn and deliver the end-of-turn report.

        Runs the concrete client's :meth:`_finalize` hook and then hands the
        populated client-owned :class:`~janito.agent.usage.TokenStats`,
        together with the turn's resolved ``self.api_config`` (provider / model /
        max tokens), to the injected observer's ``on_turn_complete`` (which
        renders the usage summary and records the overall-use accounting
        row).  The report is delivered on every turn.
        """
        result = self._finalize(full_content, reasoning_content, state)
        self.observer.on_turn_complete(turn_stats, self.api_config)
        return result

    # ------------------------------------------------------------------
    # Shared helpers (base implementation; not monkeypatched by tests)
    # ------------------------------------------------------------------

    def _invoke_stream_runner(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Run the per-round worker through the injected stream runner.

        ``stream_runner`` is a UI-side hook (e.g. the CLI's ``_run_with_progress_bar``
        Rich spinner + Enter-to-cancel runner) carried by ``ui_config.stream_runner``.
        With the default ``None`` ``func`` is called directly in the calling
        thread -- no thread, no UI -- so the client stays purely API-side.
        When a runner is injected it owns the worker's execution (it creates
        the ``cancel_event`` and injects it into ``func`` as a keyword
        argument, which is why the stream consumers accept ``cancel_event``).
        """
        if self.stream_runner is not None:
            return self.stream_runner(func, *args, **kwargs)
        return func(*args, **kwargs)

    # ------------------------------------------------------------------
    # Hooks every subclass must implement (forwarding to its module globals)
    # ------------------------------------------------------------------

    def _create_sdk_client(self, base_url, api_key):
        """Create the SDK client (module-global forwarder, e.g. ``OpenAI``)."""
        raise NotImplementedError

    def _create_tool_executor(self, mcp_manager):
        """Create the ToolExecutor (module-global forwarder)."""
        raise NotImplementedError

    def _resolve_tools(self, tools, mcp_tools):
        """Resolve the tool schemas (built-in + MCP), API-specific format."""
        raise NotImplementedError

    def _resolve_model_settings(self, provider, model):
        """Resolve ``(thinking, max_output_tokens, max_input_tokens, reasoning_effort)``.

        All four come straight from the resolved ``self.api_config`` (issue #70):
        ``thinking`` is resolved at build time by ``build_api_config`` (the
        ``--thinking`` / ``/thinking`` flag against the provider's *static*
        built-in default -- a ``True`` flag or a pass-through dict such as
        MiniMax-M3's ``{'type': 'adaptive'}``), and the token limits /
        reasoning level are the resolved config values.  The config store /
        provider registry is never read here.
        """
        raise NotImplementedError

    def _init_conversation_state(
        self,
        prompt,
        provider,
        model,
        *,
        previous_messages=None,
        previous_response_id=None,
        previous_items=None,
        instructions=None,
    ):
        """Build the per-turn conversation state from the context params.

        The keyword-only parameters are the union of the concrete per-API
        signatures; each subclass picks the ones it needs (e.g. Completions
        uses only ``previous_messages``; Responses uses
        ``previous_response_id`` / ``previous_items`` / ``instructions``).
        """
        raise NotImplementedError

    def _build_call_kwargs(
        self,
        model,
        state,
        max_output_tokens,
        reasoning_effort,
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
    ):
        """Record the final assistant message and return the result.

        The token counters were already folded onto a
        :class:`~janito.agent.usage.TokenStats` by :meth:`run_turn`, and the
        report is delivered to the observer's ``on_turn_complete`` right
        after this hook returns -- this hook no longer carries any usage
        display metadata (message count / label are gone; provider / model /
        max tokens come from the resolved ``APIConfig`` that ``run_turn``
        passes to the observer alongside the stats).
        """
        raise NotImplementedError


__all__ = [
    "Client",
]
