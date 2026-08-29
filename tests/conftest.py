"""Shared pytest fixtures and helpers for the test suite."""

from __future__ import annotations

from typing import Any

from janito.agent.observer import NullObserver
from janito.openai_client.api_config import APIConfig


def make_config(
    *,
    provider: str = "openai",
    api_type: str = "Completions",
    model: str = "gpt-5.6-luna",
    base_url: str | None = None,
    api_key: str = "sk-test",
    max_output_tokens: int = 100_000,
    max_input_tokens: int | None = None,
    reasoning_level: str | None = None,
    thinking: bool = False,
    preserve_thinking: Any = None,
    use_mcp: bool = False,
    verbose: bool = False,
    stream_runner=None,
    observer=None,
) -> APIConfig:
    """Build a minimal :class:`APIConfig` for tests that construct clients
    directly (issue #70).

    ``send_prompt`` / ``Client`` now take a resolved, immutable
    :class:`~janito.openai_client.api_config.APIConfig` instead of resolving
    the config/auth stores at call time, so tests that previously passed
    ``cli_model``/``cli_provider``/``use_mcp``/``stream_runner``/``observer``
    to the client constructor build a config with this helper instead.
    """
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
        preserve_thinking=preserve_thinking,
        use_mcp=use_mcp,
        verbose=verbose,
        stream_runner=stream_runner,
        observer=observer or NullObserver(),
    )
