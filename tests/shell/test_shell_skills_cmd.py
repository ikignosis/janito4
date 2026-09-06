"""Tests for the /skills shell command (behavior over rendering)."""

import io
from pathlib import Path
from unittest.mock import patch

from janito.shell.cmds.skills import SkillsCmdHandler
from janito.tooling.skills_provider import SkillsProvider
from tests.conftest import assert_command_registered


def _make_skill(base: Path, name: str, description: str = "A test skill.") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{description}\n",
        encoding="utf-8",
    )
    return skill_dir


def _provider_with(home_dir, local_dir=None) -> SkillsProvider:
    paths = []
    if home_dir is not None:
        paths.append((home_dir, "home"))
    if local_dir is not None:
        paths.append((local_dir, "local"))
    return SkillsProvider(skill_paths=paths)


def _run_handler(provider, user_input="/skills"):
    from janito.tooling import skills_provider as sp

    handler = SkillsCmdHandler()
    output = io.StringIO()
    with patch("sys.stdout", output), patch.object(sp, "get_skills_provider", return_value=provider):
        handled = handler.handle(object(), user_input)
    return handled, output.getvalue()


def test_skills_registered():
    assert_command_registered("/skills")


def test_skills_matching(capsys):
    handler = SkillsCmdHandler()
    assert handler.name == "/skills"
    assert handler.handle(object(), "/skills") is True
    assert handler.handle(object(), "/SKILLS") is True
    assert handler.handle(object(), "/skills extra") is False
    assert handler.handle(object(), "/tools") is False
    capsys.readouterr()


def test_lists_skills_smoke(tmp_path):
    home = tmp_path / "home" / "skills"
    local = tmp_path / "local" / "skills"
    home.mkdir(parents=True)
    local.mkdir(parents=True)
    _make_skill(home, "git-commit", "Commit with conventional commits")
    _make_skill(local, "code-review", "Review code for security issues")

    provider = _provider_with(home, local)
    # State first: provider sees both skills.
    assert len(provider.list_skills()) == 2
    handled, output = _run_handler(provider)
    # One smoke assert + one stable header max.
    assert handled is True
    assert output.strip() != ""
    assert "Home Skills" in output


def test_empty_output_smoke(tmp_path):
    provider = _provider_with(tmp_path / "does_not_exist")
    assert provider.list_skills() == []
    handled, output = _run_handler(provider)
    assert handled is True
    assert output.strip() != ""


def test_local_override_wins(tmp_path):
    home = tmp_path / "home" / "skills"
    local = tmp_path / "local" / "skills"
    home.mkdir(parents=True)
    local.mkdir(parents=True)
    _make_skill(home, "shared", "Home version")
    _make_skill(local, "shared", "Local version")

    provider = _provider_with(home, local)
    skills = provider.list_skills()
    # State: single entry, local version wins.
    assert len(skills) == 1
    assert skills[0]["description"] == "Local version"
    handled, output = _run_handler(provider)
    assert handled is True
    assert output.strip() != ""
