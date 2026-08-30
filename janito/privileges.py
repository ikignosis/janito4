"""Session privilege model (READ / WRITE / EXEC).

The :class:`Privileges` dataclass and the module-level ``running_privileges``
global back the ``-r`` / ``-w`` / ``-x`` CLI flags and the ``privileges``
config key (``--set privileges=rwx``, issue #89).

:func:`parse_privileges` / :func:`format_privileges` convert between the
``r``/``w``/``x`` string form (the ``privileges`` config value) and a
:class:`Privileges` instance.
"""

from dataclasses import dataclass

# Maps privilege characters to Privileges dataclass attribute names.
_CHAR_TO_ATTR = {
    "r": "READ",
    "w": "WRITE",
    "x": "EXEC",
}


@dataclass
class Privileges:
    READ: bool = False
    WRITE: bool = False
    EXEC: bool = False


running_privileges = None


def parse_privileges(value) -> Privileges:
    """Parse a ``privileges=...`` config value into a :class:`Privileges`.

    Accepts any combination of the ``r`` / ``w`` / ``x`` characters in any
    order and case (``rwx``, ``xwr``, ``RW``, ...); duplicates are ignored.
    Flag-semantics parity with the CLI: ``w`` alone means write-only, it does
    **not** imply read (matching ``janito -w``).

    Args:
        value: The raw config value (a string, or an iterable of characters).

    Returns:
        The parsed :class:`Privileges` instance.

    Raises:
        ValueError: If the value is empty or contains any character other
            than ``r`` / ``w`` / ``x``.
    """
    if isinstance(value, str):
        chars = value.strip().lower()
    else:
        chars = "".join(str(c).lower() for c in value).strip()
    if not chars:
        raise ValueError(
            "Invalid privileges value '': expected a combination of 'r', 'w' "
            "and 'x' (e.g. 'rw', 'rwx'); use --unset privileges to restore "
            "the read-only default."
        )
    for char in chars:
        if char not in _CHAR_TO_ATTR:
            raise ValueError(
                f"Invalid privileges value {value!r}: expected a combination "
                f"of 'r', 'w' and 'x' (e.g. 'rw', 'rwx')."
            )
    return Privileges(
        READ="r" in chars,
        WRITE="w" in chars,
        EXEC="x" in chars,
    )


def format_privileges(priv: Privileges) -> str:
    """Format a :class:`Privileges` instance as its canonical ``r``/``w``/``x``.

    The characters appear in the fixed ``r``, ``w``, ``x`` order regardless
    of the order they were parsed from, so ``--get privileges`` always
    returns a canonical value.

    Args:
        priv: The privileges to format.

    Returns:
        The canonical string (e.g. ``"rw"``, ``"rwx"``, or ``""`` when no
        privilege is granted).
    """
    chars = []
    if priv.READ:
        chars.append("r")
    if priv.WRITE:
        chars.append("w")
    if priv.EXEC:
        chars.append("x")
    return "".join(chars)
