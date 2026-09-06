"""Tests for the /compact shell command (behavior over state)."""

import json

from janito.llm_clients import RequestCancelled
from janito.shell import InteractiveShell
from janito.shell.cmds.compact import KEEP_TURNS, MIN_COMPACT_TOKENS
from janito.shell.cmds.registry import get_registered_commands
from tests.conftest import assert_command_registered

LONG = "x" * 5000

COMPACTION_JSON = json.dumps(
    {
        "goal": "Build a feature",
        "completed_steps": ["Created app.py", "Added tests"],
        "current_blocker": None,
        "explicit_constraints": ["Must use Python 3.10"],
        "code_state": "app.py: main entry point",
        "unresolved_questions": ["Deployment target?"],
    }
)


def _compact_handler():
    return next(c for c in get_registered_commands() if c.name == "/compact")


def _shell():
    return InteractiveShell(model="test-model", no_history=True)


def _stub_send(result, calls=None):
    if calls is None:
        calls = {"n": 0}

    def turn_func(prompt, **kwargs):
        calls["n"] += 1
        return result

    return turn_func, calls


def _history_shell():
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": LONG},
        {"role": "assistant", "content": LONG},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a4"},
    ]
    shell.messages_history = [dict(m) for m in original]
    shell.history_turns = [1, 3, 5, 7]
    return shell, original


def test_compact_registered():
    assert_command_registered("/compact")


def test_compact_too_short_no_llm_call(capsys):
    """Guard: few turns -> no LLM call, history unchanged."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.messages_history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": LONG},
        {"role": "assistant", "content": LONG},
    ]
    shell.history_turns = [1]
    calls = {}
    shell.turn_func, calls = _stub_send(COMPACTION_JSON)
    _compact_handler()._do_compact(shell)
    capsys.readouterr()
    assert calls["n"] == 0
    assert len(shell.messages_history) == 3


def test_compact_completions_rebuilds_state(capsys):
    shell, original = _history_shell()
    calls = {}
    shell.turn_func, calls = _stub_send(COMPACTION_JSON)
    _compact_handler()._do_compact(shell)
    capsys.readouterr()
    assert calls["n"] == 1
    assert shell.messages_history[0] == {"role": "system", "content": "sys"}
    recap = shell.messages_history[1]
    assert recap["role"] == "assistant"
    assert recap["content"].startswith("[RECAP OF PRIOR WORK]")
    assert shell.messages_history[2:] == original[3:]
    assert shell.history_turns == []
    assert shell.previous_response_id is None
    assert shell.conversation_items is None


def test_compact_cancelled_keeps_history(capsys):
    shell, original = _history_shell()

    def turn_func(prompt, **kwargs):
        raise RequestCancelled()

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)
    out = capsys.readouterr().out
    assert shell.messages_history == original
    assert shell.history_turns == [1, 3, 5, 7]
    assert "cancel" in out.lower()


def test_compact_error_keeps_history(capsys):
    """Error path: kind only + no side effects."""
    shell, original = _history_shell()

    def turn_func(prompt, **kwargs):
        raise RuntimeError("boom")

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)
    out = capsys.readouterr().out
    assert shell.messages_history == original
    assert "error" in out.lower()


def test_compact_non_json_falls_back_to_raw_text():
    shell, _ = _history_shell()

    def turn_func(prompt, **kwargs):
        return "plain text recap"

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)
    recap = shell.messages_history[1]
    assert recap["role"] == "assistant"
    assert recap["content"] == "[RECAP OF PRIOR WORK] plain text recap"


def test_min_compact_tokens_threshold_constant():
    assert MIN_COMPACT_TOKENS == 2000


def test_keep_turns_constant():
    assert KEEP_TURNS == 3


def _stateless_shell_with_tools():
    from janito.shell.cmds.compact import _history_strategy

    shell = _shell()
    shell.messages_history = []
    LONG2 = "y" * 5000
    shell.conversation_items = [
        {"type": "message", "role": "system", "content": [{"type": "input_text", "text": "sys"}]},
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": LONG}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": LONG2}]},
        {"type": "function_call", "call_id": "call_1", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "file contents"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "u2"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a2"}]},
        {"type": "function_call", "call_id": "call_2", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_2", "output": "more contents"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "u3"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a3"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "u4"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a4"}]},
    ]
    # rows: sys(0) user(1) asst(2) fc1(3) fco1(4) u2(5) a2(6) fc2(7) fco2(8)
    #   u3(9) a3(10) u4(11) a4(12). The compact zone ends at the u3 row, right
    #   after a tool round: with the pre-fix item slicing (off by the leading
    #   encrypted reasoning item) the window ended between fc2 and fco2, so
    #   the compaction call sent an unpaired function_call (API 400).
    shell.history_turns = [3, 9, 11, 13]
    assert _history_strategy(shell).mode == "stateless"
    return shell


def test_compact_stateless_keeps_tool_pairs(capsys):
    """Regression: encrypted reasoning must not misalign the slice (issue: No
    tool output found for function call on muse-spark)."""
    shell = _stateless_shell_with_tools()
    captured = {}

    def turn_func(prompt, **kwargs):
        captured.update(kwargs)
        return COMPACTION_JSON

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)
    capsys.readouterr()
    assert captured, "compaction LLM call was not made"
    sent = captured.get("previous_items") or []
    calls = {e.get("call_id") for e in sent if e.get("type") == "function_call"}
    outputs = {e.get("call_id") for e in sent if e.get("type") == "function_call_output"}
    assert calls, "expected the tool round in the compaction payload"
    assert calls == outputs, f"unpaired calls: {calls} vs {outputs}"


def test_compact_stateless_drops_orphan_call():
    from janito.shell.cmds.compact import _sanitize_response_items

    entries = [
        {"type": "function_call", "call_id": "orphan", "name": "read", "arguments": "{}"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
    ]
    assert _sanitize_response_items(entries) == [entries[1]]


def test_compact_completions_drops_orphan_tool_call():
    from janito.shell.cmds.compact import _sanitize_messages

    entries = [
        {
            "role": "assistant",
            "content": "hi",
            "tool_calls": [{"id": "tc1", "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "user", "content": "next"},
    ]
    out = _sanitize_messages(entries)
    assert out[0].get("tool_calls") is None
