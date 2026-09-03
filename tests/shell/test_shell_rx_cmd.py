"""
Tests for the /rx shell command.

``/rx <question>`` sends the prompt to the LLM using the **main**
conversation history (unlike ``/ask``, which starts a fresh history) but with
``tools=`` filtered down to the read and execute (``"r"``/``"x"`` permission)
tools. These tests verify the command is registered, dispatches correctly,
builds the read + execute tool schema list, and routes the prompt through the
shell's main-prompt path.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito.shell import InteractiveShell
from janito.shell.cmds.rx import RxCmdHandler, get_read_exec_tool_schemas

# A fake tool schema pair: one read-only tool, one execute tool and one
# write tool.
READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ReadFile",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
EXEC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "RunBashCode",
        "description": "Run a bash command",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "CreateFile",
        "description": "Create a file",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _shell():
    """Build a fresh shell for testing."""
    return InteractiveShell(model="test-model", no_history=True)


# ---------------------------------------------------------------------------
# Registry / dispatch
# ---------------------------------------------------------------------------


def test_rx_command_is_registered():
    from janito.shell.cmds import get_registered_commands

    names = [c.name for c in get_registered_commands()]
    assert "/rx" in names


def test_handler_name():
    assert RxCmdHandler().name == "/rx"


def test_handle_dispatches_only_rx_command(monkeypatch):
    handler = RxCmdHandler()
    shell = _shell()
    shell.turn_func = lambda **kw: None
    sent = {}

    def fake_run_turn(prompt, tools=None):
        sent["prompt"] = prompt
        sent["tools"] = tools

    monkeypatch.setattr(shell, "_run_turn", fake_run_turn)
    monkeypatch.setattr(
        "janito.shell.cmds.rx.get_read_exec_tool_schemas",
        lambda: [READ_SCHEMA, EXEC_SCHEMA],
    )

    assert handler.handle(shell, "/rx what is this?") is True
    assert sent["prompt"] == "what is this?"
    assert sent["tools"] == [READ_SCHEMA, EXEC_SCHEMA]

    # Case-insensitive command, question preserved.
    assert handler.handle(shell, "/RX tell me more") is True
    assert sent["prompt"] == "tell me more"

    # Bare '/rx' matches but shows usage (no send).
    assert handler.handle(shell, "/rx") is True

    # Non-matching inputs are not handled.
    assert handler.handle(shell, "/rxs") is False
    assert handler.handle(shell, "/rxd") is False
    assert handler.handle(shell, "/tools") is False
    assert handler.handle(shell, "hello") is False


def test_rx_without_question_shows_usage(monkeypatch, capfd):
    handler = RxCmdHandler()
    shell = _shell()
    called = {"n": 0}

    def fake_run_turn(prompt, tools=None):
        called["n"] += 1

    monkeypatch.setattr(shell, "_run_turn", fake_run_turn)

    assert handler.handle(shell, "/rx") is True
    out = capfd.readouterr().out
    assert "Usage: /rx <your question>" in out
    assert "read and execute tools" in out
    assert called["n"] == 0


def test_rx_requires_turn_func(monkeypatch, capfd):
    """Without turn_func an error is printed instead of crashing."""
    handler = RxCmdHandler()
    shell = _shell()
    # The shell has no turn_func until run() sets it.
    assert handler.handle(shell, "/rx hello") is True
    out = capfd.readouterr().out
    assert "No prompt function available" in out


# ---------------------------------------------------------------------------
# Read + execute schema filtering
# ---------------------------------------------------------------------------


def test_get_read_exec_tool_schemas_filters_by_r_and_x_permissions(monkeypatch):
    """Only tools whose permissions are exactly ``"r"`` or ``"x"`` are included."""
    permissions = {
        "ReadFile": "r",
        "ListFiles": "r",
        "RunBashCode": "x",
        "RunPythonCode": "x",
        "CreateFile": "w",
        "MoveFile": "rw",
        "LoadSkill": "",  # skill tools declare no permissions
    }
    schemas = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Tool {name}",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
        for name in permissions
    ]

    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: permissions,
    )
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_schemas", lambda: schemas
    )

    result = get_read_exec_tool_schemas()
    names = [s["function"]["name"] for s in result]
    assert names == ["ReadFile", "ListFiles", "RunBashCode", "RunPythonCode"]
    assert "CreateFile" not in names
    assert "MoveFile" not in names
    assert "LoadSkill" not in names


def test_get_read_exec_tool_schemas_empty_without_r_or_x_tools(monkeypatch):
    """No read/execute tools -> an empty list (equivalent to tools=[])."""
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_permissions",
        lambda: {"CreateFile": "w", "MoveFile": "rw"},
    )
    monkeypatch.setattr(
        "janito.tooling.tools_registry.get_all_tool_schemas", lambda: [WRITE_SCHEMA]
    )

    assert get_read_exec_tool_schemas() == []


# ---------------------------------------------------------------------------
# End-to-end routing through the main conversation
# ---------------------------------------------------------------------------


def test_rx_routes_through_main_history_with_read_exec_tools(monkeypatch):
    """/rx goes through _run_turn, which uses the main history and the
    filtered tools."""
    handler = RxCmdHandler()
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.verbose = False
    shell.thinking = False
    shell.no_tools = False

    monkeypatch.setattr(
        "janito.shell.cmds.rx.get_read_exec_tool_schemas",
        lambda: [READ_SCHEMA, EXEC_SCHEMA],
    )

    kwargs = {}

    def capture(prompt, **kw):
        kwargs["prompt"] = prompt
        kwargs.update(kw)
        return "assistant"

    shell.turn_func = capture

    handler.handle(shell, "/rx summarize the project")

    # The main history is used (mutated in place by the Completions client),
    # and only the read/execute schemas are passed as tools.
    assert kwargs["prompt"] == "summarize the project"
    assert kwargs["previous_messages"] is shell.messages_history
    assert kwargs["instructions"] == "sys"
    assert kwargs["tools"] == [READ_SCHEMA, EXEC_SCHEMA]


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-v"]))
