"""Shared per-API adapter layer for the CLI and web agent loops.

Both agent loops (the CLI's ``janito.llm_clients.base_client.Client.run_turn``
and the web's ``janito.web.backend.agent.loop.stream_prompt``) drive the
same turn pipeline — resolve config, stream a response, run tool calls and
repeat — but they differ where it matters: the CLI is synchronous and prints
Rich output, the web is asynchronous and yields structured events (its wire
format lives in ``janito.web.backend.events``).  The per-API *adapters*
(call-kwargs building, stream accumulation, history conversion, usage
normalization) are the same in both, so they live here:

- :mod:`~.completions` — Chat Completions call kwargs + ``CompletionsTurnAccumulator``.
- :mod:`~.responses`   — Responses API kwargs, history conversion and accumulator.
- :mod:`~.anthropic`   — native Anthropic SDK kwargs, conversion and accumulator.
- :mod:`~.dashscope`   — native DashScope SDK kwargs and accumulator.
- :mod:`~.gemini`      — native Gemini SDK kwargs, conversion and accumulator.
- :mod:`~.usage`       — token-usage normalization (``TokenStats``) shared by both loops.
- :mod:`~.observer`    — the ``TurnObserver`` protocol + headless ``NullObserver``
  (the UI-observability contract the turn pipeline drives).
- :mod:`~.sdk`         — raw SDK response-object introspection for the stream consumers.

The tool-execution core is shared too, and lives in its historical home
``janito.tooling.executor.run_tool`` (used by the CLI ``ToolExecutor`` and
wrapped in a thread by the web loop).
"""
