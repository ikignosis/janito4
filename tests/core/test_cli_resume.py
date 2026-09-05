"""
Tests for the CLI resume feature plumbing (-C/--continue).

Covers the session-identity matching helpers in :mod:`janito.cli.chat`
(``_normalize_identity`` / ``_resume_identity_matches``) and the
:func:`janito.__main__._apply_resume_session` backfill that reuses the saved
session's provider / model / API type / thinking / effort so the restored
conversation stays API-compatible.
"""

import argparse
import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import janito.__main__ as _main
import janito.shell.persistence as persistence
from janito.cli.chat import (
    _normalize_identity,
    _resume_identity_matches,
    _resolve_resume,
)


def _args(**kw):
    ns = argparse.Namespace()
    defaults = dict(
        continue_session=True,
        web=False,
        no_history=False,
        provider=None,
        model=None,
        api_type=None,
        reasoning_effort=None,
        thinking=False,
    )
    defaults.update(kw)
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


def _snapshot(**kw):
    state = {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "api_type": "Responses",
        "thinking": True,
        "reasoning_effort": "high",
    }
    state.update(kw)
    return state


# ---------------------------------------------------------------------------
# Identity matching (janito.cli.chat)
# ---------------------------------------------------------------------------


def test_normalize_identity():
    assert _normalize_identity(None) is None
    assert _normalize_identity("") is None
    assert _normalize_identity("OpenAI") == "openai"


def test_resume_identity_matches_case_insensitively():
    state = _snapshot()
    assert (
        _resume_identity_matches(state, "OpenAI", "GPT-5.6-LUNA", "responses") is True
    )


def test_resume_identity_matches_requires_api_type():
    state = _snapshot()
    # Missing/None API type never matches a recorded one.
    assert _resume_identity_matches(state, "openai", "gpt-5.6-luna", None) is False
    assert (
        _resume_identity_matches(state, "openai", "gpt-5.6-luna", "Completions")
        is False
    )


def test_resume_identity_matches_false_on_provider_or_model_mismatch():
    state = _snapshot()
    assert (
        _resume_identity_matches(state, "anthropic", "gpt-5.6-luna", "Responses")
        is False
    )
    assert _resume_identity_matches(state, "openai", "gpt-4o", "Responses") is False


def test_resume_identity_matches_when_both_none_api_types():
    # Two sessions with no recorded API type are considered equal.
    assert _resume_identity_matches(
        {"provider": "openai", "model": "m", "api_type": None},
        "openai",
        "m",
        None,
    )


# ---------------------------------------------------------------------------
# _apply_resume_session (janito.__main__)
# ---------------------------------------------------------------------------


def test_apply_resume_noop_without_snapshot(monkeypatch):
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: None)
    args = _args()
    _main._apply_resume_session(args)
    assert args.provider is None
    assert args.model is None
    assert args.api_type is None


def test_apply_resume_backfills_identity(monkeypatch):
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: _snapshot())
    args = _args()
    _main._apply_resume_session(args)
    assert args.provider == "openai"
    assert args.model == "gpt-5.6-luna"
    assert args.api_type == "Responses"
    assert args.thinking is True
    assert args.reasoning_effort == "high"


def test_apply_resume_explicit_flags_win(monkeypatch):
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: _snapshot())
    args = _args(
        provider="anthropic",
        model="claude-sonnet",
        api_type="Anthropic",
        reasoning_effort="low",
        thinking=True,
    )
    _main._apply_resume_session(args)
    assert args.provider == "anthropic"
    assert args.model == "claude-sonnet"
    assert args.api_type == "Anthropic"
    assert args.reasoning_effort == "low"
    # The explicit --thinking flag stays as passed (only absent flags are
    # backfilled from the snapshot; --thinking is store_true, so it is either
    # absent/False or explicitly True).
    assert args.thinking is True


def test_apply_resume_noop_under_no_history(monkeypatch):
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: _snapshot())
    args = _args(no_history=True)
    _main._apply_resume_session(args)
    assert args.provider is None


def test_apply_resume_noop_in_web_mode(monkeypatch):
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: _snapshot())
    args = _args(web=True)
    _main._apply_resume_session(args)
    assert args.provider is None


def test_apply_resume_noop_without_continue_flag(monkeypatch):
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: _snapshot())
    args = _args(continue_session=False)
    _main._apply_resume_session(args)
    assert args.provider is None


# ---------------------------------------------------------------------------
# _resolve_resume (janito.cli.chat): decides restore vs fresh + persistence
# ---------------------------------------------------------------------------


def test_resolve_resume_ignored_without_continue_flag():
    args = _args(continue_session=False, no_history=False)
    state, persist = _resolve_resume(args, "openai", "gpt-5.6-luna", "Responses")
    assert state is None
    assert persist is True


def test_resolve_resume_disabled_under_no_history(capsys):
    args = _args(continue_session=True, no_history=True)
    state, persist = _resolve_resume(args, "openai", "gpt-5.6-luna", "Responses")
    assert state is None
    assert persist is False
    assert "no-history" in capsys.readouterr().out


def test_resolve_resume_no_snapshot_starts_fresh(monkeypatch, capsys):
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: None)
    args = _args(continue_session=True, no_history=False)
    state, persist = _resolve_resume(args, "openai", "gpt-5.6-luna", "Responses")
    assert state is None
    assert persist is True
    assert "No previous conversation" in capsys.readouterr().out


def test_resolve_resume_restores_on_identity_match(monkeypatch):
    snapshot = _snapshot(api_type="Responses")
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: snapshot)
    args = _args(continue_session=True, no_history=False)
    state, persist = _resolve_resume(args, "openai", "gpt-5.6-luna", "Responses")
    assert state is snapshot
    assert persist is True


def test_resolve_resume_mismatch_starts_fresh_without_persisting(monkeypatch, capsys):
    snapshot = _snapshot(api_type="Responses")
    monkeypatch.setattr(persistence, "load_conversation_state", lambda: snapshot)
    args = _args(continue_session=True, no_history=False)
    # The session identity differs (API type), so the conversation cannot be
    # restored; the fresh session must not overwrite the saved snapshot.
    state, persist = _resolve_resume(args, "openai", "gpt-5.6-luna", "Completions")
    assert state is None
    assert persist is False
    assert "does not match" in capsys.readouterr().out
