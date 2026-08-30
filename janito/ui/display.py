"""Verbose API banners/panels and the reasoning / content renderers.

The verbose ``-v`` output of the turn loop is rendered here: the model /
backend banner, the API-call and API-response panels (with recursive
truncation so long conversations stay compact) and the reasoning / content
fragments.  These are pure presentation -- the data (``call_kwargs``,
``raw_attrs``, ...) is assembled by the API clients.
"""

import json
import logging
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from janito.llm_adapters.usage import normalize_usage

logger = logging.getLogger(__name__)


def _print_verbose_info(
    console: Console,
    base_url: str | None,
    model: str,
    mcp_manager,
    backend_default: str,
) -> None:
    """Print model/backend/MCP info in verbose mode.

    Args:
        console: The Rich console to print to.
        base_url: The resolved API base URL (``None`` for the standard
            OpenAI endpoint).
        model: The model name being used.
        mcp_manager: The MCP manager (may be ``None``).
        backend_default: The fallback backend label when ``base_url`` is
            ``None`` (e.g. ``"api.openai.com"``, ``"https://api.anthropic.com"``).
    """
    backend = base_url if base_url else backend_default
    text = Text(f"----- Model: {model} | Backend: {backend}")
    text.stylize("white on blue")
    console.print(text, highlight=False)

    # Show MCP status in verbose mode
    if mcp_manager and mcp_manager.connected_services:
        services_text = Text(
            f"----- MCP Services: {', '.join(mcp_manager.connected_services)}"
        )
        services_text.stylize("white on green")
        console.print(services_text, highlight=False)


def _truncate_text(value: Any, limit: int = 400) -> str:
    """Truncate long text for verbose output, marking the cut."""
    if value is None:
        return ""
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]} ... ({len(text) - limit} more chars)"


def _truncate_list(values: list, limit: int = 300) -> list:
    """Truncate each element of a list for verbose output."""
    out: list = []
    for value in values:
        if isinstance(value, dict):
            out.append(_truncate_item(value, limit))
        elif isinstance(value, list):
            out.append(_truncate_list(value, limit))
        elif isinstance(value, str):
            out.append(_truncate_text(value, limit))
        else:
            out.append(value)
    return out


def _truncate_item(item: dict, limit: int = 300) -> dict:
    """Recursively truncate a message/input item for verbose output."""
    out: dict = {}
    for key, value in item.items():
        if isinstance(value, str):
            out[key] = _truncate_text(value, limit)
        elif isinstance(value, list):
            out[key] = _truncate_list(value, limit)
        elif isinstance(value, dict):
            out[key] = _truncate_item(value, limit)
        else:
            out[key] = value
    return out


def _print_verbose_api_call(
    console: Console,
    call_kwargs: dict[str, Any],
    tools_schemas: list[dict[str, Any]] | None = None,
    *,
    tail: int = 3,
) -> None:
    """Print the API request parameters in verbose mode.

    The full ``messages`` / ``input`` conversation is too long to dump, so
    only the last ``tail`` entries are shown (with the total count noted) and
    every string inside them is truncated.  Tool schemas are summarized by
    name instead of printed in full; model capabilities carried under the
    reserved ``_builtin_tools`` key are summarized by ``type``.
    """
    display: dict[str, Any] = {}
    for key, value in call_kwargs.items():
        if key == "_builtin_tools":
            # Model capabilities (e.g. code_interpreter / web_search) are
            # merged into the tools array by the stream runner; summarize
            # them by type alongside the function-tool schemas.
            display[key] = [t.get("type") for t in (value or [])]
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            display[key] = {
                "_summary": (
                    f"{len(value)} items (showing last {tail})"
                    if len(value) > tail
                    else f"{len(value)} items"
                ),
                "tail": _truncate_list(value[-tail:]),
            }
        elif key in ("messages", "input") and isinstance(value, str):
            display[key] = _truncate_text(value, 500)
        else:
            display[key] = value

    if tools_schemas:
        names = [
            t.get("name") or (t.get("function") or {}).get("name") or t.get("type")
            for t in tools_schemas
        ]
        display["tools"] = {"_summary": f"{len(names)} tools", "names": names}

    console.print(
        Panel(
            json.dumps(display, indent=2, default=str),
            title=f"[bold blue]API Call: {call_kwargs.get('model', '?')}[/bold blue]",
            border_style="blue",
        ),
        highlight=False,
    )


def _raw_attrs_lines(raw_attrs: dict[str, Any] | None) -> list[str]:
    """Format raw response attributes as one ``Raw <key>:`` line each."""
    if not raw_attrs:
        return []
    return [
        f"Raw {key}: {_truncate_text(value, 120)}"
        for key, value in sorted(raw_attrs.items())
    ]


def _print_verbose_api_response(
    console: Console,
    full_content: str,
    reasoning_content: str | None,
    tool_calls: list[dict[str, Any]] | None,
    usage_info: Any,
    response_id: str | None = None,
    raw_attrs: dict[str, Any] | None = None,
) -> None:
    """Print a compact summary of the API response in verbose mode.

    The full assistant text is already rendered by the normal display path;
    this panel shows a tail of the content plus the raw top-level response
    attributes, the reasoning tail, tool-call names, normalized token usage
    and the server-side response id (when the API reports one).
    """
    lines = [f"Content: {_truncate_text(full_content, 500) or '(empty)'}"]
    lines.extend(_raw_attrs_lines(raw_attrs))
    if reasoning_content:
        lines.append(f"Reasoning: {_truncate_text(reasoning_content, 300)}")
    if tool_calls:
        calls = ", ".join(
            f"{t.get('name', '?')}({_truncate_text(t.get('arguments', ''), 80)})"
            for t in tool_calls
        )
        lines.append(f"Tool calls: {calls}")
    if usage_info:
        stats = normalize_usage(usage_info)
        if stats:
            parts = []
            for label, key in (("Total", "total"), ("In", "input"), ("Out", "output")):
                if stats.get(key) is not None:
                    parts.append(f"{label}: {stats[key]}")
            if stats.get("cached") is not None:
                parts.append(f"Cached: {stats['cached']}")
            lines.append("Usage: " + " | ".join(parts))
    if response_id:
        lines.append(f"Response id: {response_id}")
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold yellow]API Response[/bold yellow]",
            border_style="yellow",
        ),
        highlight=False,
    )


def _display_reasoning(reasoning_content: str, console: Console) -> None:
    """Show the reasoning panel when the model produced reasoning text."""
    if reasoning_content:
        console.print(
            Panel(
                Markdown(reasoning_content),
                title="[bold cyan]\U0001f4ad Reasoning[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        logger.debug("Reasoning content displayed")


def _display_content(full_content: str, console: Console) -> None:
    """Display the assembled response using rich markdown."""
    if full_content:
        console.print(Markdown(full_content))
