"""Tests for web session TTL reaping (issue #93).

The web backend advertises optional TTL-based expiry (``--web-session-ttl``):
sessions idle longer than the TTL are evicted from memory *lazily* (on
``get`` / ``list_sessions``, no background task) and transparently reloaded
from ``.janito/sessions/`` on the next ``get()``, so the UI never sees a
404. TTL is disabled by default (``0``) and force-disabled under
``--no-history`` (there is no disk mirror to reload an evicted session
from).

These tests pin down:

1. an idle session past the TTL is reclaimed from memory (the list shrinks);
2. reopening its ``session_id`` restores it from disk (no 404, history
   intact) and it reappears in the list;
3. TTL=0 (the default) keeps today's behaviour: nothing is ever evicted;
4. ``--no-history`` force-disables TTL even when one is configured;
5. a recently-active session is never evicted;
6. ``get()`` on a session id that never existed still 404s with TTL on
   (the disk fallback only applies to sessions that exist on disk).
"""

import sys
import time
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

# The web routes need the optional `web` extra (fastapi). Skip gracefully
# when fastapi is not installed (e.g. minimal tox envs).
try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient

    _HAS_FASTAPI = True
except ModuleNotFoundError:
    _HAS_FASTAPI = False

requires_fastapi = pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi (web extra) is not installed")


def _patch_config_start(monkeypatch, start=None):
    """Pin load_system_prompt_start so tests never touch the real config."""
    import janito.config_loaders as config_loaders_mod

    monkeypatch.setattr(config_loaders_mod, "load_system_prompt_start", lambda: (start, None))


@pytest.fixture()
def isolated_cwd(tmp_path, monkeypatch):
    """Run the server in a temp CWD so ``.janito/sessions/`` lands there."""
    _patch_config_start(monkeypatch)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _make_client(isolated_cwd, **config_kwargs):
    """A TestClient wired to a fresh Janito web app (temp CWD).

    ``config_kwargs`` are passed to ``WebServerConfig`` (e.g.
    ``session_ttl=50``, ``no_history=True``).
    """
    from janito.web.backend.app import create_app
    from janito.web.backend.config import WebServerConfig

    config = WebServerConfig(web_host="127.0.0.1", web_port=0, no_web_open=True, **config_kwargs)
    return TestClient(create_app(config))


@requires_fastapi
def test_idle_session_reclaimed_and_lazily_reloaded(isolated_cwd):
    """Idle past TTL: gone from the list, restored from disk on GET."""
    with _make_client(isolated_cwd, session_ttl=50) as c:
        session_id = c.post("/api/chat/sessions").json()["session_id"]
        sessions = c.app.state.sessions

        # Add some history so the reload is observable.
        session = sessions.get(session_id)
        session.messages.append({"role": "user", "content": "hello"})
        sessions.persist(session)

        # Simulate the session sitting idle past the TTL.
        session.last_active = time.time() - 100

        # list_sessions() sweeps it out of memory...
        listed = c.get("/api/chat/sessions").json()["sessions"]
        assert [s["session_id"] for s in listed] == []

        # ...but GET /sessions/{id} transparently restores it from disk.
        history = c.get(f"/api/chat/sessions/{session_id}")
        assert history.status_code == 200
        assert history.json()["messages"][-1]["content"] == "hello"

        # Reopening is activity, so it is back in the sidebar list.
        listed = c.get("/api/chat/sessions").json()["sessions"]
        assert [s["session_id"] for s in listed] == [session_id]


@requires_fastapi
def test_manager_reload_restores_full_history(isolated_cwd):
    """An evicted session reloads with its full persisted state."""
    from janito.web.backend.config import WebServerConfig
    from janito.web.backend.session import SessionManager

    config = WebServerConfig(web_host="127.0.0.1", web_port=0, no_web_open=True, session_ttl=50)
    manager = SessionManager(config)
    session = manager.create()
    session.messages.append({"role": "user", "content": "hello"})
    session.messages.append({"role": "assistant", "content": "world"})
    session.title = "ttl title"
    manager.persist(session)
    session.last_active = time.time() - 100

    # Evicted from memory by the sweep...
    assert manager.list_sessions() == []

    # ...reopened from disk with title + full history.
    restored = manager.get(session.session_id)
    assert restored is not None
    assert restored.title == "ttl title"
    non_system = [m["content"] for m in restored.messages if m.get("role") != "system"]
    assert non_system == ["hello", "world"]


@requires_fastapi
def test_ttl_zero_keeps_today_behavior(isolated_cwd):
    """Default TTL=0: idle sessions are never evicted."""
    with _make_client(isolated_cwd) as c:  # session_ttl defaults to 0
        session_id = c.post("/api/chat/sessions").json()["session_id"]
        sessions = c.app.state.sessions
        sessions.get(session_id).last_active = time.time() - 10_000

        listed = c.get("/api/chat/sessions").json()["sessions"]
        assert [s["session_id"] for s in listed] == [session_id]
        assert c.get(f"/api/chat/sessions/{session_id}").status_code == 200


@requires_fastapi
def test_no_history_disables_ttl(isolated_cwd):
    """--no-history + TTL set: nothing is ever evicted (no disk to reload)."""
    with _make_client(isolated_cwd, no_history=True, session_ttl=50) as c:
        session_id = c.post("/api/chat/sessions").json()["session_id"]
        sessions = c.app.state.sessions
        sessions.get(session_id).last_active = time.time() - 100

        listed = c.get("/api/chat/sessions").json()["sessions"]
        assert [s["session_id"] for s in listed] == [session_id]
        assert c.get(f"/api/chat/sessions/{session_id}").status_code == 200


@requires_fastapi
def test_active_session_not_evicted(isolated_cwd):
    """A recently-active session stays listed under TTL."""
    with _make_client(isolated_cwd, session_ttl=50) as c:
        session_id = c.post("/api/chat/sessions").json()["session_id"]
        sessions = c.app.state.sessions
        sessions.get(session_id).last_active = time.time() - 10  # still fresh

        listed = c.get("/api/chat/sessions").json()["sessions"]
        assert [s["session_id"] for s in listed] == [session_id]


@requires_fastapi
def test_unknown_session_still_404s_with_ttl(isolated_cwd):
    """TTL disk fallback only applies to sessions that exist on disk."""
    with _make_client(isolated_cwd, session_ttl=50) as c:
        assert c.get("/api/chat/sessions/does-not-exist").status_code == 404
