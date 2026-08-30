"""
Tests for the /compact shell command.

``/compact`` keeps the last ``KEEP_TURNS`` turns (recorded as
``history_turns``) untouched and replaces everything before them with a
single "[RECAP OF PRIOR WORK]" assistant message produced by a dedicated LLM
call (the Context Compression Engine prompt).  Where the history lives depends
on the API type -- Completions / Anthropic / DashScope / Gemini keep it in
``shell.messages_history``, stateless Responses (e.g. DeepSeek) in
``shell.conversation_items`` (Responses input items with the system prompt
folded in), and server-side Responses (e.g. OpenAI) on the server with a
display-only mirror.  These tests verify the compact/replace logic and the
"too short" guard in every mode.
"""

import json
import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from janito.llm_clients import RequestCancelled
from janito.shell import InteractiveShell
from janito.shell.cmds.compact import (
    KEEP_TURNS,
    MIN_COMPACT_TOKENS,
    SYSTEM_COMPACT_PROMPT,
    _build_new_context,
    _parse_compaction_response,
    format_compacted_json_to_narrative,
)
from janito.shell.cmds.registry import get_registered_commands

#: Long enough content for one message so the compact zone (2 rows) clears the
#: ~4 chars/token estimate of the MIN_COMPACT_TOKENS gate.
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
    """Return the registered /compact command handler."""
    return next(c for c in get_registered_commands() if c.name == "/compact")


def _shell():
    """Build a fresh shell for testing."""
    return InteractiveShell(model="test-model", no_history=True)


def _stub_send(result, calls=None):
    """Return a turn_func stub returning ``result`` and recording calls."""
    if calls is None:
        calls = {"n": 0}

    def turn_func(prompt, **kwargs):
        calls["n"] += 1
        return result

    return turn_func, calls


# ---------------------------------------------------------------------------
# Registry / dispatch
# ---------------------------------------------------------------------------


def test_compact_command_is_registered():
    """The /compact handler is registered with the shell command registry."""
    names = [cmd.name for cmd in get_registered_commands()]
    assert "/compact" in names


def test_compact_handles_exact_command_only():
    """/compact only fires on the exact command, not sub-strings or args."""
    shell = _shell()
    handler = _compact_handler()
    assert handler.handle(shell, "/compact") is True
    assert handler.handle(shell, "/compact foo") is False
    assert handler.handle(shell, "/compacting") is False


# ---------------------------------------------------------------------------
# "Too short" guard
# ---------------------------------------------------------------------------


def test_compact_too_short_few_turns(capsys):
    """Fewer than KEEP_TURNS turns: disabled with the warning, no LLM call."""
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

    out = capsys.readouterr().out
    assert "Conversation too short to compact effectively." in out
    assert calls["n"] == 0
    assert len(shell.messages_history) == 3


def test_compact_too_short_token_count(capsys):
    """Enough turns but a tiny compact zone: disabled with the warning."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.messages_history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a4"},
    ]
    shell.history_turns = [1, 3, 5, 7]
    calls = {}
    shell.turn_func, calls = _stub_send(COMPACTION_JSON)

    _compact_handler()._do_compact(shell)

    out = capsys.readouterr().out
    assert "Conversation too short to compact effectively." in out
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# Completions / Anthropic / DashScope / Gemini: messages_history
# ---------------------------------------------------------------------------


def test_compact_completions_mode(capsys):
    """Completions-mode history is rebuilt as system + recap + keep zone."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": LONG},  # row 1 -- compacted
        {"role": "assistant", "content": LONG},  # row 2 -- compacted
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a4"},
    ]
    shell.messages_history = [dict(m) for m in original]
    shell.history_turns = [1, 3, 5, 7]
    calls = {}
    shell.turn_func, calls = _stub_send(COMPACTION_JSON)

    _compact_handler()._do_compact(shell)

    assert calls["n"] == 1

    assert shell.messages_history[0] == {"role": "system", "content": "sys"}
    recap = shell.messages_history[1]
    assert recap["role"] == "assistant"
    assert recap["content"].startswith("[RECAP OF PRIOR WORK]")
    assert "Goal: Build a feature" in recap["content"]
    assert "Completed steps: Created app.py; Added tests" in recap["content"]
    # The last 3 turns (rows 3..8) are untouched.
    assert shell.messages_history[2:] == original[3:]
    # The turn list and server-conversation trackers are reset.
    assert shell.history_turns == []
    assert shell.previous_response_id is None
    assert shell.conversation_items is None
    assert shell.response_chain == []
    assert shell.mirrored_history == []
    out = capsys.readouterr().out
    assert "Compacting conversation history..." in out
    assert f"last {KEEP_TURNS} turns kept verbatim" in out


