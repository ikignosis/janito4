#!/usr/bin/env python3
"""
Get URL Tool - A class-based tool for fetching web content from URLs.

This tool demonstrates how to use the base tool class with progress reporting
for web requests.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito.tools.net.get_url [args]
For AI function calling, use through the tool registry (tooling.tools_registry).
"""

import atexit
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ...tooling import BaseTool, format_duration_ms
from ...tooling.decorator import tool

# Threshold (in characters) above which fetched content is written to a
# temporary file instead of being returned inline to the model. Returning very
# large payloads tends to blow up the model context / JSON result, so we store
# them on disk and hand back a pointer instead.
BIG_CONTENT_THRESHOLD = 10_000

# llms.txt site map discovery locations, checked in priority order:
# 1. root level: <origin>/llms.txt
# 2. well-known path: <origin>/.well-known/llms.txt
LLMS_TXT_ROOT_PATH = "/llms.txt"
LLMS_TXT_WELL_KNOWN_PATH = "/.well-known/llms.txt"

USER_AGENT = "Mozilla/5.0 (compatible; AI-Tool/1.0)"

# Temporary files created by GetUrl for oversized content. They are removed
# automatically when the janito process exits.
_TEMP_FILES: set[str] = set()
_atexit_registered = False


def _cleanup_temp_files() -> None:
    """Remove all temporary files created by GetUrl (called on process exit)."""
    for path in list(_TEMP_FILES):
        try:
            os.remove(path)
        except OSError:
            pass
        _TEMP_FILES.discard(path)


def _track_temp_file(path: str) -> None:
    """Register a temporary file for removal on process exit."""
    global _atexit_registered
    if not path:
        return
    _TEMP_FILES.add(path)
    if not _atexit_registered:
        atexit.register(_cleanup_temp_files)
        _atexit_registered = True


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """A redirect handler that disables automatic redirect following."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener(follow_redirects: bool) -> urllib.request.OpenerDirector:
    """Build an opener that respects the follow_redirects flag."""
    if follow_redirects:
        return urllib.request.build_opener()
    return urllib.request.build_opener(_NoRedirectHandler)


def _head_ok(url: str, timeout: int | None, follow_redirects: bool) -> bool:
    """Return True when a lightweight HEAD request to url answers 200 OK.

    Used for llms.txt discovery. Failures are silent (no reporting) so the
    caller simply falls back to its regular fetch behaviour.
    """
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", USER_AGENT)
        with _build_opener(follow_redirects).open(req, timeout=timeout) as response:
            return response.getcode() == 200
    except Exception:
        return False


def _discover_llms_txt(
    url: str,
    timeout: int | None,
    follow_redirects: bool,
) -> str | None:
    """Try to discover an llms.txt site map for url (silent, no reporting).

    Checks the root level (``<origin>/llms.txt``) first and, only if that
    fails, the well-known path (``<origin>/.well-known/llms.txt``). Each
    location is probed with a lightweight HEAD request; the first one answering
    200 OK is returned. Returns None when the requested URL is itself an
    llms.txt file or when no location exists (the caller then falls back to
    its regular fetch behaviour).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    path = (parsed.path or "").rstrip("/")
    if path in (LLMS_TXT_ROOT_PATH, LLMS_TXT_WELL_KNOWN_PATH):
        # Already fetching an llms.txt file itself - no discovery loop.
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for candidate in (
        f"{origin}{LLMS_TXT_ROOT_PATH}",
        f"{origin}{LLMS_TXT_WELL_KNOWN_PATH}",
    ):
        if _head_ok(candidate, timeout=timeout, follow_redirects=follow_redirects):
            return candidate
    return None


