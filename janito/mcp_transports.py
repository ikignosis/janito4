"""MCP transport type registry: config building + display.

The ``/mcp`` shell command and the ``--list-mcp`` listing both need to know
what each transport type (``stdio`` / ``http``) requires to be *configured*
and how to *display* a configured service.  Previously each re-implemented
the same ``if transport == "stdio" ... elif transport == "http"`` switch
(``shell/cmds/mcp.py`` and ``cli/handlers/tools.py``); this module is the
single source for that knowledge.

Creation of the live transport instance stays in
:func:`janito.mcp_client.factory.create_transport` (where the transport
classes live); this module only knows the config *shape* and the CLI-facing
strings.  It lives at the root level so the shell / CLI layers can use it
without crossing into ``mcp_client`` -- the allowed-edge matrix in
``tests/test_import_graph.py`` keeps ``mcp_client`` self-contained and only
permits ``root -> mcp_client`` imports, not ``shell``/``cli`` -> ``mcp_client``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TransportSpec:
    """Config building + display for one MCP transport type.

    Attributes:
        usage_line: The ``/mcp add`` usage line for this transport.
        build_config: Build the service config dict from the raw ``/mcp add``
            argument list.  ``(args, warnings) -> config dict``; appends
            human-readable warnings (e.g. ignored ``--header`` values) to
            ``warnings`` and raises ``ValueError`` with a user-facing message
            when a required argument is missing.
        describe: Render a configured service's ``config`` as the detail
            string shown by ``/mcp list`` and ``--list-mcp``.
        confirm_lines: The lines printed after a successful ``/mcp add``
            (kept byte-identical to the historic per-transport output).
    """

    usage_line: str
    build_config: Callable[[list[str], list[str]], dict[str, Any]]
    describe: Callable[[dict[str, Any]], str]
    confirm_lines: Callable[[dict[str, Any]], list[str]]


def _build_stdio_config(args: list[str], warnings: list[str]) -> dict[str, Any]:
    """Build a stdio service config from the ``/mcp add`` argument list."""
    if not args:
        raise ValueError("stdio transport requires a command")
    # Build command string from args, quoting args that contain shell
    # metacharacters so shlex.split (used by create_transport) round-trips.
    command_parts = []
    for arg in args:
        if " " in arg or '"' in arg or "'" in arg:
            command_parts.append(f'"{arg}"')
        else:
            command_parts.append(arg)
    command = " ".join(command_parts)
    return {"transport": "stdio", "command": command, "env": {}}


def _build_http_config(args: list[str], warnings: list[str]) -> dict[str, Any]:
    """Build an HTTP service config from the ``/mcp add`` argument list."""
    if not args:
        raise ValueError("http transport requires a URL")
    url = args[0]
    headers = {}
    # Parse --header flags
    i = 1
    while i < len(args):
        if args[i] == "--header" and i + 1 < len(args):
            header_value = args[i + 1]
            if ":" in header_value:
                key, value = header_value.split(":", 1)
                headers[key.strip()] = value.strip()
            else:
                warnings.append(
                    f"Warning: Ignoring invalid header format: {header_value}"
                )
                warnings.append("  Expected format: --header KEY:VALUE")
            i += 2
        else:
            warnings.append(f"Warning: Ignoring unexpected argument: {args[i]}")
            i += 1
    service_config = {"transport": "http", "url": url}
    if headers:
        service_config["headers"] = headers
    return service_config


def _describe_stdio(config: dict[str, Any]) -> str:
    return f"Command: {config.get('command', '')}"


def _describe_http(config: dict[str, Any]) -> str:
    details = f"URL: {config.get('url', '')}"
    headers = config.get("headers", {})
    if headers:
        details += f"; {len(headers)} header(s)"
    return details


TRANSPORT_SPECS: dict[str, TransportSpec] = {
    "stdio": TransportSpec(
        usage_line="Usage: /mcp add <name> stdio <command> [args...]",
        build_config=_build_stdio_config,
        describe=_describe_stdio,
        confirm_lines=lambda config: [
            "  Transport: stdio",
            f"  Command:   {config.get('command', '')}",
        ],
    ),
    "http": TransportSpec(
        usage_line="Usage: /mcp add <name> http <url> [--header KEY:VALUE]",
        build_config=_build_http_config,
        describe=_describe_http,
        confirm_lines=lambda config: [
            "  Transport: http",
            f"  URL:       {config.get('url', '')}",
            *(
                [f"  Headers:   {len(config.get('headers', {}))} header(s) set"]
                if config.get("headers")
                else []
            ),
        ],
    ),
}


def get_transport_spec(transport: str) -> TransportSpec:
    """Return the spec for a transport type name (case-insensitive).

    Args:
        transport: The transport type name (``stdio`` / ``http``).

    Returns:
        The :class:`TransportSpec` for that transport.

    Raises:
        ValueError: If the transport type is unknown.
    """
    spec = TRANSPORT_SPECS.get((transport or "").lower())
    if spec is None:
        supported = ", ".join(f"'{name}'" for name in TRANSPORT_SPECS)
        raise ValueError(
            f"Unknown transport type: '{transport}'. Supported types: {supported}"
        )
    return spec


__all__ = [
    "TRANSPORT_SPECS",
    "TransportSpec",
    "get_transport_spec",
]
