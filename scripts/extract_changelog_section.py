#!/usr/bin/env python3
"""Extract a single release section from CHANGELOG.md for GitHub Releases.

Usage:
    python scripts/extract_changelog_section.py v4.19.0 --output dist/release-notes.md
    python scripts/extract_changelog_section.py v4.19.0  # prints to stdout

Matches headings like ``## [v4.19.0]``, ``## [4.19.0]``, ``## v4.19.0``.
Outputs the section body (without the ``##`` heading line) up to the next
``##`` heading, stripped of leading/trailing blank lines. Exits 1 if not found.
Only stdlib is used so it runs in the release workflow without dependencies.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def extract(text: str, version: str) -> str:
    v = version.strip().lstrip("vV")
    # e.g. ## [v1.2.3](url) - 2026-01-01  or  ## v1.2.3  or  ## 1.2.3
    heading_re = re.compile(
        r"^##\s+\[?[vV]?" + re.escape(v) + r"\b.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = heading_re.search(text)
    if not match:
        raise ValueError(f"No '## [{version}]' section found in CHANGELOG.md")
    next_heading = re.search(r"^##\s+", text[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(text)
    body = text[match.end() : end].strip("\n")
    # Drop link-definition leftovers? No — keep body as-is, stripped.
    return body.strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="release version, e.g. v4.19.0")
    parser.add_argument("--changelog", default=str(DEFAULT_CHANGELOG))
    parser.add_argument("--output", default=None, help="write to file instead of stdout")
    args = parser.parse_args(argv)

    path = Path(args.changelog)
    if not path.exists():
        print(f"✗ {path} does not exist.", file=sys.stderr)
        return 1
    try:
        body = extract(path.read_text(encoding="utf-8"), args.version)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"✓ Wrote {args.version} release notes to {out}.", file=sys.stderr)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
