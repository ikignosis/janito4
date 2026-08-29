"""
CLI chat execution modes: interactive and single prompt.
"""

import os
from collections.abc import Callable

from .. import __version__
from ..general_config import load_provider_from_config, resolve_api_type
from ..openai_client import (
    APIConfig,
    RequestCancelled,
    build_api_config,
    resolve_runtime_config,
)
from ..openai_client.client_support import (
    RichTurnObserver,
    _run_with_progress_bar,
    wrap_turn_with_report,
)
from ..provider_accessors import get_responses_in_server_from_provider
from ..shell import InteractiveShell
from ..tooling.path_utils import display_path

# Whether the version banner has already been printed for this process, so it
# is shown only once (e.g. before plugin loading in main() and again by the
# full-privileges warning).
_banner_printed = False


def _make_turn_func(config: APIConfig):
    """Return a run-turn callable bound to a resolved APIConfig.

    One closure replaces the previous five per-API-type closures (issue #70):
    the client class is picked from ``config.api_type`` and the union
    signature is kept so the interactive shell can call it identically in all
    modes:

      - Completions / Anthropic / DashScope / Gemini modes: the conversation
        history is owned client-side (``previous_messages`` mutated in place,
        ``instructions`` folded in); ``previous_response_id`` /
        ``previous_items`` are ignored.
      - Responses mode: ``previous_response_id`` (server-side providers) or
        ``previous_items`` (stateless providers, e.g. DeepSeek) chain the
        conversation; ``previous_messages`` is ignored.

    Each backend's ``_init_conversation_state`` already picks what it needs
    from the union kwargs, so there is a single body.

    The returned callable is wrapped with ``wrap_turn_with_report``,
    so it calls the API *and* prints the end-of-turn reports (used files +
    token-usage summary) from the ``usage_out`` out-param the client
    populates; pass ``display_turn_report=False`` to suppress them (e.g.
    internal side calls).

    Args:
        config: The resolved, immutable
            :class:`~janito.openai_client.api_config.APIConfig` for this
            session (provider, model, endpoint, api_key, token limits,
            reasoning level, ``use_mcp`` and the UI-side ``stream_runner`` /
            ``observer``).  Built once per session / provider switch by
            ``build_api_config``; the returned callable performs no
            config-store / auth-store reads.
    """
    from .. import dashscope_api, gemini_api
    from ..openai_client import anthropic_api, completions_api, conversations_api

    _CLIENTS = {
        "Completions": completions_api.CompletionsClient,
        "Responses": conversations_api.ResponsesClient,
        "Anthropic": anthropic_api.AnthropicClient,
        "DashScope": dashscope_api.DashScopeClient,
        "Gemini": gemini_api.GeminiClient,
    }
    try:
        client = _CLIENTS[config.api_type](config)
    except KeyError:
        raise ValueError(f"Unsupported API type: {config.api_type}")

    def run_turn(
        prompt,
        *,
        verbose=None,
        previous_messages=None,
        previous_response_id=None,
        previous_items=None,
        instructions=None,
        tools=None,
        usage_out=None,
    ):
        # ``verbose`` stays a per-call override (design doc §7): ``None``
        # falls back to the session default from the config; /ask forwards
        # the session flag and /compact passes False to suppress the dumps.
        # Thinking mode is resolved into ``config.thinking`` at build time
        # (the shell's /thinking toggle rebuilds the config via the factory).
        return client.run_turn(
            prompt,
            verbose=verbose,
            previous_messages=previous_messages,
            previous_response_id=previous_response_id,
            previous_items=previous_items,
            instructions=instructions,
            tools=tools,
            usage_out=usage_out,
        )

    return wrap_turn_with_report(run_turn, observer=config.observer)


