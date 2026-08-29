"""
Tests for the GetUrl tool's oversized-content handling and llms.txt discovery.

When fetched content exceeds a threshold (default 10k characters), the tool
stores the full content in a temporary file and returns a pointer message
instead of the (huge) inline payload. The temporary files are tracked and
removed when the janito process exits.

Before fetching a site URL the tool also probes for an ``llms.txt`` site map at
the root and ``.well-known`` locations using lightweight HEAD requests; when
one is found its content is returned as-is (no Markdown parsing) instead of
the requested page. Unlike regular fetches, llms.txt content is never stored
to a temporary file - even when oversized, it is returned inline in full.

These tests spin up a local HTTP server (no external network access) to serve
both small and oversized payloads and to emulate llms.txt discovery.
"""

import http.server
import socketserver
import sys
import threading
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import os

import pytest

from janito.tools.net import get_url as get_url_module
from janito.tools.net.get_url import BIG_CONTENT_THRESHOLD, GetUrl, _cleanup_temp_files

SMALL_PAYLOAD = "hello small world"
BIG_PAYLOAD = "x" * (BIG_CONTENT_THRESHOLD + 5000)  # clearly over the threshold
LLMS_PAYLOAD = "# Docs\n\n> Example site map\n\n- [Home](https://example.com/)\n- [Guide](https://example.com/guide)\n"


