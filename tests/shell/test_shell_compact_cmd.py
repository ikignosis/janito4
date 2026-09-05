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
