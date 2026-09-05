# Tools

janito includes built-in tools for common tasks.

## Available Tools

| Category | Tools | Description |
|----------|-------|-------------|
| **Files** | File operations | List, read, write, search files and directories |
| **Code Search** | Code search | Search a pre-built trigram index (codesearch plugin, `/codesearch`) |
| **System** | Execution | Run Python code, execute PowerShell commands |
| **Net** | Web access | Fetch URLs, search the web (Brave) |
| **Interactive** | AskUser | Ask the user a question mid-turn (web UI and interactive shell only — see below) |
| **Skills** | Extensions | Install and use task-specific skills |
| **MCP** | Extensions | Connect to MCP servers for custom tools |

## Tool namespaces and deferred loading

Every tool belongs to a namespace (its toolset: `files`, `system`, `net`,
`tasks`, ...). Models with hosted tool search enabled (Meta `muse-spark`,
`tool_search: true` in the provider config) receive tools grouped as
`namespace` entries with `defer_loading`, plus a single `tool_search`
entry, so the model loads schemas on demand (Responses API only). All other
models receive flat function tools. The CLI prints `Searching for tools on
<paths>` when a lookup starts and `Loaded (n) tools` when it finishes.

## Interactive Tools

`AskUser` lets the agent ask you a question mid-turn and use your answer.
It is only loaded in **interactive runs**, where someone can actually
answer:

- **Web UI** (`janito --web`): the question appears as an inline card in
  the chat, even when the server itself runs headless (no TTY stdin).
- **Interactive shell** (no prompt argument, TTY stdin): the question is
  rendered in the console and the answer is read from stdin.

In single-prompt runs — `janito "..."` (positional or piped) — nobody is
watching mid-run, so `AskUser` is **skipped**: it is never advertised to
the model and the agent proceeds with its best judgement instead of
stalling on a question nobody sees. The tool summary lists it as skipped
with the reason.

## Enabling Tools

Tools are automatically available in chat mode. For single prompts:

```bash
# File tools are always available
janito "Read the README.md file"
```

Tool integrations that are not built-in (Gmail, code search, OneDrive) are
provided by plugins — see [Plugins](../PLUGINS.md).

## Tool Progress

Tools report progress in real-time:

```
📖 Reading files...
📊 Processing: 50/100 files
✅ Completed: 100 files (2.3MB)
```

Progress messages go to stderr so they don't interfere with tool output.

## Related Topics

- [File Tools](files.md)
- [Code Search](codesearch.md) - Search a pre-built trigram index (codesearch plugin)
- [Web Tools](web-search.md) - Fetch URLs and search the web (Brave)
- [Skills](skills.md)
- [MCP Support](mcp.md)
