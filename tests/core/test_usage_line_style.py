"""
Tests for the token-usage summary line's style (issue #105).

The end-of-turn ``=== Time | In | Out | Cached | Cost ===`` line is rendered
with the ``bright_white on dark_green`` style (issue #105 -- previously
``bright_white on magenta``).  Non-terminal consoles strip styles, so the
tests capture the printed Rich renderables and pin the style span instead of
matching ANSI escape sequences.
"""

import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console  # noqa: E402
from rich.text import Text  # noqa: E402

from janito.llm_adapters.usage import TokenStats  # noqa: E402
from janito.ui.usage import _display_usage, display_turn_usage  # noqa: E402

EXPECTED_STYLE = "bright_white on dark_green"


class _RecordingConsole(Console):
    """A Console that keeps the renderables passed to ``print``."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.printed = []

    def print(self, *args, **kwargs):
        self.printed.extend(args)
        super().print(*args, **kwargs)


def _usage_summary_line(console):
    """Return the ``=== ... ===`` Text renderable printed by a render call."""
    texts = [
        r for r in console.printed if isinstance(r, Text) and r.plain.startswith("=== ")
    ]
    assert len(texts) == 1, f"expected one usage summary line, got {len(texts)}"
    return texts[0]


def _render_display_usage():
    """Render the usage line through ``_display_usage`` and return its Text."""
    console = _RecordingConsole(file=StringIO(), force_terminal=False, width=120)
    usage = SimpleNamespace(
        prompt_tokens=60,
        completion_tokens=40,
        total_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=5),
    )
    _display_usage(
        usage,
        65536,
        8192,
        console,
        provider="deepseek",
        model="deepseek-v4-flash",
    )
    return _usage_summary_line(console)


def test_usage_line_style_is_dark_green():
    """The summary line carries the ``bright_white on dark_green`` style."""
    text = _render_display_usage()
    assert EXPECTED_STYLE in [span.style for span in text.spans]


def test_usage_line_style_spans_the_whole_line():
    """The style covers the whole ``=== ... ===`` line."""
    text = _render_display_usage()
    assert [(span.start, span.end) for span in text.spans] == [(0, len(text.plain))]


def test_usage_line_shape_kept():
    """The line keeps the historical ``=== ... | ... ===`` shape."""
    text = _render_display_usage()
    assert text.plain.startswith("=== ")
    assert "In: 60/65.5k" in text.plain
    assert "Out: 40/8.2k" in text.plain
    assert "Cached: 5" in text.plain
    assert "Cost:" in text.plain


def test_display_turn_usage_uses_the_same_style():
    """``display_turn_usage`` (the observer's render path) uses the style too."""
    console = _RecordingConsole(file=StringIO(), force_terminal=False, width=120)
    token_stats = TokenStats(
        total=100,
        last_input=60,
        last_output=40,
        last_cached=5,
        turn_input=60,
        turn_cached=5,
        turn_output=40,
    )

    class _Config:
        provider = "deepseek"
        model = "deepseek-v4-flash"
        max_input_tokens = 65536
        max_output_tokens = 8192

    display_turn_usage(token_stats, _Config(), console=console)
    text = _usage_summary_line(console)
    assert EXPECTED_STYLE in [span.style for span in text.spans]