def _make_turn_factory(
    cli_api_type: str | None,
    cli_model: str | None,
    cli_provider: str | None,
    cli_reasoning_level: str | None,
    verbose: bool = False,
    cli_thinking: bool | None = None,
) -> Callable[[str | None, str | None], Callable]:
    """Return a factory that builds the run-turn function for a provider.

    The interactive shell stores the returned factory as ``turn_factory`` and
    ``/provider`` calls it with the new provider, so a provider switch takes
    effect in real time.  For the target provider the factory re-resolves:

      - **model**: an explicit ``/model`` switch (``model_override``) wins;
        otherwise ``--model`` only applies to the provider it was given for
        (the session's startup provider).  After a switch the new provider's
        configured model, else its built-in default, is used (matching the
        toolbar display updated by ``/provider``).
      - **API type**: ``--api-type``, then the model-scoped configured
        value for that provider/model, then the built-in default.

    The resolved model / provider / API type are then handed to
    ``build_api_config`` -- the single resolution point (issue #70) -- so a
    provider switch rebuilds the immutable :class:`APIConfig`, exactly when a
    new one is needed.  Thinking mode is resolved into the config the same
    way: the shell's runtime ``/thinking`` toggle re-invokes the factory with
    ``thinking_override`` set, so the flip takes effect by rebuilding the
    config.  The CLI's TUI stream runner and Rich turn observer are injected
    here (composition point), so every CLI entry point keeps the spinner /
    Enter-to-cancel and rendered output.

    Args:
        cli_api_type: API type passed via ``--api-type`` (may be None).
        cli_model: Model passed via ``--model`` (may be None).
        cli_provider: Provider passed via ``--provider`` (may be None).
        cli_reasoning_level: Reasoning depth passed via ``--reasoning-level``
            (may be None).
        verbose: Session default for verbose output (stored on the config;
            per-call overrides still possible via ``Client.run_turn(verbose=...)``).
        cli_thinking: The ``--thinking`` CLI flag for the session (may be
            None).  ``True`` forces thinking on; ``False``/``None`` leaves it
            to the provider's built-in default.  A ``thinking_override``
            passed to the returned factory wins over this value (the shell's
            runtime ``/thinking`` toggle).

    Returns:
        A callable ``factory(provider, model_override=None, thinking_override=None)
        -> turn_func``.
    """

    def turn_factory(
        provider: str | None,
        model_override: str | None = None,
        thinking_override: bool | None = None,
    ) -> Callable:
        from janito.config_loaders import load_model_from_config
        from janito.provider_accessors import get_default_model_from_provider

        # An explicit /model switch (model_override) always wins.  Otherwise
        # --model applies to the startup provider only; a switched-to
        # provider gets its own effective model (configured, else built-in
        # default).
        if model_override:
            model = model_override
        elif (provider or "").lower() == (cli_provider or "").lower():
            model = cli_model
        else:
            model = load_model_from_config(provider) or get_default_model_from_provider(
                provider
            )
        # Thinking is resolved into the config at build time (issue #70): the
        # shell's runtime /thinking toggle passes thinking_override (the
        # shell's current flag) so a mid-session flip takes effect by
        # rebuilding the config; otherwise the session's --thinking flag
        # applies.
        thinking = thinking_override if thinking_override is not None else cli_thinking
        return _make_turn_func(
            build_api_config(
                api_type=resolve_api_type(cli_api_type, provider, model),
                cli_model=model,
                cli_provider=provider,
                reasoning_level=cli_reasoning_level,
                thinking=thinking,
                verbose=verbose,
                stream_runner=_run_with_progress_bar,
                observer=RichTurnObserver(),
            )
        )

    return turn_factory


def print_version_banner(console=None):
    """Print a banner with the version and the current working directory."""
    global _banner_printed

    from rich.console import Console

    if console is None:
        console = Console()
    console.print(
        f"Janito [cyan]{__version__}[/cyan] - Working at "
        f"[magenta]{display_path(os.getcwd())}[/magenta]"
    )
    _banner_printed = True


def _print_full_privileges_warning(args) -> None:
    """Print a warning banner when running with full privileges."""
    if getattr(args, "full_privileges", False):
        from rich.console import Console

        if not _banner_printed:
            print_version_banner()
        Console().print(
            "WARNING: Running with full privileges, consider using -r, -w, -x",
            style="yellow",
        )


def _enable_requested_toolsets(args) -> None:
    """Enable web-only toolsets when requested via CLI flags."""
    from .session_setup import SessionSetup

    SessionSetup().enable_toolsets()


def _resolve_system_prompt(args) -> tuple[str | None, bool]:
    """Return ``(effective_system_prompt, no_tools)`` for the enabled modes."""
    from .session_setup import SessionSetup

    setup = SessionSetup(
        system_prompt=args.system_prompt,
        no_system_prompt=args.no_system_prompt,
    )
    return setup.effective_system_prompt(), setup.no_tools


def _print_tool_summary(args) -> None:
    """Report the total number of active and skipped tools."""
    from ..tooling.tools_registry import get_all_tools
    from ..tools import get_skipped_tools

    active_tools = get_all_tools()
    skipped_tools = get_skipped_tools()
    print(f"\u2713 {len(active_tools)} tool(s) active, {len(skipped_tools)} skipped")
    if skipped_tools and args.verbose:
        for tool_name, reason in skipped_tools.items():
            print(f"    - {tool_name}: {reason}")


