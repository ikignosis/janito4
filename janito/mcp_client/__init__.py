"""MCP client: transports for the Model Context Protocol.

Supports two transport types:

- ``stdio``: local process communication via stdin/stdout.
- ``http``: remote server communication via HTTP/SSE.

Use :func:`create_transport` to build a transport from a config dict
(see :mod:`janito.mcp_client.factory`).
"""

# Core exports
from .base import MCPTransport
from .factory import create_transport
from .http import HttpTransport

# Protocol exports for error handling and advanced usage
from .protocols import ConnectionError as MCPConnectionError
from .protocols import (
    MCPError,
    ProtocolVersionError,
    RequestTimeoutError,
    RPCError,
    build_notification,
    build_request,
    extract_result,
    parse_message,
    serialize_message,
)
from .stdio import StdioTransport

__all__ = [
    # Main classes
    "MCPTransport",
    "StdioTransport",
    "HttpTransport",
    "create_transport",
    # Protocol utilities
    "MCPError",
    "RPCError",
    "ProtocolVersionError",
    "MCPConnectionError",
    "RequestTimeoutError",
    "build_request",
    "build_notification",
    "parse_message",
    "serialize_message",
    "extract_result",
]
