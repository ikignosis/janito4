#!/usr/bin/env python3
"""
Find Files Tool - A class-based tool for finding files by name pattern and attributes.

Unlike ListFiles (which lists directory contents), FindFiles searches for files
matching criteria such as path glob patterns, file type, size, and modification
time. It is the equivalent of the Unix `find` command.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito.tools.files.find_files [args]
For AI function calling, use through the tool registry (tooling.tools_registry).

The pure filter helpers live in :mod:`janito.tools.files.find_files_utils`.
"""

import os
from typing import Any

from ...tooling import BaseTool, norm_path
from ...tooling.decorator import tool
from .find_files_utils import (
    _parse_size,
    report_result,
    report_search_start,
    time_thresholds,
)
from .find_files_walk import _FindFilesWalker


@tool(permissions="r")
class FindFiles(_FindFilesWalker, BaseTool):
    """
    Tool for finding files and directories by name pattern and file attributes
    such as type, size, and modification time. Unlike ListFiles, patterns are
    matched against the full relative path.

    Args:
        paths (str): Space-separated root paths to search.
        pattern (str, optional): Glob pattern matched against the full relative path
            (e.g. "*/tests/test_*.py", "docs/**/*.md", "*.py").
        exclude (str, optional): Space-separated glob patterns to exclude
            (e.g. "*/node_modules/* */__pycache__/*").
        file_type (str, optional): Filter by type: "file", "dir", or "symlink".
            Default is "file".
        min_size (int, optional): Minimum file size in bytes.
        max_size (int, optional): Maximum file size in bytes.
        modified_within_days (float, optional): Only include entries modified
            within the last N days.
        older_than_days (float, optional): Only include entries modified more
            than N days ago.
        max_depth (int, optional): Maximum recursion depth (None = unlimited).
        max_results (int, optional): Maximum number of results to return.
            Default is 200.
        sort_by (str, optional): Sort results by "name", "size", or "mtime".
            Default is "name".
        respect_gitignore (bool): Whether to respect .gitignore patterns.
            .janitoignore patterns are always respected. Default is True.
    """

    def run(
        self,
        paths: str,
        pattern: str | None = None,
        exclude: str | None = None,
        file_type: str | None = "file",
        min_size: int | None = None,
        max_size: int | None = None,
        modified_within_days: float | None = None,
        older_than_days: float | None = None,
        max_depth: int | None = None,
        max_results: int | None = 200,
        sort_by: str | None = None,
        respect_gitignore: bool = True,
    ) -> dict[str, Any]:
        """
        Find files and directories matching the given criteria.

        Args:
            paths (str): Space-separated root paths to search.
            pattern (str, optional): Glob pattern matched against the full relative
                path (e.g. "*/tests/test_*.py", "docs/**/*.md", "*.py").
            exclude (str, optional): Space-separated glob patterns to exclude
                (e.g. "*/node_modules/* */__pycache__/*").
            file_type (str, optional): Filter by type: "file", "dir", or "symlink".
                Default is "file".
            min_size (int, optional): Minimum file size in bytes.
            max_size (int, optional): Maximum file size in bytes.
            modified_within_days (float, optional): Only include entries modified
                within the last N days.
            older_than_days (float, optional): Only include entries modified more
                than N days ago.
            max_depth (int, optional): Maximum recursion depth (None = unlimited).
            max_results (int, optional): Maximum number of results to return.
                Default is 200.
            sort_by (str, optional): Sort results by "name", "size", or "mtime".
                Default is "name".
            respect_gitignore (bool): Whether to respect .gitignore patterns.
                .janitoignore patterns are always respected. Default is True.

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool indicating if operation succeeded
                - 'files': list of matching relative paths
                - 'total_found': total number of matches returned
                - 'truncated': bool, True if max_results cut off results
                - 'paths': the root paths that were searched
                - 'pattern': the pattern used (if any)
                - 'stats': dict with 'entries_scanned' and 'gitignore_ignored'
                - 'error': error message if operation failed (only if success=False)
        """
        error = self._validate(file_type, sort_by, paths)
        if error:
            return error

        valid_paths, error = self._collect_valid_paths(paths)
        if error:
            return error

        exclude_patterns = exclude.strip().split() if exclude else []
        min_bytes = _parse_size(min_size)
        max_bytes = _parse_size(max_size)
        newer_than, older_than = time_thresholds(modified_within_days, older_than_days)

        report_search_start(
            self,
            valid_paths,
            pattern,
            exclude_patterns,
            file_type,
            min_bytes,
            max_bytes,
            modified_within_days,
            older_than_days,
        )

        try:
            results, stats = self._collect_results(
                valid_paths,
                pattern,
                exclude_patterns,
                file_type,
                min_bytes,
                max_bytes,
                newer_than,
                older_than,
                max_depth,
                max_results,
                respect_gitignore,
            )
        except (OSError, ValueError, RuntimeError) as e:
            self.report_error(f"Error during file search: {e!s}")
            return {
                "success": False,
                "error": str(e),
                "paths": paths,
                "pattern": pattern,
            }

        files, truncated = self._finalize_results(results, max_results, sort_by)
        report_result(self, files, stats, truncated)

        return {
            "success": True,
            "files": files,
            "total_found": len(files),
            "truncated": truncated,
            "paths": paths,
            "pattern": pattern,
            "stats": stats,
        }

    # ------------------------------------------------------------------

    def _validate(
        self,
        file_type: str | None,
        sort_by: str | None,
        paths: str,
    ) -> dict[str, Any] | None:
        """Validate file_type/sort_by; return an error result or None."""
        valid_types = {"file", "dir", "symlink"}
        if file_type is not None and file_type not in valid_types:
            msg = (
                f"Invalid file_type '{file_type}'. Must be one of: "
                f"{', '.join(sorted(valid_types))}"
            )
            self.report_error(msg)
            return {"success": False, "error": msg, "paths": paths}
        valid_sorts = {"name", "size", "mtime"}
        if sort_by is not None and sort_by not in valid_sorts:
            msg = (
                f"Invalid sort_by '{sort_by}'. Must be one of: "
                f"{', '.join(sorted(valid_sorts))}"
            )
            self.report_error(msg)
            return {"success": False, "error": msg, "paths": paths}
        return None

    def _collect_valid_paths(
        self, paths: str
    ) -> tuple[list[str], dict[str, Any] | None]:
        """Resolve the root paths; return (valid_paths, error_result)."""
        path_list = paths.strip().split()
        if not path_list:
            self.report_error("No paths provided")
            return (
                [],
                {"success": False, "error": "No paths provided", "paths": paths},
            )
        valid_paths: list[str] = []
        for p in path_list:
            abs_p = os.path.abspath(p)
            if not os.path.exists(abs_p):
                self.report_warning(f"Path does not exist: {norm_path(abs_p)}")
                continue
            valid_paths.append(abs_p)
        if not valid_paths:
            self.report_error("No valid paths to search")
            return (
                [],
                {
                    "success": False,
                    "error": "No valid paths to search",
                    "paths": paths,
                },
            )
        return valid_paths, None

    def _finalize_results(
        self,
        results: list[tuple[str, int, float]],
        max_results: int | None,
        sort_by: str | None,
    ) -> tuple[list[str], bool]:
        """Truncate and sort the results; return (files, truncated)."""
        truncated = False
        if max_results is not None and len(results) > max_results:
            results = results[:max_results]
            truncated = True
        if sort_by == "size":
            results.sort(key=lambda r: r[1])
        elif sort_by == "mtime":
            results.sort(key=lambda r: r[2])
        else:
            results.sort(key=lambda r: r[0])
        files = [r[0] for r in results]
        return files, truncated