@tool(permissions="r")
class GetUrl(BaseTool):
    """
    Tool for fetching content from web URLs.

    This tool retrieves content from HTTP/HTTPS URLs and returns the response.
    Before fetching a site URL (a hostname or hostname/path) it tries to
    discover an ``llms.txt`` site map at the root and ``.well-known``
    locations; when one exists its content is returned as-is instead of the
    requested page.
    """

    def run(
        self,
        url: str,
        max_length: int | None = 5000,
        max_lines: int | None = 200,
        timeout: int | None = 10,
        follow_redirects: bool = True,
        threshold: int | None = BIG_CONTENT_THRESHOLD,
    ) -> dict[str, Any]:
        """
        Fetch content from a URL and return results.

        Before fetching, the tool attempts llms.txt discovery: it probes
        ``<origin>/llms.txt`` (root level) and, if that fails,
        ``<origin>/.well-known/llms.txt`` (well-known path) with lightweight
        HEAD requests. If one answers 200 OK, the file is fetched with a GET
        request and its content is returned as-is (no Markdown parsing, never
        truncated by max_length/max_lines). The discovery probes are silent;
        only a successful retrieval is reported. If no llms.txt exists, the
        tool falls back to fetching the requested URL normally.

        Args:
            url (str): The URL to fetch content from (must be http:// or https://)
            max_length (Optional[int]): Maximum number of characters to return (default: 5000)
            max_lines (Optional[int]): Maximum number of lines to return (default: 200)
            timeout (Optional[int]): Request timeout in seconds (default: 10)
            follow_redirects (bool): Whether to follow HTTP redirects (default: True)
            threshold (Optional[int]): Content size (in characters) above which the
                full content is written to a temporary file instead of being returned
                inline. Pass None to disable. llms.txt content is never stored to a
                file - it is always returned inline in full. (default: 10000)

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool indicating if request succeeded
                - 'content': fetched content as string (if successful and not too big)
                - 'message': message returned when content was too big and stored to a file
                - 'tmp_filename': path to the temporary file (only when content was too big)
                - 'too_big': bool (only present when content was stored to a temp file)
                - 'llms_txt': bool (only present when the content comes from an llms.txt file)
                - 'original_url': the originally requested URL (only when llms_txt is present)
                - 'url': the URL that was fetched
                - 'status_code': HTTP status code (if available)
                - 'content_length': length of content in bytes
                - 'lines_returned': number of lines in returned content
                - 'error': error message if request failed (only present if success=False)
        """
        try:
            # Validate URL
            if not url.startswith(("http://", "https://")):
                self.report_error("URL must start with http:// or https://")
                return {
                    "success": False,
                    "error": "URL must start with http:// or https://",
                    "url": url,
                }

            # Try llms.txt discovery first. The probes are silent; only a
            # successful retrieval is reported.
            llms_url = _discover_llms_txt(
                url, timeout=timeout, follow_redirects=follow_redirects
            )
            if llms_url:
                self.report_result(f"Retrieved llms.txt from {llms_url}")
                return self._fetch_content(
                    url=llms_url,
                    original_url=url,
                    timeout=timeout,
                    follow_redirects=follow_redirects,
                    max_length=max_length,
                    max_lines=max_lines,
                    threshold=threshold,
                    llms_txt=True,
                    report_fetch_result=False,
                )

            self.report_start(f"\U0001f310 Fetching URL: {url}")

            return self._fetch_content(
                url=url,
                original_url=url,
                timeout=timeout,
                follow_redirects=follow_redirects,
                max_length=max_length,
                max_lines=max_lines,
                threshold=threshold,
                llms_txt=False,
                report_fetch_result=True,
            )

        except urllib.error.URLError as e:
            self.report_error(f"URL Error: {e.reason}")
            return {
                "success": False,
                "error": f"URL Error: {e.reason}",
                "url": url,
            }
        except Exception as e:
            self.report_error(f"Execution error: {e!s}")
            return {
                "success": False,
                "error": f"Failed to fetch URL: {e!s}",
                "url": url,
            }

    def _fetch_content(
        self,
        url: str,
        original_url: str,
        *,
        timeout: int | None,
        follow_redirects: bool,
        max_length: int | None,
        max_lines: int | None,
        threshold: int | None,
        llms_txt: bool,
        report_fetch_result: bool,
    ) -> dict[str, Any]:
        """Perform the GET request and build the result dict."""
        start_time = time.time()

        # Build the request
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)

        try:
            response = _build_opener(follow_redirects).open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            self.report_error(f"HTTP Error {e.code}: {e.reason}")
            return {
                "success": False,
                "error": f"HTTP Error {e.code}: {e.reason}",
                "url": url,
                "status_code": e.code,
                "execution_time_ms": execution_time_ms,
            }

        # Read content
        content = response.read().decode("utf-8", errors="replace")
        status_code = response.getcode()
        content_length = len(content.encode("utf-8"))
        total_lines = content.count("\n") + (
            1 if content and not content.endswith("\n") else 0
        )

        execution_time_ms = int((time.time() - start_time) * 1000)

        base = {
            "url": url,
            "status_code": status_code,
            "content_length": content_length,
            "lines_returned": total_lines,
            "execution_time_ms": execution_time_ms,
        }
        if llms_txt:
            base["llms_txt"] = True
            base["original_url"] = original_url

        # If the full content is too big, store it in a temporary file
        # instead of returning it inline (which would blow up the model
        # context). llms.txt site maps are exempt: they are always returned
        # inline in full, never written to a temporary file.
        if not llms_txt and threshold is not None and len(content) > threshold:
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                prefix="janito_geturl_",
                encoding="utf-8",
                delete=False,
            )
            tmp.write(content)
            tmp.close()

            _track_temp_file(tmp.name)

            message = f"Content was too big, stored at {tmp.name}, use search methods to explore it."
            self.report_warning(message)

            return {
                "success": True,
                "message": message,
                "too_big": True,
                "tmp_filename": tmp.name,
                **base,
            }

        # Apply limits (never to llms.txt content - it is returned as-is).
        if not llms_txt:
            if max_length is not None and len(content) > max_length:
                content = content[:max_length] + "... [truncated]"

            if max_lines is not None:
                lines = content.split("\n")
                if len(lines) > max_lines:
                    content = "\n".join(lines[:max_lines]) + "\n... [truncated]"

        lines_returned = len(content.split("\n"))

        if report_fetch_result:
            self.report_result(
                f"Fetched {content_length} bytes ({lines_returned} lines)"
            )

        return {
            "success": True,
            **base,
            "content": content,
            "lines_returned": lines_returned,
        }


