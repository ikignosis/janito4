"""
Tests for the ReadFile tool's line-range handling.

The tool reads a 1-based ``start_line`` and up to ``max_lines`` lines. A
``max_lines`` value that exceeds the number of lines in the file must not
raise an error: the tool clamps it to the last available line and returns
all the lines it could read.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from janito.tools.files.read_file import ReadFile


@pytest.fixture
def sample_file(tmp_path):
    """Create a 5-line sample file and return its path."""
    path = tmp_path / "sample.txt"
    path.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")
    return str(path)


def test_max_lines_beyond_eof_is_clamped(sample_file):
    """A max_lines past the end of the file returns all readable lines, no error."""
    result = ReadFile().run(filepath=sample_file, start_line=1, max_lines=100)

    assert result["success"] is True
    assert result["total_lines"] == 5
    assert result["start_line"] == 1
    assert result["max_lines"] == 100
    assert result["lines_read"] == 5
    assert result["content"] == "line 1\nline 2\nline 3\nline 4\nline 5\n"


def test_max_lines_beyond_eof_with_offset_start(sample_file):
    """Clamping also works when start_line is not 1."""
    result = ReadFile().run(filepath=sample_file, start_line=4, max_lines=100)

    assert result["success"] is True
    assert result["start_line"] == 4
    assert result["lines_read"] == 2
    assert result["content"] == "line 4\nline 5\n"


def test_max_lines_within_range(sample_file):
    """A max_lines inside the range is honoured as-is."""
    result = ReadFile().run(filepath=sample_file, start_line=2, max_lines=2)

    assert result["success"] is True
    assert result["start_line"] == 2
    assert result["lines_read"] == 2
    assert result["content"] == "line 2\nline 3\n"


def test_no_max_lines_reads_to_eof(sample_file):
    """Without max_lines the file is read from start_line to the end."""
    result = ReadFile().run(filepath=sample_file, start_line=2)

    assert result["success"] is True
    assert result["start_line"] == 2
    assert result["max_lines"] is None
    assert result["lines_read"] == 4
    assert result["content"] == "line 2\nline 3\nline 4\nline 5\n"


def test_max_lines_less_than_one_still_errors(sample_file):
    """A max_lines below 1 remains invalid."""
    result = ReadFile().run(filepath=sample_file, start_line=1, max_lines=0)

    assert result["success"] is False
    assert result["error"].strip() != ""


def test_start_line_out_of_range_still_errors(sample_file):
    """An out-of-range start_line is still an error."""
    result = ReadFile().run(filepath=sample_file, start_line=99, max_lines=100)

    assert result["success"] is False
    assert "error" in result["error"].lower() or "out of range" in result["error"].lower()
    assert result["total_lines"] == 5


# --- Negative start_line (tail mode) ---------------------------------------


def test_negative_start_line_reads_last_lines(sample_file):
    """start_line=-5 reads the last 5 lines of the file."""
    result = ReadFile().run(filepath=sample_file, start_line=-5)

    assert result["success"] is True
    assert result["content"] == "line 1\nline 2\nline 3\nline 4\nline 5\n"
    assert result["start_line"] == 1
    assert result["lines_read"] == 5
    assert result["max_lines"] is None


def test_negative_start_line_last_line_only(sample_file):
    """start_line=-1 returns just the last line."""
    result = ReadFile().run(filepath=sample_file, start_line=-1)

    assert result["success"] is True
    assert result["content"] == "line 5\n"
    assert result["start_line"] == 5
    assert result["lines_read"] == 1
    assert result["total_lines"] == 5


def test_negative_start_line_reads_until_eof(sample_file):
    """The tail slice always extends to the end of the file."""
    result = ReadFile().run(filepath=sample_file, start_line=-3)

    assert result["success"] is True
    assert result["content"] == "line 3\nline 4\nline 5\n"
    assert result["start_line"] == 3
    assert result["lines_read"] == 3


def test_negative_start_line_ignores_max_lines(sample_file):
    """max_lines is ignored in tail mode; the read runs to EOF."""
    result = ReadFile().run(filepath=sample_file, start_line=-4, max_lines=2)

    assert result["success"] is True
    assert result["content"] == "line 2\nline 3\nline 4\nline 5\n"
    assert result["lines_read"] == 4
    # The echoed effective limit reflects the ignored (None) max_lines.
    assert result["max_lines"] is None


def test_negative_start_line_deeper_than_file_is_clamped(sample_file):
    """A tail longer than the file returns the whole file, no error."""
    result = ReadFile().run(filepath=sample_file, start_line=-100)

    assert result["success"] is True
    assert result["content"] == "line 1\nline 2\nline 3\nline 4\nline 5\n"
    assert result["start_line"] == 1
    assert result["lines_read"] == 5


def test_negative_start_line_max_lines_below_one_still_errors(sample_file):
    """An invalid max_lines is still rejected even in tail mode."""
    result = ReadFile().run(filepath=sample_file, start_line=-2, max_lines=0)

    assert result["success"] is False
    assert result["error"].strip() != ""


def test_start_line_zero_is_an_explicit_error(sample_file):
    """start_line=0 is invalid and explains the positive/negative convention."""
    result = ReadFile().run(filepath=sample_file, start_line=0)

    assert result["success"] is False
    assert result["error"].strip() != ""
