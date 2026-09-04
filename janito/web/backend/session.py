"""Conversation session management for the web backend."""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from .config import WebServerConfig
from .session_store import (
    delete_session_file,
    load_session,
    load_sessions,
    save_session,
)

logger = logging.getLogger(__name__)


@dataclass
class ConversationSession:
    """A single conversation with its message history."""

    session_id: str
    messages: list[dict] = field(default_factory=list)
    system_prompt: str | None = None
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    title: str = "New conversation"
    # Provider/model selected for this conversation. Once the first user
    # message is sent these values are immutable for the lifetime of the
    # session, so switching tabs cannot silently change its context.
    provider: str | None = None
    model: str | None = None
    # History lengths recorded each time a new user prompt is about to be
    # sent (the length of ``messages`` before that turn); cancel (Ctrl+C)
    # and error recovery roll back to the most recent one.  Mirrors the
    # shell's ``history_turns`` attribute.
    history_turns: list[int] = field(default_factory=list)

    def touch(self) -> None:
        self.last_active = time.time()

    def restart(self) -> None:
        """Clear conversation history, preserving the system prompt.

        Mirrors the shell's F2 / ``clear`` behaviour: the system prompt
        is kept (so the AI retains its instructions) while all user/assistant
        messages are discarded.
        """
        if self.system_prompt:
            self.messages = [{"role": "system", "content": self.system_prompt}]
        else:
            self.messages = []
        # A fresh conversation has no recorded turns yet: they are
        # each time a user prompt is about to be sent (see _run_turn).
        self.history_turns = []
        self.touch()

    def to_summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "last_active": self.last_active,
            "provider": self.provider,
            "model": self.model,
            "has_messages": any(m.get("role") == "user" for m in self.messages),
        }

    def to_dict(self) -> dict:
        return {
            **self.to_summary(),
            "messages": self.messages,
            "system_prompt": self.system_prompt,
            "provider": self.provider,
            "model": self.model,
        }