class _Handler(http.server.BaseHTTPRequestHandler):
    """Configurable handler for the test server.

    Class attributes are re-configured per test:
      - routes: mapping path -> (status, payload); wins over everything else
      - default_status / default_payload: used for any other path
      - llms_404: when True (default), the llms.txt locations answer 404 unless
        explicitly present in ``routes`` (keeps discovery off unless a test
        enables it)
      - requests: (method, path) pairs seen by the server, for assertions
    """

    routes: dict[str, tuple[int, str]] = {}
    default_status: int = 200
    default_payload: str = "not found"
    llms_404: bool = True
    requests: list[tuple[str, str]] = []

    def _respond(self, include_body: bool) -> None:
        _Handler.requests.append((self.command, self.path))
        if self.path in self.routes:
            status, payload = self.routes[self.path]
        elif self.llms_404 and self.path in (
            "/llms.txt",
            "/.well-known/llms.txt",
        ):
            status, payload = 404, "not found"
        else:
            status, payload = self.default_status, self.default_payload
        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data) if include_body else 0))
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        self._respond(include_body=True)

    def do_HEAD(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        self._respond(include_body=False)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture()
def server():
    """Start a local HTTP server with a fresh handler configuration."""
    _Handler.routes = {"/big": (200, BIG_PAYLOAD)}
    _Handler.default_status = 200
    _Handler.default_payload = SMALL_PAYLOAD
    _Handler.llms_404 = True
    _Handler.requests = []

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def _clean_temp_registry():
    """Ensure no tracked temp files leak between tests."""
    yield
    _cleanup_temp_files()


def test_big_content_stored_to_temp_file(server):
    """Oversized content must be written to a temp file and reported via message."""
    tool = GetUrl()
    result = tool.run(url=f"{server}/big")

    assert result["success"] is True
    assert result.get("too_big") is True

    tmp_filename = result["tmp_filename"]
    assert os.path.isfile(tmp_filename)

    # The stored file must contain the full oversized payload.
    with open(tmp_filename, encoding="utf-8") as fh:
        assert fh.read() == BIG_PAYLOAD

    # The returned message must point to the temp file, and there must be no
    # inline content payload.
    assert tmp_filename in result["message"]
    assert "Content was too big, stored at" in result["message"]
    assert "content" not in result

    # The file must have been registered for cleanup on exit.
    assert tmp_filename in get_url_module._TEMP_FILES


def test_small_content_returned_inline(server):
    """Content below the threshold is returned inline as before."""
    tool = GetUrl()
    result = tool.run(url=f"{server}/small")

    assert result["success"] is True
    assert result.get("too_big") is None
    assert result.get("content") == SMALL_PAYLOAD
    assert "tmp_filename" not in result


def test_threshold_none_disables_temp_file(server):
    """Passing threshold=None disables the temp-file behaviour (limits still apply)."""
    tool = GetUrl()
    # With no threshold but a max_length, big content is truncated inline.
    result = tool.run(url=f"{server}/big", threshold=None, max_length=100)

    assert result["success"] is True
    assert result.get("too_big") is None
    assert "content" in result
    assert result["content"].endswith("... [truncated]")
    assert "tmp_filename" not in result


def test_cleanup_removes_temp_files(server):
    """_cleanup_temp_files() must delete every tracked temp file."""
    tool = GetUrl()
    result = tool.run(url=f"{server}/big")
    tmp_filename = result["tmp_filename"]

    assert os.path.isfile(tmp_filename)
    _cleanup_temp_files()
    assert not os.path.exists(tmp_filename)
    assert tmp_filename not in get_url_module._TEMP_FILES


# ---------------------------------------------------------------------------
# llms.txt discovery
# ---------------------------------------------------------------------------


def _set_routes(
    routes=None, *, default_status=200, default_payload=SMALL_PAYLOAD, llms_404=True
):
    """Re-configure the handler class attributes for one test."""
    _Handler.routes = dict(routes or {})
    _Handler.default_status = default_status
    _Handler.default_payload = default_payload
    _Handler.llms_404 = llms_404
    _Handler.requests = []


def test_discovery_found_at_root(server):
    """When <origin>/llms.txt exists it is fetched via GET and returned as-is."""
    _set_routes({"/llms.txt": (200, LLMS_PAYLOAD)})

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide")

    assert result["success"] is True
    assert result.get("llms_txt") is True
    assert result["content"] == LLMS_PAYLOAD  # returned as-is, no parsing
    assert result["url"] == f"{server}/llms.txt"
    assert result["original_url"] == f"{server}/guide"

    # Discovery probed with HEAD, then fetched with GET - never GET the page.
    assert ("HEAD", "/llms.txt") in _Handler.requests
    assert ("GET", "/llms.txt") in _Handler.requests
    assert all(
        method != "GET" or path != "/guide" for method, path in _Handler.requests
    )


def test_discovery_well_known_fallback(server):
    """When root llms.txt is missing, .well-known/llms.txt is tried next."""
    _set_routes({"/.well-known/llms.txt": (200, LLMS_PAYLOAD)})

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide")

    assert result["success"] is True
    assert result.get("llms_txt") is True
    assert result["content"] == LLMS_PAYLOAD
    assert result["url"] == f"{server}/.well-known/llms.txt"

    assert ("HEAD", "/llms.txt") in _Handler.requests
    assert ("HEAD", "/.well-known/llms.txt") in _Handler.requests
    assert ("GET", "/.well-known/llms.txt") in _Handler.requests


def test_discovery_root_takes_priority(server):
    """Root llms.txt wins over .well-known when both exist."""
    _set_routes(
        {
            "/llms.txt": (200, "root map"),
            "/.well-known/llms.txt": (200, "well-known map"),
        }
    )

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide")

    assert result["success"] is True
    assert result.get("llms_txt") is True
    assert result["content"] == "root map"
    assert result["url"] == f"{server}/llms.txt"
    # The well-known location must never have been probed.
    assert not any(path == "/.well-known/llms.txt" for _, path in _Handler.requests)


def test_discovery_not_found_falls_back(server):
    """When no llms.txt exists, the requested URL is fetched normally."""
    _set_routes({})

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide")

    assert result["success"] is True
    assert result.get("llms_txt") is None
    assert result["content"] == SMALL_PAYLOAD
    assert result["url"] == f"{server}/guide"
    # Both locations were probed with HEAD before falling back.
    assert ("HEAD", "/llms.txt") in _Handler.requests
    assert ("HEAD", "/.well-known/llms.txt") in _Handler.requests
    assert ("GET", "/guide") in _Handler.requests


def test_llms_txt_url_skips_discovery(server):
    """Fetching an llms.txt URL directly must not trigger discovery recursion."""
    _set_routes({"/llms.txt": (200, LLMS_PAYLOAD)})

    tool = GetUrl()
    result = tool.run(url=f"{server}/llms.txt")

    assert result["success"] is True
    assert result.get("llms_txt") is None  # fetched as a plain URL
    assert result["content"] == LLMS_PAYLOAD
    # Exactly one request: the direct GET of the llms.txt URL.
    assert _Handler.requests == [("GET", "/llms.txt")]


def test_llms_txt_too_big_returned_inline(server):
    """Oversized llms.txt content is returned inline in full, never stored to a file."""
    _set_routes({"/llms.txt": (200, BIG_PAYLOAD)})

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide")

    assert result["success"] is True
    assert result.get("llms_txt") is True
    assert result.get("too_big") is None
    assert "tmp_filename" not in result
    assert result["content"] == BIG_PAYLOAD  # full payload, no temp file
    assert "Content was too big, stored at" not in result.get("message", "")


def test_llms_txt_never_truncated(server):
    """llms.txt content is returned in full, ignoring max_length/max_lines and the threshold."""
    long_lines = "\n".join(f"- [Page {i}](https://example.com/{i})" for i in range(500))
    long_map = f"# Docs\n\n{long_lines}\n"
    assert len(long_map) > 5000  # would exceed default max_length
    assert long_map.count("\n") > 200  # would exceed default max_lines

    _set_routes({"/llms.txt": (200, long_map)})

    tool = GetUrl()
    # Tiny max_length/max_lines and the default threshold must not truncate or
    # store the site map - it is always returned inline in full.
    result = tool.run(url=f"{server}/guide", max_length=100, max_lines=5)

    assert result["success"] is True
    assert result.get("llms_txt") is True
    assert result.get("too_big") is None
    assert "tmp_filename" not in result
    assert result["content"] == long_map
    assert "... [truncated]" not in result["content"]


def test_discovery_uses_head_requests(server):
    """Discovery probes must be lightweight HEAD requests (no body)."""
    _set_routes({"/llms.txt": (200, LLMS_PAYLOAD)})

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide")

    assert result["success"] is True
    # Only the root location was probed (found on the first try).
    assert [r for r in _Handler.requests if r[0] == "HEAD"] == [("HEAD", "/llms.txt")]
    assert [r for r in _Handler.requests if r[0] == "GET"] == [("GET", "/llms.txt")]


def test_not_found_does_not_report_llms_txt(server):
    """When discovery fails, nothing about llms.txt is reported."""
    from janito.tooling.reporter import set_report_handler

    _set_routes({})

    captured: list[tuple[str, str]] = []

    def handler(level: str, message: str, end: str) -> None:
        captured.append((level, message))

    set_report_handler(handler)
    try:
        tool = GetUrl()
        result = tool.run(url=f"{server}/guide")
        assert result["success"] is True
    finally:
        set_report_handler(None)

    messages = [m for _, m in captured]
    assert not any("llms.txt" in m for m in messages)


def test_found_reports_retrieved(server):
    """When llms.txt is found, its retrieval is reported (and only once)."""
    from janito.tooling.reporter import set_report_handler

    _set_routes({"/llms.txt": (200, LLMS_PAYLOAD)})

    captured: list[tuple[str, str]] = []

    def handler(level: str, message: str, end: str) -> None:
        captured.append((level, message))

    set_report_handler(handler)
    try:
        tool = GetUrl()
        result = tool.run(url=f"{server}/guide")
        assert result["success"] is True
    finally:
        set_report_handler(None)

    llms_msgs = [m for _, m in captured if "llms.txt" in m]
    assert len(llms_msgs) == 1
    assert "Retrieved llms.txt from" in llms_msgs[0]


# ---------------------------------------------------------------------------
# skip_llms_txt parameter
# ---------------------------------------------------------------------------


def test_skip_llms_txt_fetches_url_directly(server):
    """With skip_llms_txt=True the URL is fetched as-is even when llms.txt exists."""
    _set_routes({"/llms.txt": (200, LLMS_PAYLOAD)})

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide", skip_llms_txt=True)

    assert result["success"] is True
    assert result.get("llms_txt") is None  # fetched as a plain URL
    assert result["content"] == SMALL_PAYLOAD
    assert result["url"] == f"{server}/guide"

    # No HEAD probes and no GET of llms.txt - only the requested page.
    assert _Handler.requests == [("GET", "/guide")]


def test_skip_llms_txt_default_false(server):
    """Without the flag, discovery still runs and llms.txt is returned as before."""
    _set_routes({"/llms.txt": (200, LLMS_PAYLOAD)})

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide")

    assert result["success"] is True
    assert result.get("llms_txt") is True
    assert result["content"] == LLMS_PAYLOAD
    assert ("HEAD", "/llms.txt") in _Handler.requests
    assert ("GET", "/llms.txt") in _Handler.requests


def test_skip_llms_txt_no_llms_txt(server):
    """With no llms.txt present, the page is fetched directly with no HEAD probes."""
    _set_routes({})

    tool = GetUrl()
    result = tool.run(url=f"{server}/guide", skip_llms_txt=True)

    assert result["success"] is True
    assert result.get("llms_txt") is None
    assert result["content"] == SMALL_PAYLOAD
    assert result["url"] == f"{server}/guide"
    # No discovery probes at all - just the direct GET.
    assert _Handler.requests == [("GET", "/guide")]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
