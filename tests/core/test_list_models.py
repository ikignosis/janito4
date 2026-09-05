"""Tests for ``janito --list-models`` (behavior over strings)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import janito.config_dir as config_dir_mod
import janito.config_store as cs
from janito.cli.handlers.models import _available_model_names, handle_list_models
from janito.providers.registry import get_provider


def _use_temp_config(monkeypatch, tmp_path):
    config_path = tmp_path / ".janito" / "config.json"
    monkeypatch.setattr(config_dir_mod, "_config_dir", config_path.parent)
    return config_path


def _run(monkeypatch, tmp_path, capsys, **kwargs):
    _use_temp_config(monkeypatch, tmp_path)
    rc = handle_list_models(SimpleNamespace(**kwargs))
    return rc, capsys.readouterr().out


def test_parser_accepts_list_models():
    from janito.cli.parser import create_parser

    args = create_parser().parse_args(["--list-models"])
    assert args.list_models is True


def test_parser_accepts_list_models_with_provider():
    from janito.cli.parser import create_parser

    args = create_parser().parse_args(["--list-models", "--provider", "deepseek"])
    assert args.list_models is True
    assert args.provider == "deepseek"


def test_lists_builtin_models_for_cli_provider(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, capsys, provider="openai")
    assert rc == 0
    # Registry-driven: every built-in model listed (Rule 3).
    expected = get_provider("openai").model_names()
    assert expected
    for name in expected:
        assert name in out
    assert out.strip() != ""  # smoke (Rule 2)


def test_lists_all_builtin_models(monkeypatch, tmp_path, capsys):
    _, out = _run(monkeypatch, tmp_path, capsys, provider="deepseek")
    for name in get_provider("deepseek").model_names():
        assert name in out
    assert out.strip() != ""


def test_provider_read_from_config_json(monkeypatch, tmp_path, capsys):
    _use_temp_config(monkeypatch, tmp_path)
    cs.set_config_value("provider", "deepseek")
    rc, out = _run(monkeypatch, tmp_path, capsys)
    assert rc == 0
    assert out.strip() != ""
    for name in get_provider("deepseek").model_names():
        assert name in out


def test_falls_back_to_openai(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, capsys)
    assert rc == 0
    assert out.strip() != ""
    for name in get_provider("openai").model_names():
        assert name in out


def test_lists_configured_per_model_entries(monkeypatch, tmp_path, capsys):
    _use_temp_config(monkeypatch, tmp_path)
    cs.set_config_value("openai.models.gpt-future.max-output-tokens", 1000)
    # State: registry + config entry (Rule 1).
    assert "gpt-future" in _available_model_names("openai")
    _, out = _run(monkeypatch, tmp_path, capsys, provider="openai")
    assert out.strip() != ""
    assert "gpt-future" in out  # single stable marker


def test_configured_model_is_marked(monkeypatch, tmp_path, capsys):
    from janito.config_loaders import load_model_from_config

    _use_temp_config(monkeypatch, tmp_path)
    cs.set_config_value("openai.model", "gpt-future")
    assert load_model_from_config("openai") == "gpt-future"
    _, out = _run(monkeypatch, tmp_path, capsys, provider="openai")
    assert out.strip() != ""
    assert "gpt-future (configured, current)" in out  # single marker


def test_default_model_marked(monkeypatch, tmp_path, capsys):
    default = get_provider("openai").default_model()
    _, out = _run(monkeypatch, tmp_path, capsys, provider="openai")
    assert out.strip() != ""
    assert f"{default} (default, current)" in out  # single marker


def test_model_override_is_current(monkeypatch, tmp_path, capsys):
    _, out = _run(
        monkeypatch, tmp_path, capsys, provider="custom", model="my-custom-model"
    )
    assert out.strip() != ""
    assert "my-custom-model (current)" in out  # single marker


def test_shows_config_file_path(monkeypatch, tmp_path, capsys):
    config_path = _use_temp_config(monkeypatch, tmp_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _, out = _run(monkeypatch, tmp_path, capsys, provider="openai")
    assert out.strip() != ""
    assert str(config_path) in out


def test_custom_provider_without_models_shows_hint(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, capsys, provider="custom")
    assert rc == 0
    assert _available_model_names("custom") == []
    assert out.strip() != ""
    assert "(none - set a model with: janito --set model=NAME)" in out
