"""Shared pytest fixtures and helpers for the test suite."""

from __future__ import annotations

from typing import Any

import pytest

from janito.llm_adapters.observer import NullObserver
from janito.llm_clients.api_config import APIConfig
from janito.ui.config import UIConfig


@pytest.fixture(autouse=True)
def _reset_browser_prompts_flag():
    """Isolate the mid-turn question surface flag between tests (issue #125).

    ``main(--web)`` enables it globally; without a reset later AskUser gate
    tests see a stale True.
    """
    import janito.tooling.prompting as prompting

    prompting._browser_prompts_enabled = False
    yield
    prompting._browser_prompts_enabled = False


def make_config(
    *,
    provider: str = "openai",
    api_type: str = "Completions",
    model: str = "gpt-5.6-luna",
    base_url: str | None = None,
    api_key: str = "sk-test",
    max_output_tokens: int = 100_000,
    max_input_tokens: int | None = None,
    reasoning_effort: str | None = None,
    thinking: bool = False,
    preserve_thinking: Any = None,
    use_mcp: bool = False,
) -> APIConfig:
    """Build a minimal :class:`APIConfig` for tests that construct clients
    directly (issue #70).

    ``run_turn`` / ``Client`` now take a resolved, immutable
    :class:`~janito.llm_clients.api_config.APIConfig` instead of resolving
    the config/auth stores at call time, so tests that previously passed
    ``cli_model``/``cli_provider``/``use_mcp``/``stream_runner``/``observer``
    to the client constructor build a config with this helper instead.  The
    UI-side stream runner / turn observer go in
    :func:`make_ui_config`.
    """
    return APIConfig(
        provider=provider,
        api_type=api_type,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_output_tokens=max_output_tokens,
        max_input_tokens=max_input_tokens,
        reasoning_effort=reasoning_effort,
        thinking=thinking,
        preserve_thinking=preserve_thinking,
        use_mcp=use_mcp,
    )


def make_ui_config(*, stream_runner=None, observer=None) -> UIConfig:
    """Build a minimal :class:`~janito.ui.config.UIConfig` for tests.

    The per-round stream runner and the turn observer are injected through
    the UI config (no longer constructor params / module globals to
    monkeypatch); ``None`` values fall back to the headless defaults.
    """
    return UIConfig(
        stream_runner=stream_runner,
        observer=observer or NullObserver(),
    )
