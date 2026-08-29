"""
OpenAI client module for sending prompts to OpenAI-compatible endpoints.
Uses streaming (SSE) to display tokens as they arrive.
"""

import logging
from typing import Any

from openai import AuthenticationError, NotFoundError, OpenAI

# Import auth handling (API keys come from the auth store, not the environment)
from janito.auth_config import get_api_key

# Import general configuration handling
from janito.config_loaders import load_endpoint_from_config, load_model_from_config
from janito.general_config import load_provider_from_config

# Import provider configuration for base URLs and built-in defaults.
from ..provider_accessors import (
    get_default_model_from_provider,
    requires_explicit_model,
)
from ..provider_validation import is_custom_provider

# Import the tool executor (routes tool calls to the MCP manager or the
# built-in registry and tracks usage/used-files/changes around each call)
from ..tooling.executor import ToolExecutor

# Resolved, immutable per-session configuration (issue #70): the turn
# pipeline consumes it instead of re-reading the config/auth stores.
from .api_config import APIConfig

# Shared agent-loop pipeline (see Client.run_turn) implemented by CompletionsClient.
from .base_client import Client

# Shared helpers reused by every client module (token formatting, MCP
# loading, Rich console output, auth-error explainer), the per-round stream
# runner (``_run_with_progress_bar`` + ``RequestCancelled``, injected by the
# CLI) and the Chat Completions stream consumer.  Re-exported here so
# existing ``completions_api.<name>`` references (including tests) keep
# working.
from .client_support import (  # noqa: F401 (re-exported for backward compat)
    RequestCancelled,
    TurnUsage,
    _display_usage,
    _load_mcp,
    format_tokens,
)
from .completions_helpers import _build_call_kwargs, _finalize_response, _resolve_tools
from .completions_stream import (  # noqa: F401 (re-exported for backward compat)
    _consume_chunk,
    _consume_stream,
    _consume_tool_call_delta,
    _stream_response,
)

# Import tools

# Import used-files tracking (best-effort, never fails)


# Configure logger for this module
logger = logging.getLogger(__name__)


