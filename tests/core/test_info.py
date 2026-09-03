"""
Tests for the --info handler output, in particular the ``Stateless Mode``
line that reflects the resolved ``stateless_mode`` flag.

The line is shown only when the effective API type resolves to ``Responses``:
- server-side providers (e.g. OpenAI) report
  ``server-side (previous_response_id)``
- stateless providers (e.g. DeepSeek) report
  ``stateless (client re-sends history)``
- when the API type resolves to ``Completions`` the line is omitted.

Also covers ``--show-system-prompt`` (``handle_show_system_prompt``), in
particular that the "(with skills)" suffix is only shown when a ``skills``
section is actually present in the default prompt.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import janito.tooling.tools_registry as tools_registry_mod
from janito.cli.handlers.info import (
    handle_info,
    handle_show_config,
    handle_show_system_prompt,
)
from janito.providers.registry import get_provider as _real_get_provider

SKILLS_SECTION = "## Available Skills\n(fake skills section)"

#: Sentinel: a ``_fake_provider`` knob not overridden delegates to the real
#: provider's value.
_MISSING = object()


def _fake_provider(name, *, default_model=_MISSING, default_thinking=False):
    """A Provider-like object for ``name`` with selected lookups overridden.

    ``endpoint_for`` is stubbed to ``None`` and un-overridden methods fall
    back to the real :class:`Provider` (e.g. ``gemini_flavor`` stays real so
    the Google N/A thinking display keeps working).
    """
    real = _real_get_provider(name)

    class _P:
        def default_model(self):
            if default_model is not _MISSING or real is None:
                return default_model
            return real.default_model()

        def default_thinking(self, model=None):
            return default_thinking

        def endpoint_for(self, api_type=None):
            return None

        def gemini_flavor(self):
            return real.gemini_flavor() if real is not None else False

        def stateless_mode(self, model=None):
            return real.stateless_mode(model) if real is not None else True

    return _P()


def _fake_resolve_api_type(cli_api_type, provider, model=None):
    """Deterministic stand-in for resolve_api_type.

    ``--api-type`` is honored; otherwise the API type defaults to Responses
    (matching OpenAI's built-in default).  ``model`` is accepted (ignored)
    for signature parity with the model-aware resolve_api_type.
    """
    if cli_api_type:
        normalized = str(cli_api_type).strip().capitalize()
        return "Responses" if normalized == "Responses" else "Completions"
    return "Responses"


def _run(capsys, provider="openai", api_type=None):
    """Run handle_info with patched config lookups and capture the output."""
    auth_path = MagicMock()
    auth_path.exists.return_value = False
    with (
        patch(
            "janito.cli.handlers.info.load_provider_from_config",
            return_value=None,
        ),
        patch(
            "janito.cli.handlers.info.load_model_from_config",
            return_value=None,
        ),
        patch(
            "janito.cli.handlers.info.get_api_key",
            return_value=None,
        ),
        patch(
            "janito.cli.handlers.info.get_masked_api_key",
            return_value="(not set)",
        ),
        patch(
            "janito.cli.handlers.info.load_endpoint_from_config",
            return_value=None,
        ),
        patch(
            "janito.cli.handlers.info.resolve_api_type",
            side_effect=_fake_resolve_api_type,
        ),
        patch(
            "janito.cli.handlers.info.get_config_path",
            return_value="/tmp/config.json",
        ),
        patch(
            "janito.cli.handlers.info.get_auth_file_path",
            return_value=auth_path,
        ),
    ):
        args = SimpleNamespace(provider=provider, model=None, api_type=api_type)
        handle_info(args)
    return capsys.readouterr().out


def test_stateless_mode_shown_for_server_side_provider(capsys):
    """OpenAI defaults to Responses and keeps state server-side."""
    out = _run(capsys, provider="openai")
    assert "API Type" in out
    assert "Responses" in out
    assert "Stateless Mode" in out
    assert "server-side (previous_response_id)" in out


def test_stateless_mode_stateless_for_deepseek(capsys):
    """DeepSeek's /responses endpoint is stateless."""
    out = _run(capsys, provider="deepseek")
    assert "Responses" in out
    assert "stateless (client re-sends history)" in out


def test_stateless_mode_hidden_when_api_type_completions(capsys):
    """The line is omitted when the API type resolves to Completions."""
    out = _run(capsys, provider="openai", api_type="completions")
    assert "Completions" in out
    assert "Stateless Mode" not in out


def test_stateless_mode_shown_when_api_type_forced_responses(capsys):
    """--api-type responses keeps the line even for a Completions-only provider."""
    out = _run(capsys, provider="minimax", api_type="responses")
    assert "Responses" in out
    assert "server-side (previous_response_id)" in out


def test_thinking_gemini_flavor_shows_na_in_show_config(capsys):
    """Google uses Gemini flavor: thinking in --show-config reports N/A (controlled via Reasoning Effort)."""
    out = _run_show_config(capsys, provider="google")
    assert "Thinking" in out
    assert "N/A (controlled via Reasoning Effort)" in out


def _run_show_config(
    capsys,
    provider="openai",
    cli_model=None,
    config_model=None,
    default_model=None,
):
    """Run handle_show_config with patched config lookups and capture output."""
    with (
        patch(
            "janito.cli.handlers.info.load_provider_from_config",
            return_value=provider,
        ),
        patch(
            "janito.cli.handlers.info.load_model_from_config",
            return_value=config_model,
        ),
        patch(
            "janito.cli.handlers.info.get_provider",
            return_value=_fake_provider(provider, default_model=default_model),
        ),
        patch(
            "janito.cli.handlers.info.get_api_key",
            return_value=None,
        ),
        patch(
            "janito.cli.handlers.info.get_masked_api_key",
            return_value="(not set)",
        ),
        patch(
            "janito.cli.handlers.info.load_endpoint_from_config",
            return_value=None,
        ),
        patch(
            "janito.cli.handlers.info.resolve_api_type",
            side_effect=_fake_resolve_api_type,
        ),
        patch(
            "janito.cli.handlers.info.is_custom_provider",
            return_value=False,
        ),
    ):
        args = SimpleNamespace(provider=None, model=cli_model, api_type=None)
        handle_show_config(args)
    return capsys.readouterr().out


def test_show_config_uses_provider_default_model_when_unset(capsys):
    """No explicit model -> the provider's built-in default model is shown."""
    out = _run_show_config(
        capsys, provider="deepseek", default_model="deepseek-v4-flash"
    )
    assert "deepseek-v4-flash (deepseek default)" in out


def test_show_config_uses_configured_model(capsys):
    """A model set in config.json takes precedence over the provider default."""
    out = _run_show_config(
        capsys,
        provider="deepseek",
        config_model="my-model",
        default_model="deepseek-v4-flash",
    )
    assert "my-model (deepseek.model)" in out


def test_show_config_cli_model_overrides_config(capsys):
    """--model takes precedence over both config and the provider default."""
    out = _run_show_config(
        capsys,
        provider="deepseek",
        cli_model="gpt-x",
        config_model="my-model",
        default_model="deepseek-v4-flash",
    )
    assert "gpt-x (CLI argument)" in out


def test_show_config_no_default_model(capsys):
    """A provider without a default model still reports (not configured)."""
    out = _run_show_config(capsys, provider="custom", default_model=None)
    assert "(not configured)" in out


# --- --show-system-prompt -------------------------------------------------


def _run_show_system_prompt(
    capsys, monkeypatch, tmp_path, skills_section, config_start=None, config_label=None
):
    """Run handle_show_system_prompt and return its captured output.

    ``skills_section`` is what ``get_skills_section`` should return; pass
    ``None`` to leave it unpatched (uses the real tool registry).
    ``config_start`` pins the configured system-prompt start (the
    ``system-prompt`` / ``system-prompt-file`` keys) so the tests never touch
    the real config; ``config_label`` pins the start section's display label.
    """
    import janito.config_loaders as config_loaders_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        config_loaders_mod,
        "load_system_prompt_start",
        lambda: (config_start, config_label),
    )
    if skills_section is not None:
        monkeypatch.setattr(
            tools_registry_mod, "get_skills_section", lambda: skills_section
        )
    args = SimpleNamespace(system_prompt=None, no_system_prompt=False)
    handle_show_system_prompt(args)
    return capsys.readouterr().out


