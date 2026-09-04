"""
Tests for the CLI setup helpers (janito.cli.setup).

Covers ``validate_system_prompt_file``: when the ``system-prompt-file``
config key is set, the app must validate at startup that the file exists and
fail with an actionable error otherwise.
"""

import json
import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

import janito.config_dir as config_dir_mod
from janito.cli.setup import validate_system_prompt_file


def _use_temp_config(monkeypatch, tmp_path):
    """Point the config directory at a temporary directory."""
    config_path = tmp_path / ".janito" / "config.json"
    monkeypatch.setattr(config_dir_mod, "_config_dir", config_path.parent)
    return config_path


if pytest is not None:

    def test_validate_system_prompt_file_unset_passes(monkeypatch, tmp_path):
        _use_temp_config(monkeypatch, tmp_path)
        # No config file at all: nothing to validate.
        validate_system_prompt_file()

    def test_validate_system_prompt_file_exists_passes(monkeypatch, tmp_path):
        config_path = _use_temp_config(monkeypatch, tmp_path)
        prompt_file = tmp_path / "base-prompt.md"
        prompt_file.write_text("Be terse.", encoding="utf-8")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"system-prompt-file": str(prompt_file)}))
        validate_system_prompt_file()  # must not raise

    def test_validate_system_prompt_file_expands_tilde(monkeypatch, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (home / "base-prompt.md").write_text("Be terse.", encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))  # Windows
        config_path = _use_temp_config(monkeypatch, tmp_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"system-prompt-file": "~/base-prompt.md"}))
        validate_system_prompt_file()  # must not raise

    def test_validate_system_prompt_file_relative_path_resolved_against_cwd(
        monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "prompt.md").write_text("relative", encoding="utf-8")
        config_path = _use_temp_config(monkeypatch, tmp_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"system-prompt-file": "prompt.md"}))
        validate_system_prompt_file()  # must not raise

    def test_validate_system_prompt_file_missing_exits(monkeypatch, tmp_path, capsys):
        config_path = _use_temp_config(monkeypatch, tmp_path)
        missing = tmp_path / "does-not-exist.md"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"system-prompt-file": str(missing)}))
        with pytest.raises(SystemExit) as exc:
            validate_system_prompt_file()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "system-prompt-file" in err
        assert str(missing) in err
        assert "does not exist" in err

    def test_validate_system_prompt_file_directory_is_not_a_file(
        monkeypatch, tmp_path, capsys
    ):
        # A directory path is not a file: validation fails too.
        config_path = _use_temp_config(monkeypatch, tmp_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"system-prompt-file": str(tmp_path)}))
        with pytest.raises(SystemExit) as exc:
            validate_system_prompt_file()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

else:  # pragma: no cover - fallback runner without pytest

    def _main():
        import tempfile

        class _MP:
            def setattr(self, obj, name, value):
                setattr(obj, name, value)

            def chdir(self, path):
                import os

                os.chdir(path)

            def setenv(self, name, value):
                import os

                os.environ[name] = value

        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                with tempfile.TemporaryDirectory() as d:
                    fn(_MP(), Path(d))
                print(f"OK {name}")

    if __name__ == "__main__":
        _main()
