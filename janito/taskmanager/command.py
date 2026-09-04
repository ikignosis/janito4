"""Child ``janito`` command-line construction for the task manager package.

Maps the task's ``privileges`` string to the ``-r``/``-w``/``-x`` CLI flags
and builds the full child command line (see the package docstring).
"""

import sys

from ..config_dir import config_cli_args


def privilege_flags(privileges: str | None) -> list[str]:
    """Map a privileges string (``"rwx"``) to CLI flags (``["-r", "-w", "-x"]``).

    ``None`` or an empty string yields ``[]`` -- the child then starts with
    the janito default privileges (read-only, issue #85).

    Args:
        privileges: A combination of ``r`` / ``w`` / ``x`` (any order/case).

    Returns:
        The corresponding ``-r`` / ``-w`` / ``-x`` flags.

    Raises:
        ValueError: If ``privileges`` contains any character other than
            ``r`` / ``w`` / ``x``.
    """
    if not privileges:
        return []
    flags: list[str] = []
    for char in str(privileges).strip().lower():
        if char == "r":
            flags.append("-r")
        elif char == "w":
            flags.append("-w")
        elif char == "x":
            flags.append("-x")
        else:
            raise ValueError(
                f"Invalid privilege character {char!r} in {privileges!r}: "
                "expected a combination of 'r', 'w' and 'x'"
            )
    return flags


def build_task_command(privileges: str | None) -> list[str]:
    """Build the child ``janito`` command line (issue #94).

    Uses ``sys.executable -m janito`` so the child runs in the same Python
    environment as the parent, inherits the parent's ``-c``/``-l`` config
    flags via :func:`janito.config_dir.config_cli_args`, maps ``privileges``
    to the ``-r``/``-w``/``-x`` flags, and always appends ``--no-tasks`` so
    the child cannot spawn further tasks (preventing recursive task
    execution).

    Args:
        privileges: Privileges for the child (``None``/``""`` = read-only).

    Returns:
        The command line (as a list of argv strings).
    """
    cmd = [sys.executable, "-m", "janito"]
    cmd.extend(config_cli_args())
    cmd.extend(privilege_flags(privileges))
    cmd.append("--no-tasks")
    return cmd
