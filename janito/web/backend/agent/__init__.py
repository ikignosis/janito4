"""Headless streaming agentic loop for the web backend.

Drives the agentic while-loop as an async generator that yields structured
events instead of printing.  The per-API adapters (call-kwargs building,
stream accumulation, history conversion) live in the shared
``janito.llm_adapters`` layer; the runner modules here — one per API type
(``completions.py``, ``responses.py``, ``anthropic.py``, ``dashscope.py``,
``gemini.py``) — keep only the web-only async glue (SDK client creation +
event-stream drivers).  See :func:`stream_prompt` in :mod:`~.loop` for the
orchestration skeleton.
"""