def test_compact_completions_sends_compaction_prompt():
    """The compaction LLM call carries the compaction system prompt and exactly
    the raw messages being replaced (main history untouched by the side call).
    Completions-mode passes ``previous_messages`` only -- no Responses items."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.messages_history = [
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
    shell.history_turns = [1, 3, 5, 7]
    seen = {}

    def turn_func(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["messages"] = kwargs.get("previous_messages")
        seen["instructions"] = kwargs.get("instructions")
        seen["tools"] = kwargs.get("tools")
        seen["items"] = kwargs.get("previous_items")
        return COMPACTION_JSON

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)

    msgs = seen["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == SYSTEM_COMPACT_PROMPT
    # The raw message dicts of the compact zone (rows 1..2) are re-sent
    # verbatim after the compaction system prompt.
    assert msgs[1:] == [
        {"role": "user", "content": LONG},
        {"role": "assistant", "content": LONG},
    ]
    assert seen["instructions"] == SYSTEM_COMPACT_PROMPT
    assert seen["tools"] == []
    assert "Output ONLY the JSON" in seen["prompt"]
    # Completions mode never builds a Responses items variant.
    assert seen["items"] is None


def test_compact_completions_preserves_tool_rounds():
    """Tool-call rounds in the compact zone keep their native Completions
    shape (assistant ``tool_calls`` + ``role: \"tool\"`` results) -- they must
    not be flattened into invalid plain messages."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "ListFiles", "arguments": '{"directory": "."}'},
        }
    ]
    shell.messages_history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": LONG},
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"files": ["a.py"]}'},
        {"role": "assistant", "content": LONG},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a4"},
    ]
    shell.history_turns = [1, 5, 7, 9]
    seen = {}

    def turn_func(prompt, **kwargs):
        seen["messages"] = kwargs.get("previous_messages")
        return COMPACTION_JSON

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)

    msgs = seen["messages"]
    # system compact prompt + the 4 raw compact-zone messages (rows 1..4).
    assert msgs[0]["role"] == "system"
    compact_zone = msgs[1:]
    assert [m["role"] for m in compact_zone] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    # The assistant tool-call message keeps its tool_calls structure.
    assert compact_zone[1]["tool_calls"] == tool_calls
    assert compact_zone[1]["content"] is None
    # The tool result keeps its tool_call_id.
    assert compact_zone[2]["tool_call_id"] == "call_1"
    assert compact_zone[2]["content"] == '{"files": ["a.py"]}'


# ---------------------------------------------------------------------------
# Stateless Responses (e.g. DeepSeek): conversation_items
# ---------------------------------------------------------------------------


