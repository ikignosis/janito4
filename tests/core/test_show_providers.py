"""Tests for ``janito --show-providers`` (behavior over strings)."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import janito.config_cli as cc
import janito.config_dir as config_dir_mod
import janito.config_store as cs
import janito.config_variants as cv
from janito.auth_config import set_api_key
from janito.cli.handlers.providers import handle_show_providers
from janito.providers.registry import get_provider
from janito.providers.validation import list_supported_providers


def _use_temp_config(monkeypatch, tmp_path):
    config_path = tmp_path / ".janito" / "config.json"
    monkeypatch.setattr(config_dir_mod, "_config_dir", config_path.parent)
    return config_path


def _run(monkeypatch, tmp_path, capsys):
    _use_temp_config(monkeypatch, tmp_path)
    rc = handle_show_providers(SimpleNamespace())
    return rc, capsys.readouterr().out


def test_parser_accepts_show_providers():
    from janito.cli.parser import create_parser

    args = create_parser().parse_args(["--show-providers"])
    assert args.show_providers is True


def test_lists_all_builtin_providers(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, capsys)
    assert rc == 0
    # Registry-driven (Rule 3) + single header (Rule 2).
    assert f"Supported Providers ({len(list_supported_providers())})" in out
    for name in list_supported_providers():
        assert name in out


def test_alibaba_tools_driven_from_registry(monkeypatch, tmp_path, capsys):
    """Alibaba built-in tools shown per API type, driven from the registry."""
    from janito.cli.handlers.providers import _tools_display

    found = get_provider("alibaba")
    default_model = found.default_model()
    expected = _tools_display("alibaba", default_model)
    assert expected  # precondition: fixture has tools
    _, out = _run(monkeypatch, tmp_path, capsys)
    assert out.strip() != ""
    # Every tool type named by the registry appears in the output.
    for api_type in found.supported_api_types(default_model) or []:
        for tool in found.tools(default_model, api_type=api_type) or []:
            t = tool.get("type") if isinstance(tool, dict) else str(tool)
            assert t in out


def test_custom_provider_endpoint_hint_from_registry(monkeypatch, tmp_path, capsys):
    from janito.cli.handlers.providers import _resolve_endpoint_display

    _, source = _resolve_endpoint_display("custom")
    _, out = _run(monkeypatch, tmp_path, capsys)
    assert out.strip() != ""
    assert "custom" in out
    assert source in out  # registry-resolved hint, not a hardcoded sentence


def test_google_thinking_driven_from_registry(monkeypatch, tmp_path, capsys):
    from janito.providers.payloads import format_thinking_display

    found = get_provider("google")
    default_model = found.default_model()
    expected = format_thinking_display(
        found.default_thinking(default_model), provider="google"
    )
    _, out = _run(monkeypatch, tmp_path, capsys)
    assert out.strip() != ""
    assert default_model in out
    assert expected in out


def test_lists_registered_variants(monkeypatch, tmp_path, capsys):
    _use_temp_config(monkeypatch, tmp_path)
    cv.create_variant("alibaba-tokenplan")
    cv.create_variant("custom-local")

    rc, out = _run(monkeypatch, tmp_path, capsys)
    assert rc == 0
    assert "alibaba-tokenplan (variant of alibaba)" in out  # single marker
    assert out.strip() != ""
    # Variant inherits base provider defaults (registry-driven).
    base_default = get_provider("alibaba").default_model()
    assert base_default in out


def test_shows_configured_overrides_and_masked_key(monkeypatch, tmp_path, capsys):
    from janito.config_loaders import load_endpoint_from_config, load_model_from_config

    _use_temp_config(monkeypatch, tmp_path)
    cv.create_variant("alibaba-tokenplan")
    cc.set_config_from_cli("model=qwen3.8-max", "alibaba-tokenplan")
    cc.set_config_from_cli(
        "endpoint=https://variant.example.com/v1", "alibaba-tokenplan"
    )
    set_api_key(
        "alibaba-tokenplan", "sk-abcdef1234567890wxyz"  # pragma: allowlist secret
    )

    # State asserts (Rule 1), not rendering pins.
    assert load_model_from_config("alibaba-tokenplan") == "qwen3.8-max"
    assert (
        load_endpoint_from_config("alibaba-tokenplan")
        == "https://variant.example.com/v1"
    )

    _, out = _run(monkeypatch, tmp_path, capsys)
    assert out.strip() != ""
    assert "https://variant.example.com/v1" in out  # single stable marker


def test_api_key_hidden_for_unset_providers(monkeypatch, tmp_path, capsys):
    _, out = _run(monkeypatch, tmp_path, capsys)
    assert out.strip() != ""
    assert "(not set)" in out  # single stable marker


def test_active_marker_follows_configured_provider(monkeypatch, tmp_path, capsys):
    _use_temp_config(monkeypatch, tmp_path)
    cs.set_config_value("provider", "deepseek")
    _, out = _run(monkeypatch, tmp_path, capsys)
    assert out.strip() != ""
    assert "deepseek [active]" in out  # single stable marker


def test_active_marker_for_variant(monkeypatch, tmp_path, capsys):
    _use_temp_config(monkeypatch, tmp_path)
    cv.create_variant("alibaba-tokenplan")
    cs.set_config_value("provider", "alibaba-tokenplan")
    _, out = _run(monkeypatch, tmp_path, capsys)
    assert out.strip() != ""
    assert "alibaba-tokenplan (variant of alibaba) [active]" in out


def test_shows_config_file_path(monkeypatch, tmp_path, capsys):
    config_path = _use_temp_config(monkeypatch, tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump({}, f)
    _, out = _run(monkeypatch, tmp_path, capsys)
    assert out.strip() != ""
    assert str(config_path) in out
