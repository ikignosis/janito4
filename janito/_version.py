"""
Runtime version resolution for janito.

The version shown by the CLI is derived at import time instead of being
frozen into a static string, so it always reflects the actual source state:

* When running from a git checkout (editable install, ``uv sync``), the
  version is computed from the latest git tag with ``git describe --tags
  --long``, mirroring the ``post-release`` scheme used by setuptools_scm:
  ``4.33.0`` exactly at a tag, ``4.33.0.post<N>+g<sha>`` when ``N`` commits
  have been made after the tag.  The tag is the source of truth, so there
  is no need to reinstall after cutting a new release.

* Otherwise (installed wheel/sdist) the version comes from the package
  distribution metadata via ``importlib.metadata``.

* As a last resort (no git repository and no distribution metadata) a
  static fallback constant is used.

The resolution assumes ``_version.py`` lives inside the janito package in a
checkout whose repository root holds the janito tags (the janito repo
itself, or a monorepo where janito is developed).
"""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
from functools import lru_cache
from pathlib import Path

# Last-resort fallback: only used when neither git nor distribution
# metadata can provide a version (should not happen in normal installs).
_FALLBACK_VERSION = "0.2.0"

# Repository root: the directory that contains the ``janito`` package.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# ``git describe --tags --long`` output, e.g. ``v4.33.0-1-g6412eb8``.
_DESCRIBE_RE = re.compile(r"^v?(?P<tag>\d+\.\d+\.\d+)(?:-(?P<dist>\d+)-g(?P<sha>[0-9a-f]+))?$")

# A ``post<N>`` segment inside a version, e.g. ``post1`` in ``4.33.0.post1``.
_POST_RE = re.compile(r"^post(?P<num>\d+)")


def _git_describe() -> str | None:
    """Return the ``git describe --tags --long`` output, or None."""
    if not (_REPO_ROOT / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "describe", "--tags", "--long"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _version_from_describe(describe: str) -> str | None:
    """Convert ``git describe`` output into a ``post-release`` style version.

    ``v4.33.0-0-g6412eb8`` (exactly at the tag) -> ``4.33.0``
    ``v4.33.0-1-g6412eb8`` (one commit after the tag) -> ``4.33.0.post1+g6412eb8``

    Returns None when the output is not a parseable tag (e.g. a bare commit
    hash because the repository has no tags yet).
    """
    match = _DESCRIBE_RE.match(describe)
    if match is None:
        return None
    tag = match.group("tag")
    distance = int(match.group("dist") or 0)
    sha = match.group("sha")
    if distance == 0:
        return tag
    return f"{tag}.post{distance}+g{sha}"


def _metadata_version() -> str | None:
    """Return the installed distribution version, or None."""
    try:
        return importlib.metadata.version("janito")
    except importlib.metadata.PackageNotFoundError:
        return None


@lru_cache(maxsize=1)
def _resolve_version() -> str:
    """Resolve the janito version once per process."""
    describe = _git_describe()
    if describe is not None:
        version = _version_from_describe(describe)
        if version is not None:
            return version
    metadata = _metadata_version()
    if metadata:
        return metadata
    return _FALLBACK_VERSION


def _version_tuple(version: str) -> tuple[int, ...]:
    """Turn a version string into a numeric tuple ``(major, minor, patch[, post])``."""
    parts: list[int] = []
    for part in version.split("."):
        post = _POST_RE.match(part)
        if post is not None:
            parts.append(int(post.group("num")))
        else:
            match = re.match(r"\d+", part)
            parts.append(int(match.group()) if match is not None else 0)
    return tuple(parts)


__version__ = _resolve_version()
__version_tuple__ = _version_tuple(__version__)