def _stateless_shell():
    """Build a stateless-Responses shell (system folded into the items)."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    items = [
        {
            "type": "message",
            "role": "system",
            "content": [{"type": "input_text", "text": "sys"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": LONG}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": LONG}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u2"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a2"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u3"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a3"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u4"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a4"}],
        },
    ]
    shell.messages_history = [{"role": "system", "content": "sys"}]
    shell.conversation_items = [dict(i) for i in items]
    shell.history_turns = [1, 3, 5, 7]
    return shell, items


def test_compact_stateless_responses(capsys):
    """Stateless Responses rebuilds conversation_items as system + recap +
    keep zone items."""
    shell, original = _stateless_shell()
    calls = {}
    shell.turn_func, calls = _stub_send(COMPACTION_JSON)

    _compact_handler()._do_compact(shell)

    assert calls["n"] == 1
    items = shell.conversation_items
    assert items[0]["role"] == "system"
    assert items[0]["content"][0]["text"] == "sys"
    assert items[1]["role"] == "assistant"
    recap_text = items[1]["content"][0]["text"]
    assert recap_text.startswith("[RECAP OF PRIOR WORK]")
    assert "Goal: Build a feature" in recap_text
    # The last 3 turns (items 3..8) are untouched, in Responses format.
    assert items[2:] == original[3:]
    assert shell.history_turns == []
    assert shell.previous_response_id is None
    assert shell.response_chain == []
    assert shell.mirrored_history == []
    # The baseline is the whole new items list, so /rewind does not truncate it.
    assert shell.conversation_turn == len(items)
    out = capsys.readouterr().out
    assert "Compacting conversation history..." in out


def test_compact_stateless_preserves_tool_call_items():
    """Stateless Responses tool-call rounds in the compact zone are passed to
    the compaction call as native input items (``function_call`` /
    ``function_call_output``), NOT flattened into message items with an
    invalid ``function_call`` role (regression: the API rejected the request
    with 'unknown variant `function_call`, expected one of user, assistant,
    system, developer')."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.messages_history = [{"role": "system", "content": "sys"}]
    shell.conversation_items = [
        {
            "type": "message",
            "role": "system",
            "content": [{"type": "input_text", "text": "sys"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": LONG}],
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "ListFiles",
            "arguments": '{"directory": "."}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"files": ["a.py"]}',
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": LONG}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u2"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a2"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u3"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a3"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u4"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a4"}],
        },
    ]
    shell.history_turns = [1, 5, 7, 9]
    seen = {}

    def turn_func(prompt, **kwargs):
        seen["items"] = kwargs.get("previous_items")
        return COMPACTION_JSON

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)

    items = seen["items"]
    # System compact item + the raw compact-zone items (rows 1..4: the first
    # turn, including its tool-call round).
    assert items[0]["type"] == "message" and items[0]["role"] == "system"
    assert items[0]["content"][0]["text"] == SYSTEM_COMPACT_PROMPT
    compact_zone = items[1:]
    # The tool-call round keeps its native item types -- every message item
    # has a valid role.
    assert [i["type"] for i in compact_zone] == [
        "message",
        "function_call",
        "function_call_output",
        "message",
    ]
    assert compact_zone[0]["role"] == "user"
    assert compact_zone[1]["name"] == "ListFiles"
    assert compact_zone[2]["call_id"] == "call_1"
    assert compact_zone[3]["role"] == "assistant"
    assert all(
        i.get("role") in ("user", "assistant", "system")
        for i in items
        if i["type"] == "message"
    )


# ---------------------------------------------------------------------------
# Server-side Responses (e.g. OpenAI): mirrored_history + pending items
# ---------------------------------------------------------------------------


def _server_side_shell():
    """Build a server-side Responses shell (history on the server, display-only
    mirror of completed turns client-side)."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.messages_history = [{"role": "system", "content": "sys"}]
    shell.previous_response_id = "r4"
    shell.response_chain = ["r1", "r2", "r3", "r4"]
    shell.response_turn = 4
    mirror = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": LONG}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": LONG}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u2"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a2"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u3"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a3"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "u4"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "a4"}],
        },
    ]
    shell.mirrored_history = [dict(i) for i in mirror]
    shell.conversation_items = None
    shell.history_turns = [1, 3, 5, 7]
    return shell, mirror


def test_compact_server_side_responses(capsys):
    """Server-side Responses seeds the next fresh server turn with the recap +
    keep zone as input items and drops the server conversation handle."""
    shell, mirror = _server_side_shell()
    calls = {}
    shell.turn_func, calls = _stub_send(COMPACTION_JSON)

    _compact_handler()._do_compact(shell)

    assert calls["n"] == 1
    # The server conversation is reset; the recap + keep zone seed the next
    # fresh turn as input items.
    assert shell.previous_response_id is None
    assert shell.response_chain == []
    assert shell.response_turn == 0
    assert shell.mirrored_history == []
    assert shell.mirrored_turn == 0
    assert shell.history_turns == []
    # System prompt stays in messages_history (sent as instructions next turn).
    assert shell.messages_history == [{"role": "system", "content": "sys"}]
    items = shell.conversation_items
    assert items[0]["role"] == "assistant"
    assert items[0]["content"][0]["text"].startswith("[RECAP OF PRIOR WORK]")
    # Keep zone = mirror rows from row 3 onward (u2..a4).
    assert items[1:] == mirror[2:]
    assert shell.conversation_turn == len(items)
    out = capsys.readouterr().out
    assert "Compacting conversation history..." in out


# ---------------------------------------------------------------------------
# Cancellation / errors leave the history unchanged
# ---------------------------------------------------------------------------


def test_compact_cancelled_keeps_history(capsys):
    """Enter-cancel (RequestCancelled) aborts the compaction; history is kept."""
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

    def turn_func(prompt, **kwargs):
        raise RequestCancelled()

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)

    assert shell.messages_history == original
    assert shell.history_turns == [1, 3, 5, 7]
    out = capsys.readouterr().out
    assert "Compaction cancelled" in out


def test_compact_error_keeps_history(capsys):
    """A failing compaction call leaves the conversation untouched."""
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

    def turn_func(prompt, **kwargs):
        raise RuntimeError("boom")

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)

    assert shell.messages_history == original
    out = capsys.readouterr().out
    assert "Error during compaction: boom" in out


def test_compact_non_json_falls_back_to_raw_text():
    """A non-JSON compaction answer is used verbatim as the recap narrative."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.messages_history = [
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
    shell.history_turns = [1, 3, 5, 7]

    def turn_func(prompt, **kwargs):
        return "plain text recap"

    shell.turn_func = turn_func
    _compact_handler()._do_compact(shell)

    recap = shell.messages_history[1]
    assert recap["role"] == "assistant"
    assert recap["content"] == "[RECAP OF PRIOR WORK] plain text recap"


def test_compact_no_turn_func(capsys):
    """Without a session send function /compact reports an error."""
    shell = _shell()
    shell.initialize_history(system_prompt="sys")
    shell.messages_history = [
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
    shell.history_turns = [1, 3, 5, 7]
    # No turn_func attribute.
    _compact_handler()._do_compact(shell)
    out = capsys.readouterr().out
    assert "No prompt function available" in out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_parse_compaction_response_strips_markdown_fences():
    """Markdown-fenced JSON is parsed (```json ... ```)."""
    assert _parse_compaction_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_compaction_response_plain_json():
    """Plain JSON is parsed."""
    assert _parse_compaction_response('{"goal": "x"}') == {"goal": "x"}


def test_parse_compaction_response_fallback():
    """Unparseable output is returned as the raw text."""
    assert _parse_compaction_response("not json") == "not json"


def test_format_compacted_json_to_narrative():
    """The compacted JSON fields are rendered into a readable narrative."""
    compacted = {
        "goal": "Ship the feature",
        "completed_steps": ["REJECTED: use SQLite", "Wrote schema.sql"],
        "current_blocker": "auth flaky",
        "explicit_constraints": ["must use Python 3.10"],
        "code_state": "schema.sql: CREATE TABLE users",
        "unresolved_questions": ["Deploy target?"],
    }
    narrative = format_compacted_json_to_narrative(compacted)
    assert "Goal: Ship the feature" in narrative
    assert "Completed steps: REJECTED: use SQLite; Wrote schema.sql" in narrative
    assert "Current blocker: auth flaky" in narrative
    assert "Explicit constraints: must use Python 3.10" in narrative
    assert "Code state: schema.sql: CREATE TABLE users" in narrative
    assert "Unresolved questions: Deploy target?" in narrative


def test_format_compacted_json_to_narrative_partial():
    """Missing optional fields are skipped."""
    narrative = format_compacted_json_to_narrative({"goal": "g"})
    assert narrative == "Goal: g"


def test_build_new_context_reference_implementation():
    """_build_new_context: system at top, recap as assistant, keep zone appended."""
    keep = [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    new_history = _build_new_context("sys", {"goal": "g"}, keep)
    assert new_history == [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "[RECAP OF PRIOR WORK] Goal: g"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]


def test_build_new_context_without_system_prompt():
    """With no system prompt (-Z) the recap leads the new context."""
    new_history = _build_new_context(
        None, {"goal": "g"}, [{"role": "user", "content": "u"}]
    )
    assert new_history[0]["role"] == "assistant"
    assert new_history[0]["content"] == "[RECAP OF PRIOR WORK] Goal: g"


def test_min_compact_tokens_threshold_constant():
    """The guard threshold is the documented 2,000 tokens."""
    assert MIN_COMPACT_TOKENS == 2000


def test_keep_turns_constant():
    """The keep-zone size is the documented 3 turns."""
    assert KEEP_TURNS == 3