def run_interactive_chat(args):
    """Run the interactive chat session.

    Args:
        args: Parsed command line arguments
    """
    _print_full_privileges_warning(args)
    _enable_requested_toolsets(args)

    # Check if any skills are installed
    from ..tooling.skills_provider import get_skills_provider

    skills = get_skills_provider().list_skills()
    if skills:
        print(f"\u2713 {len(skills)} skill(s) available")

    _print_tool_summary(args)

    # Resolve the model for display (and bind CLI model/provider so every
    # prompt uses the same configuration without environment variables).
    cli_model = getattr(args, "model", None)
    cli_provider = getattr(args, "provider", None)
    cli_reasoning_level = getattr(args, "reasoning_level", None)
    cli_api_type = getattr(args, "api_type", None)
    try:
        _, _, model = resolve_runtime_config(cli_model, cli_provider)
    except ValueError:
        model = cli_model or "(not configured)"
    provider = cli_provider or load_provider_from_config() or "(not configured)"
    try:
        api_type = resolve_api_type(cli_api_type, provider, cli_model)
    except ValueError:
        api_type = cli_api_type or "(not configured)"
    from rich.console import Console

    # Annotate where the conversation state lives: the Responses API keeps
    # it server-side (chained via previous_response_id) unless the
    # responses-in-server ("keep in server") config flips it to stateless,
    # in which case the client re-sends the full history; Completions and
    # other API types always keep history client-side.
    if api_type == "Responses" and provider != "(not configured)":
        responses_in_server = get_responses_in_server_from_provider(provider, model)
        state = "server-side" if responses_in_server else "client-side"
    else:
        state = "client-side"
    Console().print(
        f"Using [cyan]{provider}[/cyan], model [magenta]{model}[/magenta], "
        f"API: [yellow]{api_type}[/yellow] [green]({state})[/green]"
    )
    print(
        "Starting interactive chat session. Type '/exit' or CTRL-D to end the session"
    )

    # Choose system prompt based on enabled modes
    effective_system_prompt, no_tools = _resolve_system_prompt(args)

    shell = InteractiveShell(
        model=model,
        no_history=args.no_history,
        provider=cli_provider,
        api_type=cli_api_type,
    )
    # Factory to (re)build the run-turn function per provider: ``/provider``
    # calls
    # it with the new provider so the switch takes effect in real time
    # (provider, model and API type are re-resolved, see _make_turn_factory).
    # The session's verbose flag is baked into the config at build time
    # (issue #70); the shell keeps its own copy for /status display.
    shell.turn_factory = _make_turn_factory(
        cli_api_type,
        cli_model,
        cli_provider,
        cli_reasoning_level,
        verbose=args.verbose,
        cli_thinking=getattr(args, "thinking", False),
    )
    shell.initialize_history(system_prompt=effective_system_prompt)
    shell.run(
        turn_func=shell.turn_factory(cli_provider),
        verbose=args.verbose,
        no_tools=no_tools,
        thinking=args.thinking,
    )


def _build_single_prompt_context(args):
    """Build ``(messages_history, tools_to_use)`` for a single prompt run."""
    from .session_setup import SessionSetup

    setup = SessionSetup(
        system_prompt=args.system_prompt,
        no_system_prompt=args.no_system_prompt,
    )
    return setup.messages_context(), setup.tools_arg()


def run_single_prompt(args):
    """Run a single prompt.

    Args:
        args: Parsed command line arguments
    """
    import sys

    _print_full_privileges_warning(args)
    _enable_requested_toolsets(args)

    prompt = args.prompt

    if not prompt:
        print("Error: Empty prompt provided.", file=sys.stderr)
        sys.exit(1)

    # Initialize messages history (with or without system prompt based on -Z or -S flag)
    messages_history, tools_to_use = _build_single_prompt_context(args)

    try:
        # Select the API type for the provider: --api-type, then the
        # provider's configured api-type, then its built-in default, and
        # build the resolved per-session APIConfig once (issue #70).  The
        # CLI's TUI stream runner and Rich turn observer are injected at this
        # composition point.
        turn_func = _make_turn_func(
            build_api_config(
                api_type=resolve_api_type(
                    getattr(args, "api_type", None),
                    getattr(args, "provider", None),
                ),
                cli_model=getattr(args, "model", None),
                cli_provider=getattr(args, "provider", None),
                reasoning_level=getattr(args, "reasoning_level", None),
                thinking=getattr(args, "thinking", False),
                verbose=args.verbose,
                stream_runner=_run_with_progress_bar,
                observer=RichTurnObserver(),
            )
        )
        # In Responses mode the system prompt is sent as `instructions` on the
        # first turn (extracted from the seeded history); in Completions mode
        # the same value is carried inside `previous_messages`.
        instructions = None
        if messages_history and messages_history[0].get("role") == "system":
            instructions = messages_history[0].get("content")
        turn_func(
            prompt,
            previous_messages=messages_history,
            instructions=instructions,
            tools=tools_to_use,
        )
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(130)
    except RequestCancelled:
        # Enter was pressed while waiting for the API response.
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(130)