def resolve_runtime_config(
    cli_model: str | None = None,
    cli_provider: str | None = None,
    cli_api_type: str | None = None,
) -> tuple[str | None, str, str]:
    """
    Resolve the runtime configuration (base_url, api_key, model) without
    relying on OPENAI_* environment variables.

    Resolution rules:
      - api_key:  taken from the auth store (~/.janito/auth.json) for the
                  active provider (see ``auth_config.get_api_key``).
      - base_url: the endpoint configured for the provider (``--set endpoint``)
                  or, when none is set, the provider's built-in default base
                  URL resolved for the effective API type (see
                  ``provider_accessors.get_endpoint_for_api_type``, honoring the
                  provider's ``endpoint_by_api_type`` map). ``None`` means the
                  standard OpenAI endpoint.
      - model:    ``--model`` (``cli_model``) when given, otherwise the model
                  configured for the active provider (``<provider>.model``),
                  and finally the provider's built-in default model.  A
                  provider whose built-in default is the ``"custom"``
                  placeholder (e.g. ``openrouter``) has no usable default --
                  the placeholder only carries built-in defaults such as the
                  default API type -- so the user must supply the model
                  explicitly (``--model`` or ``<provider>.model``) and an
                  unresolvable model is reported as an error.

    Args:
        cli_model: Model passed via ``--model`` (highest priority). May be None.
        cli_provider: Provider passed via ``--provider``. May be None.
        cli_api_type: API type passed via ``--api-type`` (or implied by the
            selected client, e.g. ``"Anthropic"`` for the native Anthropic
            SDK). Used to pick the built-in default endpoint when the provider
            declares ``endpoint_by_api_type``. May be None.

    Returns:
        Tuple of (base_url, api_key, model). ``base_url`` may be None for the
        standard OpenAI API.

    Raises:
        ValueError: If the API key or model cannot be resolved, or if a custom
            provider has no endpoint configured.
    """
    # Provider: --provider CLI arg, then config.json.  The default provider
    # is stored under the ``provider`` key in config.json -- never in
    # auth.json.  If none of these is set, report that no provider is
    # configured rather than silently assuming "openai".
    provider = cli_provider or load_provider_from_config()
    if not provider:
        logger.error("No provider configured")
        raise ValueError(
            "No provider is configured. "
            "Set one with: janito --set provider=<name> (e.g. janito --set provider=alibaba) "
            "or pass --provider <name>."
        )
    logger.debug(f"Resolving runtime config for provider: {provider}")

    # API key from the auth store (no environment variables).
    api_key = get_api_key(provider)
    if not api_key:
        logger.error(f"No API key configured for provider '{provider}'")
        raise ValueError(
            f"No API key configured for provider '{provider}'. "
            f"Set one with: janito --set-api-key <key> --provider {provider}"
        )

    # Model: --model, then the provider's configured model, and finally the
    # provider's built-in default model (from the provider config).  A
    # provider whose built-in default is the "custom" placeholder (e.g.
    # "openrouter") has no usable default: the placeholder "custom" model
    # entry only carries built-in defaults (the default API type), so the
    # user must supply the model explicitly (--model or <provider>.model in
    # config.json).  When it cannot be resolved, report it here instead of
    # silently sending the placeholder to the API.
    model = cli_model or load_model_from_config(provider)
    if not model:
        model = get_default_model_from_provider(provider)
        if model and requires_explicit_model(provider):
            model = None
    if not model:
        logger.error(f"No model configured for provider '{provider}'")
        raise ValueError(
            f"No model configured for provider '{provider}'. "
            f"Pass --model <name> or set it with: "
            f"janito --provider {provider} --set model=<name>"
        )

    # Base URL: configured endpoint for the provider, otherwise the provider's
    # built-in default resolved for the effective API type (None for standard
    # OpenAI). The effective API type comes from --api-type, then the
    # provider's configured api-type, then its built-in default.
    base_url = load_endpoint_from_config(provider)
    if not base_url:
        if is_custom_provider(provider):
            logger.warning(f"Custom provider '{provider}' has no endpoint configured")
            raise ValueError(
                f"Provider '{provider}' requires an endpoint. "
                f"Set it with: janito --provider {provider} --set endpoint=<url>"
            )
        from ..general_config import resolve_api_type
        from ..provider_accessors import get_endpoint_for_api_type

        api_type = resolve_api_type(cli_api_type, provider)
        base_url = get_endpoint_for_api_type(provider, api_type)

    logger.debug(f"Runtime config resolved: base_url={base_url}, model={model}")
    return base_url, api_key, model


def get_env_config() -> tuple[str | None, str, str]:
    """Backward-compatible alias for :func:`resolve_runtime_config`.

    Retained for external callers; resolves configuration from auth/config
    without using environment variables.
    """
    return resolve_runtime_config()


def run_turn(
    config: APIConfig,
    prompt: str,
    *,
    previous_messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    usage_out: TurnUsage | None = None,
) -> str:
    """Send prompt to OpenAI endpoint and return response using streaming.

    Thin config-driven wrapper (issue #70): all resolved session config
    (provider, model, endpoint, api_key, token limits, reasoning level,
    thinking, preserve_thinking, use_mcp, verbose, stream_runner, observer)
    arrives in ``config`` -- built once per session by ``build_api_config`` --
    so this entry point performs no config-store / auth-store reads of its
    own.

    Args:
        config: The resolved, immutable
            :class:`~janito.openai_client.api_config.APIConfig` for this
            session.
        prompt: The user prompt to send
        previous_messages: List of previous message dicts for conversation
            context (mutated in place).
        tools: Optional list of tool schemas to pass. If None, uses all
            available tools. If an empty list, no tools are passed.
        usage_out: Optional out-param (a
            :class:`~janito.openai_client.client_support.TurnUsage`) populated
            with the turn's usage and display metadata, so the caller can
            render the end-of-turn reports after the call returns (see
            :func:`~janito.openai_client.client_support.display_turn_usage`).

    Note:
        Thinking mode is resolved into ``config.thinking`` at build time: the
        explicit ``--thinking`` / ``/thinking`` flag wins, otherwise the
        provider's built-in default applies (``True`` for DeepSeek and
        Alibaba/Qwen, sent as ``extra_body={'enable_thinking': True}``; a
        pass-through dict such as MiniMax-M3's ``{'type': 'adaptive'}`` is
        sent as ``extra_body={'thinking': {...}}``).
    """
    logger.info("Sending prompt to API")
    return CompletionsClient(config).run_turn(
        prompt,
        previous_messages=previous_messages,
        tools=tools,
        usage_out=usage_out,
    )