class SessionManager:
    """Store of active sessions, persisted to disk.

    Each session's conversation history is mirrored to
    ``./.janito/sessions/<session_id>/metadata.json`` (see
    :mod:`janito.web.backend.session_store`) so conversations survive a
    server restart. Persistence is skipped entirely when
    ``config.no_history`` is set (``--no-history``).

    TTL expiry is optional (``config.session_ttl``, ``--web-session-ttl``;
    ``0`` = disabled, the default). When enabled, a session idle longer
    than the TTL is evicted from memory *lazily* (on ``get`` /
    ``list_sessions``, no background task) and transparently restored from
    disk on the next ``get()``, so the UI never sees a 404 and the
    conversation is never lost. TTL is force-disabled under ``--no-history``
    because there is no disk mirror to reload an evicted session from.
    """

    def __init__(self, config: WebServerConfig, ttl_seconds: int | None = None):
        self.config = config
        # TTL in seconds; ``0``/``None`` disables expiry. ``None`` follows
        # the config value (default 0).
        self.ttl_seconds = config.session_ttl if ttl_seconds is None else ttl_seconds
        self._sessions: dict[str, ConversationSession] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _effective_ttl(self) -> int:
        """The TTL in seconds actually enforced, or ``0`` when disabled.

        Disabled when the configured TTL is ``0`` (the default) or when
        ``config.no_history`` is set: with no disk mirror, evicting a
        session from memory would make it a hard 404 with nothing to
        reload, so reaping is never attempted.
        """
        if self.config.no_history:
            return 0
        return self.ttl_seconds or 0

    @staticmethod
    def _session_from_meta(meta: dict) -> "ConversationSession":
        """Build a ConversationSession from a persisted metadata dict."""
        session = ConversationSession(
            session_id=meta["session_id"],
            messages=meta.get("messages", []),
            system_prompt=meta.get("system_prompt"),
            created_at=meta.get("created_at", time.time()),
            last_active=meta.get("last_active", time.time()),
            title=meta.get("title", "New conversation"),
            provider=meta.get("provider"),
            model=meta.get("model"),
        )
        # A fresh conversation has no recorded turns yet: they are recorded
        # each time a user prompt is about to be sent (see _run_turn).
        session.history_turns = []
        return session

    def _load_from_disk(self, session_id: str) -> "ConversationSession | None":
        """Load a single session from disk (TTL lazy-reload path)."""
        meta = load_session(session_id)
        if meta is None:
            return None
        return self._session_from_meta(meta)

    def _persist(self, session: ConversationSession) -> None:
        """Write the session to disk unless ``--no-history`` was passed."""
        if self.config.no_history:
            return
        save_session(session)

    def load_from_disk(self) -> int:
        """Restore persisted sessions from ``.janito/sessions/``.

        Called once at server startup so conversations survive a restart.
        Returns the number of sessions restored (0 with ``--no-history``).
        """
        if self.config.no_history:
            return 0
        loaded = 0
        for meta in load_sessions():
            session = self._session_from_meta(meta)
            with self._lock:
                self._sessions[session.session_id] = session
            loaded += 1
        if loaded:
            logger.info(f"Restored {loaded} session(s) from disk")
        return loaded

    def create(self) -> ConversationSession:
        """Create a new session with the effective system prompt."""
        session_id = uuid.uuid4().hex[:12]
        system_prompt = self.config.get_effective_system_prompt()

        messages: list[dict] = []
        if system_prompt and not self.config.no_system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        session = ConversationSession(
            session_id=session_id,
            messages=messages,
            system_prompt=system_prompt,
        )
        # A fresh conversation has no recorded turns yet: they are
        # each time a user prompt is about to be sent (see _run_turn).
        session.history_turns = []
        with self._lock:
            self._sessions[session_id] = session
        self._persist(session)
        return session

    def get(self, session_id: str) -> ConversationSession | None:
        """Return a session, lazily evicting + reloading idle ones.

        A session idle past the TTL is dropped from memory (lazy reaping —
        no background task) and transparently restored from
        ``.janito/sessions/`` so the caller still gets the conversation and
        the frontend never sees a 404. Reloading counts as activity, so a
        just-reopened session is not immediately reaped again. With TTL
        disabled (or ``--no-history``) this is a plain dict lookup.
        """
        ttl = self._effective_ttl()
        with self._lock:
            session = self._sessions.get(session_id)
            if session and ttl and (time.time() - session.last_active) > ttl:
                # Idle past the TTL: drop it from memory and treat it as a
                # miss so it is transparently reloaded from disk below.
                del self._sessions[session_id]
                session = None
        if session:
            session.touch()
            return session
        if ttl:
            # Only reload when TTL is enabled: with it disabled a miss is a
            # genuine 404 (matches pre-TTL behaviour), and reloading on
            # every unknown id would hide bugs behind disk I/O.
            session = self._load_from_disk(session_id)
            if session:
                with self._lock:
                    self._sessions[session_id] = session
                session.touch()  # reopening a session is activity
                return session
        return None

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                removed = True
            else:
                removed = False
        if removed and not self.config.no_history:
            delete_session_file(session_id)
        return removed

    def list_sessions(self) -> list[ConversationSession]:
        """List active sessions, reclaiming idle ones first.

        With TTL enabled, sessions idle past the TTL are evicted from
        memory here so the sidebar session list actually shrinks (they are
        still on disk and come back on the next ``get()``).
        """
        ttl = self._effective_ttl()
        now = time.time()
        with self._lock:
            if ttl:
                expired = [
                    sid
                    for sid, s in self._sessions.items()
                    if (now - s.last_active) > ttl
                ]
                for sid in expired:
                    del self._sessions[sid]
            return list(self._sessions.values())

    def set_title(self, session_id: str, title: str) -> bool:
        session = self.get(session_id)
        if session:
            session.title = title[:120]
            self._persist(session)
            return True
        return False

    def persist(self, session: ConversationSession) -> None:
        """Persist a session's current state (called after each turn).

        The chat router calls this after a turn completes — normally, on
        cancel (rollback), on error (rollback), and after a restart — so
        the on-disk copy always mirrors the in-memory conversation.
        """
        self._persist(session)
