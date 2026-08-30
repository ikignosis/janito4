"""Headless streaming agentic loop for the web backend.

Lifts the agentic while-loop from ``janito.llm_clients.openai.completions_api``
into an async generator that yields structured events instead of printing.
The per-API adapters (call-kwargs building, stream accumulation, history
conversion) live in the shared ``janito.llm_adapters`` layer; the runner modules here
keep only the web-only async glue (SDK client creation + event-stream
drivers).  See :func:`stream_prompt` in :mod:`~.loop` for the orchestration
skeleton.
"""
