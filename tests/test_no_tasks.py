"""
Tests for the ``--no-tasks`` CLI flag.

``--no-tasks`` disables loading of the tasks toolset (StartTask / StopTask /
WaitForTask) while leaving every other toolset -- and the skill tools --
enabled.  The registry filters "tasks" out of the autoload discovery and
``add_toolset("tasks")`` becomes a no-op.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import janito.tooling.tools_registry as tools_registry


def _fake_tool(name, permissions=""):
    def fake(**kwargs):
        return {"success": True}

    fake.__name__ = name
    fake._tool_permissions = permissions
    return fake


if pytest is not None:

    def test_parser_exposes_no_tasks_flag():
        from janito.cli.parser import create_parser

        args = create_parser().parse_args(["--no-tasks", "prompt"])
        assert args.no_tasks is True
        # Default stays disabled.
        args = create_parser().parse_args(["prompt"])
        assert args.no_tasks is False

    def test_parser_help_documents_no_tasks():
        from janito.cli.parser import create_parser

        help_text = create_parser().format_help()
        assert "--no-tasks" in help_text
        assert "StartTask" in help_text
        assert "tasks toolset" in help_text

    def test_setup_runtime_applies_no_tasks(monkeypatch):
        from janito import __main__

        class _Args:
            no_tools = False
            no_tasks = True
            config_dir = None
            local = False
            log = None
            provider = None
            model = None
            read = write = exec = False

        monkeypatch.setattr(tools_registry, "_disabled_toolsets", set())
        assert __main__._setup_runtime(_Args()) is None
        assert tools_registry.disabled_toolsets() == {"tasks"}

    def test_setup_runtime_leaves_tasks_enabled_without_flag(monkeypatch):
        from janito import __main__

        class _Args:
            no_tools = False
            no_tasks = False
            config_dir = None
            local = False
            log = None
            provider = None
            model = None
            read = write = exec = False

        monkeypatch.setattr(tools_registry, "_disabled_toolsets", set())
        assert __main__._setup_runtime(_Args()) is None
        assert tools_registry.disabled_toolsets() == set()

    def test_web_config_from_args_applies_no_tasks(monkeypatch):
        from janito.cli.parser import create_parser
        from janito.web.backend.config import WebServerConfig

        monkeypatch.setattr(tools_registry, "_disabled_toolsets", set())
        args = create_parser().parse_args(["--web", "--no-tasks"])
        WebServerConfig.from_args(args)
        assert tools_registry.disabled_toolsets() == {"tasks"}
        assert args.no_tasks is True

    def test_web_config_cli_args_include_no_tasks(monkeypatch):
        from janito.cli.parser import create_parser
        from janito.web.backend.config import WebServerConfig

        monkeypatch.setattr(tools_registry, "_disabled_toolsets", set())
        args = create_parser().parse_args(["--web", "--no-tasks"])
        config = WebServerConfig.from_args(args)
        assert config.cli_args["no_tasks"] is True

    def test_registry_excludes_tasks_with_no_tasks(monkeypatch):
        """--no-tasks: tasks tools are absent, other tools and skills remain."""
        skill_tools = {
            "load_skill": _fake_tool("load_skill"),
            "read_skill_resource": _fake_tool("read_skill_resource"),
        }
        discovered = {"names": []}
        monkeypatch.setattr(tools_registry, "AVAILABLE_TOOLS", {})
        monkeypatch.setattr(tools_registry, "_tools_initialized", False)
        monkeypatch.setattr(
            tools_registry,
            "_loaded_toolsets",
            set(tools_registry.AUTOLOAD_TOOLSETS),
        )
        monkeypatch.setattr(tools_registry, "_skills_enabled", True)
        monkeypatch.setattr(tools_registry, "_tools_loading_enabled", True)
        monkeypatch.setattr(tools_registry, "_disabled_toolsets", {"tasks"})
        monkeypatch.setattr(tools_registry, "get_skills_tools", lambda: skill_tools)

        def fake_discover(names):
            discovered["names"] = list(names)
            tools = {"ReadFile": _fake_tool("ReadFile", "r")}
            if "tasks" in names:
                tools.update(
                    {
                        "StartTask": _fake_tool("StartTask", "x"),
                        "StopTask": _fake_tool("StopTask", "x"),
                        "WaitForTask": _fake_tool("WaitForTask", "x"),
                    }
                )
            return tools

        monkeypatch.setattr(tools_registry, "discover_toolsets", fake_discover)

        schemas = tools_registry.get_all_tool_schemas()
        names = {s["function"]["name"] for s in schemas}

        # Discovery ran without the disabled "tasks" toolset.
        assert "tasks" not in discovered["names"]
        assert discovered["names"] == ["files", "system", "net"]
        # Tasks tools are gone, everything else stayed.
        assert not {"StartTask", "StopTask", "WaitForTask"} & names
        assert "ReadFile" in names
        assert {"load_skill", "read_skill_resource"} <= names

    def test_add_toolset_tasks_refused_when_disabled(monkeypatch):
        """--no-tasks: add_toolset("tasks") is a no-op once disabled."""
        calls = {"n": 0}
        monkeypatch.setattr(tools_registry, "AVAILABLE_TOOLS", {})
        monkeypatch.setattr(tools_registry, "_tools_initialized", False)
        monkeypatch.setattr(
            tools_registry,
            "_loaded_toolsets",
            {"files", "system", "net"},
        )
        monkeypatch.setattr(tools_registry, "_skills_enabled", False)
        monkeypatch.setattr(tools_registry, "_tools_loading_enabled", True)
        monkeypatch.setattr(tools_registry, "_disabled_toolsets", {"tasks"})

        def fake_discover(names):
            calls["n"] += 1
            return {"ExtraTool": _fake_tool("ExtraTool", "r")}

        monkeypatch.setattr(tools_registry, "discover_toolsets", fake_discover)
        monkeypatch.setattr(tools_registry, "get_skills_tools", lambda: {})

        from janito.tooling.tools_registry import ToolsRegistry

        registry = ToolsRegistry()
        assert registry.add_toolset("tasks") is False
        # The early return skips ensure_initialized -> no discovery at all.
        assert calls["n"] == 0
        assert "StartTask" not in registry.all_tools()

else:  # pragma: no cover - fallback runner without pytest

    def _main():
        class _MP:
            def setattr(self, obj, name, value):
                self._undo = getattr(self, "_undo", [])
                self._undo.append((obj, name, getattr(obj, name)))
                setattr(obj, name, value)

            def restore(self):
                for obj, name, value in reversed(getattr(self, "_undo", [])):
                    setattr(obj, name, value)

        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                mp = _MP()
                try:
                    fn(mp)
                finally:
                    mp.restore()
                print(f"OK {name}")

    if __name__ == "__main__":
        _main()
