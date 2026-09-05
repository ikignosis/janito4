"""Tests for the shell /api_types command handler (behavior over rendering)."""

import janito.config_dir as config_dir_mod
from janito.providers.registry import get_provider
from janito.providers.validation import list_supported_providers
from janito.shell import InteractiveShell
from janito.shell.cmds.registry import get_registered_commands
from tests.conftest import assert_command_registered


def _use_temp_config(monkeypatch, tmp_path):
    config_path = tmp_path / ".janito" / "config.json"
    monkeypatch.setattr(config_dir_mod, "_config_dir", config_path.parent)
    return config_path


def _shell():
    return InteractiveShell(model="test-model", no_history=True)


def _api_types_handler():
    return next(c for c in get_registered_commands() if c.name == "/api_types")


def test_api_types_registered():
    assert_command_registered("/api_types")


def test_api_types_matching():
    # Standard matching except exact-only nuance: /api_type must not match.
    handler = _api_types_handler()
    assert handler.name == "/api_types"
    shell = _shell()
    assert handler.handle(shell, "/api_types") is True
    assert handler.handle(shell, "/API_TYPES") is True
    assert handler.handle(shell, "/api_type") is False
    assert handler.handle(shell, "hello") is False


def test_api_types_smoke(monkeypatch, tmp_path, capsys):
    """One smoke assert: renders non-empty output with a stable header."""
    _use_temp_config(monkeypatch, tmp_path)
    shell = _shell()
    assert _api_types_handler().handle(shell, "/api_types") is True
    out = capsys.readouterr().out
    assert out.strip() != ""
    assert "Provider" in out


def test_api_types_covers_registry(monkeypatch, tmp_path, capsys):
    """Expectations driven from the provider registry (source of truth)."""
    _use_temp_config(monkeypatch, tmp_path)
    shell = _shell()
    assert _api_types_handler().handle(shell, "/api_types") is True
    out = capsys.readouterr().out
    for provider in list_supported_providers():
        found = get_provider(provider)
        for model in found.model_names():
            assert provider in out
            assert model in out


def test_non_api_types_input_no_side_effects(capsys):
    shell = _shell()
    assert _api_types_handler().handle(shell, "/api_type") is False
    assert capsys.readouterr().out == ""
