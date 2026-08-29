"""
Tests for the pluggable TurnObserver protocol.

The API clients route every user-visible event through a
:class:`~janito.agent.observer.TurnObserver` so ``Client.send`` itself stays
UI-free: the default resolves to the headless
:class:`~janito.agent.observer.NullObserver`, and the CLI injects the Rich
observer (:class:`~janito.openai_client.client_support.RichTurnObserver`)
through ``_make_send_prompt_func``.  These tests pin the protocol surface,
the headless default, the Rich observer's rendering and its error dispatch.
"""

import re
import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from io import StringIO  # noqa: E402

from rich.console import Console  # noqa: E402

from janito.agent.observer import NullObserver  # noqa: E402
from janito.openai_client.base_client import Client  # noqa: E402
from janito.openai_client.client_support import RichTurnObserver  # noqa: E402

_PROTOCOL_METHODS = (
    "on_reasoning",
    "on_message",
    "on_verbose_info",
    "on_verbose_call",
    "on_verbose_response",
    "on_error",
    "on_turn_complete",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _PlainBuffer:
    """A StringIO whose ``getvalue()`` strips ANSI escape sequences, so the
    tests can assert on the plain text regardless of Rich's styling/highlighting."""

    def __init__(self):
        self._buf = StringIO()

    def getvalue(self) -> str:
        return _ANSI_RE.sub("", self._buf.getvalue())


class TestTurnObserverProtocol:
    def test_null_observer_implements_full_surface(self):
        obs = NullObserver()
        for name in _PROTOCOL_METHODS:
            assert callable(getattr(obs, name, None)), name

    def test_rich_observer_implements_full_surface(self):
        obs = RichTurnObserver()
        for name in _PROTOCOL_METHODS:
            assert callable(getattr(obs, name, None)), name

    def test_null_observer_drops_every_event(self):
        obs = NullObserver()
        # Every protocol method must be callable without raising.
        obs.on_reasoning("think")
        obs.on_message("hello")
        obs.on_verbose_info(
            base_url=None, model="m", mcp_manager=None, backend_default="api.openai.com"
        )
        obs.on_verbose_call({}, [])
        obs.on_verbose_response("hi", None, None, None, None)
        obs.on_error(ValueError("boom"), error_kind="unknown")
        obs.on_turn_complete(None)

    def test_client_defaults_to_null_observer(self):
        # A config with no observer resolves to NullObserver: the headless
        # default produces no terminal output.
        from conftest import make_config

        assert isinstance(Client(make_config()).observer, NullObserver)

    def test_client_accepts_injected_observer(self):
        from conftest import make_config

        obs = NullObserver()
        assert Client(make_config(observer=obs)).observer is obs


class TestRichTurnObserver:
    def _make(self):
        buf = _PlainBuffer()
        observer = RichTurnObserver(
            console=Console(file=buf._buf, width=120, force_terminal=True)
        )
        return observer, buf

    def test_on_reasoning_renders_panel(self):
        obs, buf = self._make()
        obs.on_reasoning("step 1")
        rendered = buf.getvalue()
        assert "Reasoning" in rendered
        assert "step 1" in rendered

    def test_on_reasoning_empty_is_silent(self):
        obs, buf = self._make()
        obs.on_reasoning("")
        assert buf.getvalue() == ""

    def test_on_message_renders_markdown(self):
        obs, buf = self._make()
        obs.on_message("**bold** text")
        assert "bold" in buf.getvalue()

    def test_on_message_empty_is_silent(self):
        obs, buf = self._make()
        obs.on_message("")
        assert buf.getvalue() == ""

    def test_on_verbose_info_renders_banner(self):
        obs, buf = self._make()
        obs.on_verbose_info(
            base_url="https://api.example.com",
            model="gpt-4",
            mcp_manager=None,
            backend_default="api.openai.com",
        )
        rendered = buf.getvalue()
        assert "Model: gpt-4" in rendered
        assert "Backend: https://api.example.com" in rendered

    def test_on_verbose_call_renders_request_panel(self):
        obs, buf = self._make()
        obs.on_verbose_call({"model": "gpt-4", "input": "hello"}, [])
        assert "API Call" in buf.getvalue()

    def test_on_verbose_response_renders_summary(self):
        obs, buf = self._make()
        obs.on_verbose_response("answer", "think", None, None, "resp_1")
        rendered = buf.getvalue()
        assert "API Response" in rendered
        assert "Response id: resp_1" in rendered

    def test_on_error_dispatches_unknown_model(self):
        obs, buf = self._make()
        e = Exception("Model not exist: `gpt-4`")
        obs.on_error(e, base_url=None, model="gpt-4", error_kind="not_found")
        rendered = buf.getvalue()
        assert "Model not found" in rendered
        assert "gpt-4" in rendered

    def test_on_error_dispatches_stale_previous_response(self):
        obs, buf = self._make()
        e = Exception("previous response id not found")
        obs.on_error(
            e,
            base_url=None,
            model="gpt-4",
            response_id="resp_old",
            error_kind="not_found",
        )
        rendered = buf.getvalue()
        assert "Conversation state not found" in rendered
        assert "previous_response_id=None" in rendered

    def test_on_error_dispatches_auth_failure(self):
        obs, buf = self._make()
        e = Exception("401 authentication failed")
        e.status_code = 401
        obs.on_error(
            e,
            provider="openai",
            api_key="sk-test",  # pragma: allowlist secret
            base_url=None,
            model="gpt-4",
            error_kind="auth",
        )
        rendered = buf.getvalue()
        assert "Authentication failed" in rendered
        assert "gpt-4" in rendered

    def test_on_error_unknown_kind_stays_silent(self):
        """An ``"unknown"`` classification prints nothing (the caller always
        re-raises)."""
        obs, buf = self._make()
        e = Exception("502 upstream timeout")
        obs.on_error(
            e,
            provider="openai",
            api_key="sk-test",  # pragma: allowlist secret
            base_url=None,
            model="gpt-4",
            error_kind="unknown",
        )
        assert buf.getvalue() == ""

    def test_on_error_no_kind_stays_silent(self):
        """No classification renders nothing: dispatch is always explicit."""
        obs, buf = self._make()
        e = Exception("Model not found: `gpt-4`")
        obs.on_error(e, base_url=None, model="gpt-4")
        assert buf.getvalue() == ""
