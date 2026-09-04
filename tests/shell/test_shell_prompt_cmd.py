"""
Tests for the ``/prompt`` shell command display.

``/prompt`` shows the effective system prompt; when it is the default
skills-advertising prompt, each section (``start``, ``skills``, ``agents.md``)
is displayed as a row of a rich table so the user can see how much of the
prompt each source contributes.  Custom prompts (``-S``) fall back to a
plain single-column table with the full text.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

import janito.tooling.tools_registry as tools_registry_mod
from janito.shell import InteractiveShell
from janito.shell.cmds.prompt import PromptCmdHandler

SKILLS_SECTION = "## Available Skills\n(fake skills section)"


def _patch_skills_section(monkeypatch):
    """Patch the skills section so the test is isolated from the tool registry."""
    monkeypatch.setattr(
        tools_registry_mod, "get_skills_section", lambda: SKILLS_SECTION
    )


def _patch_no_skills(monkeypatch):
    """Patch the skills section to advertise no skills at all."""
    monkeypatch.setattr(tools_registry_mod, "get_skills_section", lambda: "")


def _patch_config_start(monkeypatch, start, label=None):
    """Pin load_system_prompt_start so tests never touch the real config."""
    import janito.config_loaders as config_loaders_mod

    monkeypatch.setattr(
        config_loaders_mod, "load_system_prompt_start", lambda: (start, label)
    )


def test_prompt_cmd_shows_section_table(monkeypatch, tmp_path, capfd):
    """The default prompt is displayed as a rich table with per-section rows."""
    from janito.system_prompt import default_system_prompt_manager

    _patch_skills_section(monkeypatch)
    _patch_config_start(monkeypatch, None)
    monkeypatch.chdir(tmp_path)

    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt=default_system_prompt_manager().render())

    handler = PromptCmdHandler()
    assert handler.handle(shell, "/prompt") is True

    out = capfd.readouterr().out
    assert "System Prompt - Default (with Skills)" in out
    # The default start section is labeled "built-in" (issue #86).
    assert "built-in" in out
    assert "skills" in out
    assert "Available Skills" in out
    assert "(fake skills section)" in out
    assert "Explore the current directory" in out
    # No more plain-text ==== / ---- headers.
    assert "----" not in out


def test_prompt_cmd_no_skills_title_omits_skills(monkeypatch, tmp_path, capfd):
    """Without any skills the title omits the (with Skills) suffix."""
    from janito.system_prompt import default_system_prompt_manager

    _patch_no_skills(monkeypatch)
    _patch_config_start(monkeypatch, None)
    monkeypatch.chdir(tmp_path)

    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt=default_system_prompt_manager().render())

    handler = PromptCmdHandler()
    assert handler.handle(shell, "/prompt") is True

    out = capfd.readouterr().out
    assert "System Prompt - Default" in out
    assert "(with Skills)" not in out
    # No skills row is rendered either.
    assert "skills" not in out


def test_prompt_cmd_includes_agents_md_section(monkeypatch, tmp_path, capfd):
    """An AGENTS.md in cwd appears as its own row in the table."""
    from janito.system_prompt import default_system_prompt_manager

    _patch_skills_section(monkeypatch)
    _patch_config_start(monkeypatch, None)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("agent line", encoding="utf-8")

    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt=default_system_prompt_manager().render())

    handler = PromptCmdHandler()
    assert handler.handle(shell, "/prompt") is True

    out = capfd.readouterr().out
    assert "agents.md" in out
    assert "agent line" in out


def test_prompt_cmd_custom_prompt_falls_back_to_plain(monkeypatch, tmp_path, capfd):
    """A custom (-S) prompt is shown in full as a single section labeled -S."""
    _patch_skills_section(monkeypatch)
    _patch_config_start(monkeypatch, None)
    monkeypatch.chdir(tmp_path)

    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt="custom system prompt")

    handler = PromptCmdHandler()
    assert handler.handle(shell, "/prompt") is True

    out = capfd.readouterr().out
    assert "System Prompt" in out
    assert "custom system prompt" in out
    # The section column shows the -S label (issue #86).
    assert "-S" in out
    assert "----" not in out


def test_prompt_cmd_config_start_keeps_section_table(monkeypatch, tmp_path, capfd):
    """A configured start is still shown as the default section table (not custom).

    The shell prompt is resolved through the same config-aware path
    (SessionSetup -> default_system_prompt_manager), so /prompt must classify
    it as the default prompt and render the per-section rows -- with the
    configured text in the ``start`` row -- instead of drifting into the
    plain custom-prompt view.
    """
    from janito.session_setup import SessionSetup

    _patch_skills_section(monkeypatch)
    _patch_config_start(
        monkeypatch, "configured start text", label="(config) ~/base.md"
    )
    monkeypatch.chdir(tmp_path)

    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt=SessionSetup().effective_system_prompt())

    handler = PromptCmdHandler()
    assert handler.handle(shell, "/prompt") is True

    out = capfd.readouterr().out
    assert "System Prompt - Default (with Skills)" in out
    # The start row shows the (config) label instead of the section name.
    assert "(config) ~/base.md" in out
    assert "configured start text" in out
    assert "(fake skills section)" in out
    # The base prompt is gone from the display (replaced by the config start).
    assert "Explore the current directory" not in out


def test_prompt_cmd_preserves_leading_whitespace_of_sections(
    monkeypatch, tmp_path, capfd
):
    """Leading whitespace of a section is kept in the table display.

    The start section and plugin sections may start with a newline; ``rstrip``
    (not ``strip``) keeps that leading whitespace so the rendered rows show
    the blank-line separation between sections.
    """
    from janito.system_prompt import (
        SYSTEM_PROMPT_MANAGER,
        default_system_prompt_manager,
    )

    _patch_skills_section(monkeypatch)
    _patch_config_start(monkeypatch, None)
    monkeypatch.chdir(tmp_path)

    # Register a fake plugin section with a leading newline (as a plugin whose
    # SYSTEM_PROMPT starts with a blank line would) and remove it afterwards.
    SYSTEM_PROMPT_MANAGER.add_section("plugins:testplugin", "\nplugin section text")
    try:
        shell = InteractiveShell(model="test-model", no_history=True)
        shell.initialize_history(system_prompt=default_system_prompt_manager().render())

        handler = PromptCmdHandler()
        assert handler.handle(shell, "/prompt") is True

        out = capfd.readouterr().out
        assert "plugins:testplugin" in out
        assert "plugin section text" in out
        # The leading newline of the plugin section shows as a blank content
        # row between the previous section and the plugin text.
        assert "\u2502\n\u2502 plugins:testplugin" in out
    finally:
        SYSTEM_PROMPT_MANAGER.del_section("plugins:testplugin")


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-v"]))
