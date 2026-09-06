#!/usr/bin/env python3
"""
Read File Tool - A class-based tool for reading file contents.

This tool demonstrates how to use the base tool class with progress reporting.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito.tools.files.read_file [args]
For AI function calling, use through the tool registry (tooling.tools_registry).
"""

import json
import os
from typing import Any

from ...tooling import BaseTool, norm_path
from ...tooling.decorator import tool


@tool(permissions="r")
class ReadFile(BaseTool):
    """
    Tool for reading the contents of a file.

    Args:
        filepath (str): Path to the file to read
        start_line (int): Starting line number (1-based). Defaults to 1.
            A negative value counts back from the end of the file
            (-1 = last line, -5 = fifth-to-last) and reads to the end of
            the file, like ``tail -5``; ``max_lines`` is then ignored.
        max_lines (int, optional): Maximum number of lines to read from
            start_line. Defaults to None (read to end of file).
    """

    def run(
        self,
        filepath: str,
        start_line: int = 1,
        max_lines: int | None = None,
    ) -> dict[str, Any]:
        """
        Read the contents of a file.

        Args:
            filepath (str): The path to the file to read
            start_line (int): Starting line number (1-based). Defaults to 1.
                A negative value counts back from the end of the file
                (``-1`` is the last line, ``-5`` the fifth-to-last) and reads
                to the end of the file, like ``tail -5``; ``max_lines`` is
                ignored in that case.
            max_lines (int, optional): Maximum number of lines to read,
                starting from ``start_line``. If None, reads to the end of the
                file. Values beyond the end of the file are clamped to the
                last line, so the tool returns all the lines it could read.
                Ignored when ``start_line`` is negative.

        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool indicating if operation succeeded
                - 'content': file content as string (if successful)
                - 'filepath': the file that was read
                - 'start_line': first line actually returned (1-based)
                - 'max_lines': effective line limit (None means no limit)
                - 'total_lines': total number of lines in the file
                - 'lines_read': number of lines actually read
                - 'error': error message if operation failed (only present if success=False)
        """
        try:
            abs_filepath = os.path.abspath(filepath)
            norm_path_str = norm_path(abs_filepath)

            # A negative start_line means "read the last N lines" (tail-like
            # semantics): the slice runs to the end of the file and max_lines
            # is ignored. _resolve_slice still validates max_lines so that a
            # nonsensical value is reported instead of silently dropped.
            tail_mode = start_line < 0

            # Report start
            if tail_mode:
                range_info = f" (last {-start_line} lines)"
            elif max_lines is not None:
                range_info = f" (line {start_line}, max {max_lines} lines)"
            else:
                range_info = f" (line {start_line}, until EOF)"

            self.report_start(f"\U0001f4d6 Reading file {norm_path_str}{range_info}", end="")

            if not os.path.exists(abs_filepath):
                self.report_error(f"File does not exist: {norm_path_str}")
                return {
                    "success": False,
                    "error": f"File does not exist: {norm_path_str}",
                    "filepath": filepath,
                }

            if not os.path.isfile(abs_filepath):
                self.report_error(f"Path is not a file: {norm_path_str}")
                return {
                    "success": False,
                    "error": f"Path is not a file: {norm_path_str}",
                    "filepath": filepath,
                }

            # Get file size for progress indication
            file_size = os.path.getsize(abs_filepath)
            size_str = f"({file_size} bytes)"
            self.report_progress(f" {size_str}", end="")

            # Read the file and determine total lines
            with open(abs_filepath, encoding="utf-8") as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)

            try:
                actual_from, effective_max = self._resolve_slice(start_line, max_lines, total_lines)
            except ValueError as e:
                error_msg = str(e)
                self.report_error(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "filepath": filepath,
                    "total_lines": total_lines,
                }

            # A max_lines beyond the end of the file is not an error: clamp it
            # to the last available line so the caller gets all the lines the
            # tool could read instead of a failure.
            actual_to = min(actual_from + effective_max, total_lines) if effective_max is not None else total_lines

            # Extract the requested lines
            selected_lines = all_lines[actual_from:actual_to]
            content = "".join(selected_lines)
            lines_read = len(selected_lines)

            # Convert back to 1-based
            actual_from_line = actual_from + 1

            self.report_result(f"Read {lines_read} lines")

            return {
                "success": True,
                "content": content,
                "filepath": filepath,
                "start_line": actual_from_line,
                "max_lines": effective_max,
                "total_lines": total_lines,
                "lines_read": lines_read,
            }

        except Exception as e:
            self.report_error(f"Error reading file: {e!s}")
            return {
                "success": False,
                "error": str(e),
                "filepath": filepath,
                "start_line": start_line,
                "max_lines": max_lines,
            }

    @staticmethod
    def _resolve_slice(
        start_line: int,
        max_lines: int | None,
        total_lines: int,
    ) -> tuple[int, int | None]:
        """
        Resolve the slice to read as (0-based start line, line limit).

        Args:
            start_line: Requested start line. Positive values are 1-based from
                the beginning of the file; negative values count back from the
                end (-1 = last line), like Python subscripts or ``tail``.
            max_lines: Requested line limit (None = read to end of file).
                Ignored (but still validated) when ``start_line`` is negative.
            total_lines: Number of lines in the file.

        Returns:
            tuple[int, int | None]: (0-based start line, line limit). A line
            limit of None means "read to end of file".

        Raises:
            ValueError: if the arguments are invalid.
        """
        # Validate both arguments up front so the error reported never depends
        # on which one is checked first.
        if start_line == 0:
            raise ValueError(
                "start_line (0) is out of range. Use a positive line number "
                "or a negative value to count back from the end of the file."
            )

        if max_lines is not None and max_lines < 1:
            raise ValueError(f"max_lines ({max_lines}) is out of range. " "max_lines must be at least 1.")

        if start_line < 0:
            # Tail mode: -1 is the last line, -N is N lines from the end. The
            # read starts at that anchor and always runs to EOF, so the limit
            # is None regardless of max_lines (which was validated above and
            # is then dropped). An anchor deeper than the file is clamped to
            # the first line rather than failing, so the caller gets the whole
            # file.
            return max(total_lines + start_line, 0), None

        if start_line > total_lines:
            raise ValueError(f"start_line ({start_line}) is out of range. " f"File has {total_lines} lines.")

        return start_line - 1, max_lines


