"""
Tests for the runtime version resolution in ``janito._version``.

The CLI version is resolved at import time: from the latest git tag when
running from a checkout (editable install), from distribution metadata
otherwise.  These tests cover the pure parsing helpers and the shape of
the resolved ``__version__``.
"""

import sys
from pathlib import Path

# Add the repo root to sys.path to allow importing the package directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from janito._version import _version_from_describe, _version_tuple

if pytest is not None:

    def test_describe_exactly_at_tag():
        assert _version_from_describe("v4.33.0-0-g6412eb8") == "4.33.0"
        assert _version_from_describe("v4.33.0") == "4.33.0"

    def test_describe_after_tag():
        assert _version_from_describe("v4.33.0-1-g6412eb8") == "4.33.0.post1+g6412eb8"
        assert _version_from_describe("v4.33.0-42-gabc1234") == "4.33.0.post42+gabc1234"

    def test_describe_unparseable():
        assert _version_from_describe("6412eb8") is None
        assert _version_from_describe("not-a-version") is None
        assert _version_from_describe("v4.33.0rc1-2-gabc1234") is None

    def test_version_tuple():
        assert _version_tuple("4.33.0") == (4, 33, 0)
        assert _version_tuple("4.33.0.post1+g6412eb8") == (4, 33, 0, 1)
        assert _version_tuple("0.2.0") == (0, 2, 0)

    def test_resolved_version_shape():
        import re

        import janito

        assert re.match(
            r"^\d+\.\d+\.\d+(\.post\d+(\+g[0-9a-f]+(\.d\d{8})?)?)?$",
            janito.__version__,
        )
        assert janito.__version_tuple__ == _version_tuple(janito.__version__)

else:  # pragma: no cover - fallback runner without pytest

    def _main():
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                except TypeError:
                    # Skip tests that require monkeypatch/capsys fixtures.
                    continue
                print(f"OK {name}")

    if __name__ == "__main__":
        _main()