# CLI interface for testing
def main():
    """Command line interface for testing the GetUrl tool."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch content from URLs for AI function calling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "https://httpbin.org/get"
  %(prog)s "https://example.com" --max-length 1000 --max-lines 50
  %(prog)s "https://api.github.com/users/octocat" --json
        """,
    )

    parser.add_argument("url", help="URL to fetch (must be http:// or https://)")
    parser.add_argument(
        "--max-length",
        "-l",
        type=int,
        default=5000,
        help="Maximum characters to return (default: 5000)",
    )
    parser.add_argument(
        "--max-lines",
        "-n",
        type=int,
        default=200,
        help="Maximum lines to return (default: 200)",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=10,
        help="Request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=BIG_CONTENT_THRESHOLD,
        help=(
            "Content size (chars) above which content is stored to a temp file "
            f"instead of returned inline (default: {BIG_CONTENT_THRESHOLD}, "
            "pass -1 to disable)"
        ),
    )
    parser.add_argument(
        "--no-follow-redirects", action="store_true", help="Don't follow HTTP redirects"
    )
    parser.add_argument(
        "--json", "-j", action="store_true", help="Output in JSON format"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show verbose output"
    )

    args = parser.parse_args()

    # Create tool instance and execute
    tool_instance = GetUrl()
    result = tool_instance.run(
        url=args.url,
        max_length=args.max_length,
        max_lines=args.max_lines,
        timeout=args.timeout,
        follow_redirects=not args.no_follow_redirects,
        threshold=None
        if args.threshold is not None and args.threshold < 0
        else args.threshold,
    )

    # Output results
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            # Oversized content was stored to a temporary file.
            if result.get("too_big"):
                print("? Content too big - stored to a temporary file")
                print(f"  URL: {result['url']}")
                if result.get("llms_txt"):
                    print(
                        f"  Source: llms.txt (site map) for {result.get('original_url', 'N/A')}"
                    )
                print(f"  Status: {result.get('status_code', 'N/A')}")
                print(f"  Content length: {result.get('content_length', 'N/A')} bytes")
                print(f"  Lines: {result.get('lines_returned', 'N/A')}")
                print(f"  Temp file: {result.get('tmp_filename', 'N/A')}")
                print(
                    f"  Execution time: {format_duration_ms(result.get('execution_time_ms', 'N/A'))}"
                )
                print(f"\n  {result.get('message', '')}")
                return 0

            print("? URL fetch successful")
            if result.get("llms_txt"):
                print(
                    f"  Source: llms.txt (site map) for {result.get('original_url', 'N/A')}"
                )
            print(f"  URL: {result['url']}")
            print(f"  Status: {result.get('status_code', 'N/A')}")
            print(f"  Content length: {result.get('content_length', 'N/A')} bytes")
            print(f"  Lines returned: {result.get('lines_returned', 'N/A')}")
            print(
                f"  Execution time: {format_duration_ms(result.get('execution_time_ms', 'N/A'))}"
            )

            if args.verbose:
                print("\nContent:")
                print("-" * 40)
                print(result["content"])
            else:
                # Show truncated preview
                content_preview = result["content"][:200].replace("\n", " ")
                if len(result["content"]) > 200:
                    content_preview += "..."
                print(f"\nContent preview: {content_preview}")
        else:
            print("? URL fetch failed")
            print(f"  URL: {result['url']}")
            print(f"  Error: {result.get('error', 'Unknown error')}")
            if "status_code" in result:
                print(f"  Status code: {result['status_code']}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    exit(main())