# CLI interface for testing
def main():
    """Command line interface for testing the ReadFileTool."""
    import argparse

    parser = argparse.ArgumentParser(description="Read file tool for AI function calling")
    parser.add_argument("filepath", help="File path to read")
    parser.add_argument(
        "--start-line",
        "-s",
        type=int,
        default=1,
        help=(
            "Starting line number (1-based, default: 1). Negative values read "
            "the last N lines (e.g. -5 = last 5 lines, like tail)."
        ),
    )
    parser.add_argument(
        "--max-lines",
        "-m",
        type=int,
        default=None,
        help=("Maximum number of lines to read (default: end of file). " "Ignored when --start-line is negative."),
    )
    parser.add_argument("--json", "-j", action="store_true", help="Output in JSON format")

    args = parser.parse_args()

    tool_instance = ReadFile()
    result = tool_instance.run(
        filepath=args.filepath,
        start_line=args.start_line,
        max_lines=args.max_lines,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            norm_path_str = norm_path(result["filepath"])
            start_line = result.get("start_line", 1)
            lines_read = result.get("lines_read", 0)
            end_line = start_line + lines_read - 1
            print(f"Content of '{norm_path_str}' (lines {start_line}-{end_line}):")
            print("-" * 40)
            print(result["content"])
        else:
            print(f"Error: {result['error']}")


if __name__ == "__main__":
    main()