def main():
    """Command line interface for testing the FindFiles tool."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Find files by name pattern and attributes"
    )
    parser.add_argument("paths", help="Space-separated root paths to search")
    parser.add_argument(
        "--pattern", "-p", help="Glob pattern for the full relative path"
    )
    parser.add_argument(
        "--exclude", "-e", help="Space-separated glob patterns to exclude"
    )
    parser.add_argument(
        "--type",
        "-t",
        dest="file_type",
        choices=["file", "dir", "symlink"],
        default="file",
        help="Filter by entry type (default: file)",
    )
    parser.add_argument("--min-size", type=int, help="Minimum file size in bytes")
    parser.add_argument("--max-size", type=int, help="Maximum file size in bytes")
    parser.add_argument(
        "--modified-within-days",
        type=float,
        help="Modified within the last N days",
    )
    parser.add_argument(
        "--older-than-days",
        type=float,
        help="Modified more than N days ago",
    )
    parser.add_argument("--max-depth", "-d", type=int, help="Maximum recursion depth")
    parser.add_argument(
        "--max-results", "-m", type=int, default=200, help="Maximum results"
    )
    parser.add_argument(
        "--sort-by",
        "-s",
        choices=["name", "size", "mtime"],
        help="Sort order for results",
    )
    parser.add_argument(
        "--no-gitignore", action="store_true", help="Disable .gitignore filtering"
    )
    parser.add_argument(
        "--json", "-j", action="store_true", help="Output in JSON format"
    )

    args = parser.parse_args()

    result = FindFiles().run(
        paths=args.paths,
        pattern=args.pattern,
        exclude=args.exclude,
        file_type=args.file_type,
        min_size=args.min_size,
        max_size=args.max_size,
        modified_within_days=args.modified_within_days,
        older_than_days=args.older_than_days,
        max_depth=args.max_depth,
        max_results=args.max_results,
        sort_by=args.sort_by,
        respect_gitignore=not args.no_gitignore,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            print(f"Found {result['total_found']} matches:")
            if result.get("truncated"):
                print("  (results truncated)")
            stats = result.get("stats", {})
            ignore_msgs = []
            if stats.get("gitignore_ignored", 0) > 0:
                ignore_msgs.append(
                    f"{stats['gitignore_ignored']} ignored by .gitignore"
                )
            if stats.get("janitoignore_ignored", 0) > 0:
                ignore_msgs.append(
                    f"{stats['janitoignore_ignored']} ignored by .janitoignore"
                )
            if ignore_msgs:
                print(f"  ({', '.join(ignore_msgs)})")
            print("-" * 40)
            for f in result["files"]:
                print(f"  {f}")
        else:
            print(f"Error: {result['error']}")


if __name__ == "__main__":
    main()
