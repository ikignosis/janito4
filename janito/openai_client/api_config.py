"""Resolved, immutable per-session API configuration (issue #70).

Everything a turn needs that can be resolved *before* the call starts.
Built once per session (or per provider/model switch) by
``build_api_config``; never mutated afterwards.  The turn pipeline and the
five ``run_turn`` entry points consume it instead of re-reading the
config store / auth store / provider registry at call time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from janito.agent.observer import NullObserver, TurnObserver


@dataclass(frozen=True)
class APIConfig:
    """Resolved, immutable per-session API configuration.

    Frozen -- the whole point is that the pipeline can't mutate session
    config; per-call variance is handled by per-call args (``usage_out``,
    the conversation-context kwargs) or by rebuilding the config (cheap, and
    exactly what happens on a provider/model or ``/thinking`` switch).

    Attributes:
        provider: The effective provider name.
        api_type: The canonical API type (``"Completions"``, ``"Responses"``,
            ``"Anthropic"``, ``"DashScope"``, ``"Gemini"``).
        model: The effective model name.
        base_url: The resolved API base URL (``None`` = the standard OpenAI
            endpoint).
        api_key: The API key from the auth store.
        max_output_tokens: Resolved max output tokens (never ``None``: falls
            back to the built-in default, then to 100_000).
        max_input_tokens: Resolved max input tokens (``None`` = unknown
            context window; the usage display omits the total).
        reasoning_level: Resolved reasoning depth (``None`` = the API's own
            default applies).
        thinking: The resolved thinking mode for the session: the explicit
            ``--thinking`` / ``/thinking`` flag when set, otherwise the
            provider's built-in default (``True``, a pass-through dict such
            as MiniMax-M3's ``{'type': 'adaptive'}``, or ``False``).  A falsy
            value means the flag was not forced on; the resolved value is
            what gets sent to the API.
        preserve_thinking: The ``preserve_thinking`` config value (may be
            ``None``).
        use_mcp: Whether to load and use MCP tools.
        verbose: Session default for verbose output (per-call ``verbose`` on
            :meth:`Client.run_turn` may still override it).
        stream_runner: The per-round stream runner (a UI-side concern, e.g.
            the TUI ``_run_with_progress_bar``); ``None`` = headless (each
            streaming round runs directly in the calling thread).
        observer: The turn observer (a
            :class:`~janito.agent.observer.TurnObserver`); defaults to the
            headless :class:`~janito.agent.observer.NullObserver`.
    """

    # --- Identity / endpoint (from resolve_runtime_config) ---
    provider: str
    api_type: str  # "Completions" | "Responses" | "Anthropic" | "DashScope" | "Gemini"
    model: str
    base_url: str | None  # None = standard OpenAI endpoint
    api_key: str

    # --- Resolved model settings (config override -> built-in default) ---
    max_output_tokens: int  # never None: falls back to 100_000
    max_input_tokens: int | None
    reasoning_level: str | None  # None = API's own default applies
    thinking: bool | dict | None  # resolved: --thinking / /thinking flag or provider built-in default
    preserve_thinking: Any  # config value; may be None
    use_mcp: bool

    # --- UI / observability (composition-point injection) ---
    verbose: bool
    stream_runner: Callable | None  # the TUI progress-bar runner; None = headless
    observer: TurnObserver  # NullObserver by default


def build_api_config(
    *,
    api_type: str,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    reasoning_level: str | None = None,
    thinking: bool | None = None,
    use_mcp: bool = True,
    verbose: bool = False,
    stream_runner: Callable | None = None,
    observer: TurnObserver | None = None,
) -> APIConfig:
    """Resolve everything a turn needs into an immutable APIConfig.

    The ONLY place that touches the config store / auth store / provider
    registry (issue #70): the turn pipeline becomes a pure function of its
    inputs.  ``thinking`` is resolved here too: the explicit
    ``--thinking`` / ``/thinking`` flag wins, otherwise the provider's
    static built-in default applies (a ``True`` flag or a pass-through dict
    such as MiniMax-M3's ``{'type': 'adaptive'}``).  The shell's ``/thinking``
    toggle flips it mid-session by rebuilding the config through the
    ``turn_factory`` -- the same cheap rebuild that a provider/model
    switch performs.

    Args:
        api_type: The canonical API type (``"Completions"``, ``"Responses"``,
            ``"Anthropic"``, ``"DashScope"``, ``"Gemini"``).  Also selects the
            built-in default endpoint for providers that declare
            ``endpoint_by_api_type`` (native-SDK types resolve their native
            base URL, e.g. the Anthropic / DashScope / Gemini SDK endpoints).
        cli_provider: Provider passed via ``--provider`` (may be ``None``).
        cli_model: Model passed via ``--model`` (may be ``None``).
        reasoning_level: Reasoning depth passed via ``--reasoning-level``
            (may be ``None``).
        thinking: The ``--thinking`` CLI flag / shell ``/thinking`` override
            (may be ``None``).  ``True`` forces thinking on; ``False`` (or
            ``None``) leaves it to the provider's built-in default.
        use_mcp: Whether to load and use MCP tools (default ``True``).
        verbose: Session default for verbose output (may be overridden
            per call on :meth:`Client.run_turn`).
        stream_runner: The per-round stream runner injected into the client
            (``None`` = headless).
        observer: The turn observer injected into the client (``None`` =
            headless :class:`~janito.agent.observer.NullObserver`).

    Returns:
        A fully resolved, frozen :class:`APIConfig`.

    Raises:
        ValueError: If the API key, model or endpoint cannot be resolved
            (propagated from ``resolve_runtime_config``).
    """
    # Lazy imports avoid a cycle: completions_api imports APIConfig.
    from janito.config_loaders import (
        load_max_input_tokens,
        load_max_output_tokens,
        load_reasoning_level,
    )
    from janito.config_store import get_config_value
    from janito.general_config import get_active_provider
    from janito.openai_client.completions_api import resolve_runtime_config
    from janito.provider_accessors import (
        get_default_max_input_tokens_from_provider,
        get_default_max_output_tokens_from_provider,
        get_default_reasoning_level_from_provider,
        get_default_thinking_from_provider,
    )

    provider = cli_provider or get_active_provider()
    base_url, api_key, model = resolve_runtime_config(
        cli_model, cli_provider, cli_api_type=api_type
    )

    max_output_tokens = (
        load_max_output_tokens(provider, model)
        or get_default_max_output_tokens_from_provider(provider, model)
        or 100_000
    )
    max_input_tokens = load_max_input_tokens(
        provider, model
    ) or get_default_max_input_tokens_from_provider(provider, model)
    reasoning_level = (
        reasoning_level
        or load_reasoning_level(provider, model)
        or get_default_reasoning_level_from_provider(provider, model)
    )
    thinking = thinking or get_default_thinking_from_provider(provider, model)

    return APIConfig(
        provider=provider,
        api_type=api_type,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_output_tokens=max_output_tokens,
        max_input_tokens=max_input_tokens,
        reasoning_level=reasoning_level,
        thinking=thinking,
        preserve_thinking=get_config_value("preserve_thinking"),
        use_mcp=use_mcp,
        verbose=verbose,
        stream_runner=stream_runner,
        observer=observer or NullObserver(),
    )


__all__ = [
    "APIConfig",
    "build_api_config",
]