class CompletionsClient(Client):
    """Chat Completions client (``client.chat.completions.create``).

    The conversation history is owned **client-side**: the caller-owned
    ``previous_messages`` list is mutated in place (user/assistant turns are
    appended), so the interactive shell's history keeps growing.  Every hook
    forwards to this module's globals so test monkeypatches keep working.
    """

    api_type = "Completions"

    def _create_sdk_client(self, base_url, api_key):
        # base_url can be None for standard OpenAI
        return OpenAI(api_key=api_key, base_url=base_url)

    def _create_tool_executor(self, mcp_manager):
        return ToolExecutor(mcp_manager)

    def _resolve_tools(self, tools, mcp_tools):
        return _resolve_tools(tools, mcp_tools)

    def _resolve_model_settings(self, provider, model):
        # All resolved at build time into the APIConfig (issue #70): thinking
        # (the --thinking / /thinking flag, or the model's built-in default:
        # a True flag or a pass-through dict such as MiniMax-M3's
        # {'type': 'adaptive'}) and the token limits / reasoning level.  The
        # config store / provider registry is never read here.
        return (
            self.config.thinking,
            self.config.max_output_tokens,
            self.config.max_input_tokens,
            self.config.reasoning_effort,
        )

    def _init_conversation_state(self, prompt, provider, model, **kwargs):
        # Use previous messages if provided, otherwise start with the user
        # prompt.  NOTE: check `is not None` (not truthiness). An empty list
        # is a valid, caller-owned history (e.g. after a restart or with
        # --no-system-prompt); using a truthy check would replace it with a
        # new local list and the appended messages would never propagate back
        # to the caller, silently resetting the history on every turn.
        previous_messages = kwargs.get("previous_messages")
        messages = previous_messages if previous_messages is not None else []
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_call_kwargs(
        self,
        model,
        state,
        max_output_tokens,
        reasoning_effort,
        preserve_thinking,
        thinking,
    ):
        # The effective model's built-in tools (e.g. Alibaba/Qwen's
        # code_interpreter / web_search / web_extractor) are resolved for
        # the effective provider and sent as request-body enable_* flags in
        # extra_body (see completions_helpers._build_call_kwargs).  They are
        # resolved for the Completions API type, so API types without
        # built-in tools (e.g. alibaba's qwen3.8-max) send nothing.
        from janito.provider_accessors import get_default_tools_from_provider

        tools = get_default_tools_from_provider(
            self.config.provider, model, api_type="Completions"
        )
        return _build_call_kwargs(
            model,
            state,
            max_output_tokens,
            reasoning_effort,
            preserve_thinking,
            thinking,
            tools,
            provider=self.config.provider,
        )

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
        try:
            (
                full_content,
                reasoning_content,
                tool_calls,
                usage_info,
                raw_attrs,
            ) = self._invoke_stream_runner(
                _stream_response, client, call_kwargs, tools_schemas
            )
        except NotFoundError as e:
            self.observer.on_error(
                e, base_url=base_url, model=model, error_kind="not_found"
            )
            raise
        except AuthenticationError as e:
            self.observer.on_error(
                e,
                provider=self.config.provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                error_kind="auth",
            )
            raise
        return full_content, reasoning_content, tool_calls, usage_info, raw_attrs

    def _handle_tool_calls(
        self, tool_calls, full_content, reasoning_content, state, tool_executor
    ):
        # Build the assistant message (with tool_calls), execute every call
        # and append the tool responses to the history, then loop to get the
        # final response after the tool calls.
        tool_executor.handle_tool_calls(tool_calls, state, full_content)
        return state

    def _finalize(
        self,
        full_content,
        reasoning_content,
        state,
        usage_out=None,
    ):
        return _finalize_response(full_content, reasoning_content, state, usage_out)