def test_show_system_prompt_title_with_skills(capsys, monkeypatch, tmp_path):
    """A skills section present -> the title advertises (with skills)."""
    out = _run_show_system_prompt(capsys, monkeypatch, tmp_path, SKILLS_SECTION)
    assert "System prompt (default (with skills))" in out
    # The default start section is labeled "built-in" (issue #86).
    assert "built-in" in out
    assert "skills" in out
    assert "(fake skills section)" in out
    assert "Explore the current directory" in out


def test_show_system_prompt_title_without_skills(capsys, monkeypatch, tmp_path):
    """No skills section (no skills available) -> title omits (with skills)."""
    out = _run_show_system_prompt(capsys, monkeypatch, tmp_path, "")
    assert "System prompt (default)" in out
    assert "(with skills)" not in out


def test_show_system_prompt_no_skills_section_row(capsys, monkeypatch, tmp_path):
    """With no skills, no skills section row is rendered."""
    out = _run_show_system_prompt(capsys, monkeypatch, tmp_path, "")
    assert "skills" not in out


def test_show_system_prompt_config_start_shown_in_section_table(
    capsys, monkeypatch, tmp_path
):
    """A configured start appears in the start row of the default section table."""
    out = _run_show_system_prompt(
        capsys,
        monkeypatch,
        tmp_path,
        SKILLS_SECTION,
        config_start="configured start text",
        config_label="(config) ~/base-prompt.md",
    )
    assert "System prompt (default (with skills))" in out
    # The start row shows the (config) label instead of the section name.
    assert "(config) ~/base-prompt.md" in out
    assert "configured start text" in out
    # The base prompt is replaced by the configured start.
    assert "Explore the current directory" not in out


def test_show_system_prompt_override(capsys, monkeypatch, tmp_path):
    """A custom -S prompt is shown as a single section labeled -S (issue #86)."""
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace(system_prompt="custom system prompt", no_system_prompt=False)
    handle_show_system_prompt(args)
    out = capsys.readouterr().out
    assert "custom system prompt" in out
    # The section column shows the -S label.
    assert "-S" in out
    assert "(with skills)" not in out


def test_show_system_prompt_disabled(capsys, monkeypatch, tmp_path):
    """-Z / --no-system-prompt is reported as disabled."""
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace(system_prompt=None, no_system_prompt=True)
    handle_show_system_prompt(args)
    out = capsys.readouterr().out
    assert "disabled via -Z" in out
