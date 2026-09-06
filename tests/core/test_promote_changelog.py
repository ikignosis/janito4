"""
Tests for the changelog promotion script (scripts/promote_changelog.py).

Covers the EOF handling regression: promoting ``[Unreleased]`` when it is the
last section of CHANGELOG.md (the post-release-reset state) used to leave the
file with a dangling blank line and no trailing newline, which made
pre-commit's ``end-of-file-fixer`` rewrite the file and abort the commit.
"""

import importlib.util
from pathlib import Path

# scripts/ is not a package, so load the script directly from its path.
_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "promote_changelog.py"
_spec = importlib.util.spec_from_file_location("promote_changelog", _SCRIPT)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

_HEADER = """\
# Changelog

## [Unreleased](https://example.com/org/repo/compare/v1.2.3...HEAD)

### Added

- Some new feature.
"""

_RELEASED_TAIL = """
## [v1.2.3](https://example.com/org/repo/compare/v1.2.2...v1.2.3) - 2026-01-01

### Fixed

- Old fix.
"""


def _write_changelog(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_promote_when_unreleased_is_last_section(tmp_path):
    path = _write_changelog(tmp_path, _HEADER)
    pc.promote(path, version="v1.3.0", bump="minor", date="2026-02-02", dry_run=False)
    result = path.read_text(encoding="utf-8")
    assert result.endswith("- Some new feature.\n")
    assert not result.endswith("\n\n")
    assert "## [v1.3.0]" in result
    assert "## [Unreleased](https://example.com/org/repo/compare/v1.3.0...HEAD)" in result


def test_promote_with_following_sections(tmp_path):
    path = _write_changelog(tmp_path, _HEADER + _RELEASED_TAIL)
    pc.promote(path, version="v1.3.0", bump="minor", date="2026-02-02", dry_run=False)
    result = path.read_text(encoding="utf-8")
    assert result.endswith("- Old fix.\n")
    assert not result.endswith("\n\n")
    assert result.index("## [v1.3.0]") < result.index("## [v1.2.3]")


def test_promote_empty_unreleased_as_last_section(tmp_path):
    text = "# Changelog\n\n" "## [Unreleased](https://example.com/org/repo/compare/v1.2.3...HEAD)\n"
    path = _write_changelog(tmp_path, text)
    pc.promote(path, version="v1.3.0", bump="minor", date="2026-02-02", dry_run=False)
    result = path.read_text(encoding="utf-8")
    assert result.endswith("\n")
    assert not result.endswith("\n\n")
    assert "## [v1.3.0]" in result
