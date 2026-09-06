"""
/compact command handler - compresses older conversation history.

Long-running conversations grow past the model's context window.  ``/compact``
keeps the most recent turns untouched and replaces everything before them with
a single "[RECAP OF PRIOR WORK]" assistant message produced by a dedicated
LLM call (the Context Compression Engine prompt), so the conversation stays
within the context while the recent history stays verbatim.

The last ``KEEP_TURNS`` turns (recorded as ``history_turns``, the
row counts ``/history`` would render before each user prompt) form the "keep
zone" and are left untouched.  Everything before them (after the system
prompt) is sent to the model for compression.

History is only compacted when the portion to be replaced is large enough
(``MIN_COMPACT_TOKENS``); otherwise the command is disabled with a
user-facing warning.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from ...llm_clients import RequestCancelled
from ...llm_clients.openai.responses_items import message_item
from ..conversation import (
    effective_rows,
    is_stateless_conversation,
    items_to_rows,
    messages_to_rows,
    slice_items_by_row_range,
)
from .base import CmdHandler
from .registry import register_command

#: System prompt for the dedicated compaction LLM call.  The model returns a
#: strict JSON object describing the conversation; the fields are rendered
#: into the "[RECAP OF PRIOR WORK]" assistant message of the new history.
SYSTEM_COMPACT_PROMPT = """\
You are a Context Compression Engine. Compress the provided conversation into a
strict JSON object. Do not add conversational filler.

Extract the following fields:
1. "goal": (string) The user's ultimate primary objective.
2. "completed_steps": (array of strings) Explicitly finished tasks or code blocks.
3. "current_blocker": (string or null) The most recent error, limitation, or
   unresolved issue.
4. "explicit_constraints": (array of strings) Any hard rules the user specified
   (e.g., "must use Python 3.10", "must be memory-efficient").
5. "code_state": (string or null) The current version of the code/logic if one
   exists.
6. "unresolved_questions": (array of strings) Any questions the user asked that
   remain unanswered.

CRITICAL RULES:
- If a user explicitly says "No" or rejects a suggestion, mark that decision in
  "completed_steps" with a "REJECTED:" prefix.
- Preserve exact file paths, function names, and variable names verbatim.
- Ignore all greetings, pleasantries, and off-topic digressions.
- Output ONLY the JSON. No markdown, no explanations.
"""

#: Minimum estimated size (tokens) of the history to compact.  Below this
#: /compact is disabled with a user-facing warning ("Conversation too short to
#: compact effectively.").
MIN_COMPACT_TOKENS = 2000

#: Number of most recent turns kept untouched by /compact.
KEEP_TURNS = 3

_console = Console(markup=False)


def _history_mode(shell) -> str:
    """Detect where the conversation lives.

    Returns one of:

    - ``"stateless"``: Responses input items hold the full conversation
      client-side (the system prompt is folded in on the first turn, e.g.
      DeepSeek's stateless ``/responses`` endpoint).
    - ``"server_side"``: the Responses API keeps the history on the server
      (chained via ``previous_response_id``); the shell only mirrors the
      completed turns for ``/history`` (e.g. OpenAI).
    - ``"completions"``: the client-side ``messages_history`` holds the whole
      conversation (Completions / Anthropic / DashScope / Gemini).
    """
    conversation_items = getattr(shell, "conversation_items", None)
    if is_stateless_conversation(shell):
        return "stateless"
    if conversation_items is not None:
        # Either a stateless full history without a system prompt (-Z mode)
        # or a server-side conversation with pending (Enter-cancelled)
        # messages / a post-compaction seed.  Server-side state elsewhere
        # decides; a system prompt living in messages_history (not folded into
        # the items) is also server-side.
        if (
            getattr(shell, "previous_response_id", None)
            or getattr(shell, "mirrored_history", None)
            or getattr(shell, "response_chain", None)
            or (
                shell.messages_history
                and shell.messages_history[0].get("role") == "system"
            )
        ):
            return "server_side"
        return "stateless"
    if (
        getattr(shell, "previous_response_id", None)
        or getattr(shell, "mirrored_history", None)
        or getattr(shell, "response_chain", None)
    ):
        return "server_side"
    return "completions"


def _sanitize_response_items(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop unpaired ``function_call`` / ``function_call_output`` items.

    The Responses API rejects a history where a ``function_call`` has no
    matching ``function_call_output`` ("No tool output found for function
    call ...").  Slicing the compact/keep zones by turn boundaries can
    strand one side of a tool round, so the compaction payload keeps only
    complete pairs (matched on ``call_id``); ``message`` / ``reasoning``
    items pass through untouched.
    """
    calls = {e.get("call_id") for e in entries if e.get("type") == "function_call"}
    outputs = {
        e.get("call_id") for e in entries if e.get("type") == "function_call_output"
    }
    complete = calls & outputs
    return [
        e
        for e in entries
        if e.get("type") not in ("function_call", "function_call_output")
        or e.get("call_id") in complete
    ]


def _sanitize_messages(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop unpaired Completions ``tool_calls`` / ``tool`` messages.

    Mirrors :func:`_sanitize_response_items` for the Completions shape: an
    assistant message whose ``tool_calls`` have no matching ``tool`` reply
    (or a ``tool`` reply with no call) makes some providers reject the
    compaction call, so the orphan side is removed.  Assistant ``tool_calls``
    without any output are stripped (the text is kept); lone ``tool``
    messages are dropped.
    """
    wanted: set[str] = set()
    for e in entries:
        for tc in (e.get("tool_calls") or []):
            wanted.add(tc.get("id"))
    have = {
        e.get("tool_call_id") for e in entries if e.get("role") == "tool"
    }
    complete = wanted & have
    out: list[dict[str, Any]] = []
    for e in entries:
        if e.get("role") == "tool":
            if e.get("tool_call_id") in complete:
                out.append(e)
            continue
        tcs = e.get("tool_calls")
        if tcs:
            kept = [tc for tc in tcs if tc.get("id") in complete]
            if len(kept) != len(tcs):
                e = dict(e)
                if kept:
                    e["tool_calls"] = kept
                else:
                    e.pop("tool_calls", None)
            out.append(e)
        else:
            out.append(e)
    return out


class _HistoryStrategy:
    """Per-API-mode strategy for reading and rebuilding the conversation.

    Each mode (``completions`` / ``stateless`` / ``server_side``) owns where
    the history lives and how /compact slices and rebuilds it, so the
    handler stops switching on the mode string in six places (rows, compact
    zone, keep zone, context application, compaction call args).
    """

    mode = ""

    def effective_rows(self, shell) -> list[tuple[str, str]]:
        """Return ``(role, content)`` rows for the whole effective history.

        Mirrors the source selection of the ``/history`` command (see
        ``janito.shell.cmds.history``) so the recorded values (from
        ``_history_row_count``) index directly into these rows in every API
        mode.
        """
        raise NotImplementedError

    def compact_zone(self, shell, skip: int, keep_start: int) -> list[dict[str, Any]]:
        """Return the raw storage entries of the zone to compact, API-native.

        The compaction LLM call must re-send the history in the exact format
        the provider expects, so the flattened ``(role, content)`` rows are
        *not* used here -- the underlying entries are re-sliced from their
        native storage (Completions message dicts / Responses input items).
        ``skip`` is the row index where the compact zone starts (0 or 1,
        after the optional system prompt) and ``keep_start`` the row index
        where it ends (the ``history_turns`` value); both index into the
        ``/history`` display rows.
        """
        raise NotImplementedError

    def keep_zone(self, shell, keep_start: int) -> list[dict[str, Any]]:
        """Return the storage entries of the untouched keep zone."""
        raise NotImplementedError

    def compaction_call_args(
        self, compact_entries: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
        """Return ``(compact_messages, compact_items)`` for the compaction call."""
        raise NotImplementedError

    def apply(self, shell, new_context, keep_zone_entries) -> None:
        """Reset the conversation state to the compacted context.

        Runs the mode-specific rebuild (:meth:`_apply_conversation`) and then
        resets every turn/response tracker: the compacted baseline is the new
        conversation start, so /rewind steps back from there and the next
        turn starts a fresh server conversation (Responses modes) or uses the
        rebuilt client-side history (Completions modes).
        """
        self._apply_conversation(shell, new_context, keep_zone_entries)
        shell.history_turns = []
        shell.previous_response_id = None
        shell.response_chain = []
        shell.response_turn = 0
        shell.mirrored_history = []
        shell.mirrored_turn = 0

    def _apply_conversation(self, shell, new_context, keep_zone_entries) -> None:
        raise NotImplementedError


class _CompletionsStrategy(_HistoryStrategy):
    """Completions / Anthropic / DashScope / Gemini: client-side messages."""

    mode = "completions"

    def effective_rows(self, shell) -> list[tuple[str, str]]:
        return messages_to_rows(shell.messages_history)

    def compact_zone(self, shell, skip: int, keep_start: int) -> list[dict[str, Any]]:
        return _sanitize_messages(list(shell.messages_history[skip:keep_start]))

    def keep_zone(self, shell, keep_start: int) -> list[dict[str, Any]]:
        return _sanitize_messages(list(shell.messages_history[keep_start:]))

    def compaction_call_args(
        self, compact_entries: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
        return (
            [{"role": "system", "content": SYSTEM_COMPACT_PROMPT}, *compact_entries],
            None,
        )

    def _apply_conversation(self, shell, new_context, keep_zone_entries) -> None:
        shell.messages_history = new_context
        shell.conversation_items = None
        shell.conversation_turn = 0


class _StatelessStrategy(_HistoryStrategy):
    """Stateless Responses: the full conversation lives in input items."""

    mode = "stateless"

    def effective_rows(self, shell) -> list[tuple[str, str]]:
        return items_to_rows(shell.conversation_items or [])

    def compact_zone(self, shell, skip: int, keep_start: int) -> list[dict[str, Any]]:
        return _sanitize_response_items(
            slice_items_by_row_range(shell.conversation_items or [], skip, keep_start)
        )

    def keep_zone(self, shell, keep_start: int) -> list[dict[str, Any]]:
        items = list(shell.conversation_items or [])
        rows = len(items_to_rows(items))
        return _sanitize_response_items(
            slice_items_by_row_range(items, keep_start, rows)
        )

    def compaction_call_args(
        self, compact_entries: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
        compact_items = list(compact_entries)
        compact_items.insert(0, message_item("system", SYSTEM_COMPACT_PROMPT))
        return None, compact_items

    def _apply_conversation(self, shell, new_context, keep_zone_entries) -> None:
        recap = _find_recap(new_context)
        new_items: list[dict[str, Any]] = []
        system_prompt = shell.get_system_prompt()
        if system_prompt:
            new_items.append(message_item("system", system_prompt))
        new_items.append(message_item("assistant", recap))
        new_items.extend(keep_zone_entries)
        shell.conversation_items = new_items
        shell.conversation_turn = len(new_items)


class _ServerSideStrategy(_HistoryStrategy):
    """Server-side Responses: mirror of completed turns + pending items."""

    mode = "server_side"

    def effective_rows(self, shell) -> list[tuple[str, str]]:
        # Same composition as /history (see
        # janito.shell.conversation.effective_rows): the stateless branch
        # never applies in server-side mode.
        return effective_rows(shell)

    def compact_zone(self, shell, skip: int, keep_start: int) -> list[dict[str, Any]]:
        # Display rows = messages_history + mirrored_history + pending
        # conversation_items, mapped back to their storage slices.
        msgs_len = len(shell.messages_history)
        mirrored = shell.mirrored_history or []
        pending = shell.conversation_items or []
        entries: list[dict[str, Any]] = []
        # messages part (Completions-style dicts).  In practice server-side
        # keeps only the system prompt here and skip=1 excludes it.
        if skip < msgs_len:
            entries.extend(shell.messages_history[skip : min(keep_start, msgs_len)])
        # mirrored part
        mir_start = msgs_len
        mir_end = mir_start + len(mirrored)
        if keep_start > mir_start:
            lo = max(skip, mir_start) - mir_start
            hi = min(keep_start, mir_end) - mir_start
            if lo < hi:
                entries.extend(mirrored[lo:hi])
        # pending part
        pend_start = mir_end
        if keep_start > pend_start:
            lo = max(skip, pend_start) - pend_start
            hi = keep_start - pend_start
            if lo < hi:
                entries.extend(pending[lo:hi])
        return _sanitize_messages(_sanitize_response_items(entries))

    def keep_zone(self, shell, keep_start: int) -> list[dict[str, Any]]:
        msgs_len = len(shell.messages_history)
        mirrored = shell.mirrored_history or []
        pending = shell.conversation_items or []
        if keep_start <= msgs_len:
            return list(mirrored) + list(pending)
        offset = keep_start - msgs_len
        if offset >= len(mirrored):
            keep = list(pending[offset - len(mirrored) :])
        else:
            keep = list(mirrored[offset:]) + list(pending)
        return _sanitize_messages(_sanitize_response_items(keep))

    def compaction_call_args(
        self, compact_entries: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
        return None, list(compact_entries)

    def _apply_conversation(self, shell, new_context, keep_zone_entries) -> None:
        # The system prompt stays in messages_history / instructions; the
        # recap + keep zone seed the next fresh server turn as input items.
        recap = _find_recap(new_context)
        new_items = [message_item("assistant", recap)]
        new_items.extend(keep_zone_entries)
        shell.conversation_items = new_items
        shell.conversation_turn = len(new_items)


_STRATEGIES = {
    "completions": _CompletionsStrategy(),
    "stateless": _StatelessStrategy(),
    "server_side": _ServerSideStrategy(),
}


def _history_strategy(shell) -> _HistoryStrategy:
    """Return the history strategy for the shell's conversation mode.

    The mode is detected by :func:`_history_mode`; the returned strategy
    owns the mode-specific row/zone/apply behaviour for /compact.
    """
    return _STRATEGIES[_history_mode(shell)]


def _estimate_tokens(rows: list[tuple[str, str]]) -> int:
    """Rough token estimate for a list of ``(role, content)`` rows.

    Uses the common ~4 characters per token heuristic plus a small per-message
    overhead; good enough to gate /compact on a 2,000-token threshold without
    a tokenizer dependency.
    """
    total = 0
    for role, content in rows:
        total += (len(role) + len(content)) // 4 + 4
    return total


def _parse_compaction_response(text: str) -> Any:
    """Parse the compaction LLM's output.

    Strips optional markdown code fences, then ``json.loads``.  When the model
    did not return parseable JSON, the raw text is returned so it can be used
    as the recap narrative as-is.
    """
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def format_compacted_json_to_narrative(compacted: Any) -> str:
    """Render the compacted JSON into a readable recap narrative.

    When the model did not return parseable JSON, ``compacted`` is the raw
    assistant text and is used as the narrative as-is.  Exact file paths,
    function names and variable names are preserved verbatim (they come from
    the JSON the model produced from the conversation).
    """
    if not isinstance(compacted, dict):
        return str(compacted) if compacted else ""
    lines: list[str] = []
    goal = compacted.get("goal")
    if goal:
        lines.append(f"Goal: {goal}")
    completed = compacted.get("completed_steps") or []
    if completed:
        lines.append("Completed steps: " + "; ".join(completed))
    blocker = compacted.get("current_blocker")
    if blocker:
        lines.append(f"Current blocker: {blocker}")
    constraints = compacted.get("explicit_constraints") or []
    if constraints:
        lines.append("Explicit constraints: " + "; ".join(constraints))
    code_state = compacted.get("code_state")
    if code_state:
        lines.append(f"Code state: {code_state}")
    questions = compacted.get("unresolved_questions") or []
    if questions:
        lines.append("Unresolved questions: " + "; ".join(questions))
    return "\n".join(lines)


def _build_new_context(
    system_prompt: str | None,
    compacted: Any,
    keep_zone_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the post-compaction history (reference implementation).

    - The system prompt stays at the top.
    - The compaction result is injected as an assistant "[RECAP OF PRIOR
      WORK]" message.
    - The untouched recent history (``keep_zone_entries``) is appended.

    Returns Completions-style ``{"role": ..., "content": ...}`` messages;
    Responses modes convert them into input items when applying the context.
    """
    new_context: list[dict[str, Any]] = []
    if system_prompt:
        new_context.append({"role": "system", "content": system_prompt})
    narrative = format_compacted_json_to_narrative(compacted)
    recap = f"[RECAP OF PRIOR WORK] {narrative}".strip()
    new_context.append({"role": "assistant", "content": recap})
    new_context.extend(keep_zone_entries)
    return new_context


def _find_recap(new_context: list[dict[str, Any]]) -> str:
    """Return the recap content from a built new context (the first assistant
    message, which /compact always injects)."""
    for msg in new_context:
        if msg.get("role") == "assistant":
            return msg.get("content") or ""
    return ""


def _compaction_turn_func(shell):
    """Return a turn callable for the compaction call, silent observer included.

    The compression call must not echo the model's raw recap output to the
    terminal, but must keep the progress bar / Enter-to-cancel: it is built
    through the session's turn factory with ``silent=True``, which swaps the
    Rich observer for the silent variant (still recording the accounting
    row) while keeping the injected TUI stream runner.  The factory is
    re-invoked with the shell's current provider / model override / thinking
    flag, exactly like ``/provider``, ``/model`` and ``/thinking`` rebind the
    session send function.

    Falls back to the plain ``shell.turn_func`` (the Rich observer variant)
    only when the shell has no factory -- bare test shells, or a factory
    stub without the ``silent`` kwarg -- so compaction still works there.
    """
    factory = getattr(shell, "turn_factory", None)
    if factory is not None:
        try:
            return factory(
                getattr(shell, "provider", None),
                model_override=getattr(shell, "model_override", None),
                thinking_override=getattr(shell, "thinking", None),
                silent=True,
            )
        except TypeError:
            # A factory that predates the silent kwarg (or a test stub):
            # fall back to the plain session turn function.
            pass
    return getattr(shell, "turn_func", None)


class CompactCmdHandler(CmdHandler):
    """Command handler for /compact - compress older conversation history."""

    @property
    def name(self) -> str:
        return "/compact"

    @property
    def description(self) -> str:
        return "Compress older conversation history"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /compact command."""
        if user_input.lower().strip() == self.name.lower():
            self._do_compact(shell)
            return True
        return False

    def _do_compact(self, shell) -> None:
        """Compact the history before the last KEEP_TURNS turns."""
        turns = getattr(shell, "history_turns", None) or []
        if len(turns) < KEEP_TURNS:
            _console.print("Conversation too short to compact effectively.")
            return
        keep_start = turns[-KEEP_TURNS]

        strategy = _history_strategy(shell)
        rows = strategy.effective_rows(shell)
        # The system prompt (when present) is the first row and stays at the
        # top of the new context; everything before the keep zone after it is
        # compacted.
        skip = 1 if (rows and rows[0][0] == "system") else 0
        compact_rows = rows[skip:keep_start]
        keep_rows = rows[keep_start:]
        if not compact_rows or not keep_rows:
            _console.print("Conversation too short to compact effectively.")
            return
        if _estimate_tokens(compact_rows) < MIN_COMPACT_TOKENS:
            _console.print("Conversation too short to compact effectively.")
            return
        compact_entries = strategy.compact_zone(shell, skip, keep_start)
        if not compact_entries:
            _console.print("Conversation too short to compact effectively.")
            return

        print()
        _console.print("Compacting conversation history...")
        compacted = self._compact(shell, strategy, compact_entries)
        if compacted is None:
            return

        keep_zone = strategy.keep_zone(shell, keep_start)
        new_context = _build_new_context(
            shell.get_system_prompt(), compacted, keep_zone
        )
        strategy.apply(shell, new_context, keep_zone)
        _console.print(
            f"Compacted: {len(compact_rows)} message(s) replaced by a recap "
            f"(last {KEEP_TURNS} turns kept verbatim). "
            f"History now has {len(strategy.effective_rows(shell))} message(s)."
        )

    def _compact(
        self, shell, strategy: _HistoryStrategy, compact_entries: list[dict[str, Any]]
    ) -> Any:
        """Run the compression-engine LLM call; return the parsed JSON.

        The call reuses the session's ``turn_func`` (so the current
        provider / model / API type apply) with a **local** conversation built
        from the compaction system prompt plus the raw history entries of the
        compact zone -- the main conversation is never touched by this side
        call, and tool-call rounds are preserved in their native format.
        The call is built through the session's turn factory with
        ``silent=True`` (see :func:`_compaction_turn_func`), so it runs with
        the silent turn observer -- the raw recap JSON is never echoed, only
        the progress bar shows -- while the accounting row is still recorded.

        The mode-specific argument shape comes from the strategy:

        - ``completions`` mode (Completions / Anthropic / DashScope / Gemini):
          the raw ``messages_history`` dicts (including ``tool_calls`` /
          ``tool`` roles) are passed as ``previous_messages`` with the
          compaction system prompt on top.  The Anthropic/Gemini clients fold
          the leading system message into their top-level ``system`` param,
          and DashScope sees an existing system message so it does not prepend
          ``instructions`` again.
        - ``stateless`` Responses: the raw input items (including
          ``function_call`` / ``function_call_output`` items) are passed as
          ``previous_items`` with a system item prepended (stateless providers
          only fold ``instructions`` into an empty history).
        - ``server_side`` Responses: the raw mirrored/pending items are passed
          as ``previous_items`` and the compaction system prompt as
          ``instructions`` (sent on the first turn of the fresh conversation).

        Returns ``None`` on cancellation/error (the history is left unchanged).
        """
        turn_func = _compaction_turn_func(shell)
        if turn_func is None:
            print(
                "\nError: No prompt function available. Are you in an active session?\n"
            )
            return None

        compact_messages, compact_items = strategy.compaction_call_args(compact_entries)

        prompt = (
            "Compress the conversation above into the required JSON object. "
            "Output ONLY the JSON. No markdown, no explanations."
        )
        try:
            result = turn_func(
                prompt,
                verbose=False,
                previous_messages=compact_messages,
                previous_response_id=None,
                previous_items=compact_items,
                instructions=SYSTEM_COMPACT_PROMPT,
                tools=[],
            )
        except KeyboardInterrupt:
            print("Compaction interrupted; conversation left unchanged.")
            return None
        except RequestCancelled:
            print("Compaction cancelled (Enter); conversation left unchanged.")
            return None
        except Exception as e:
            print(f"Error during compaction: {e}")
            return None
        # Responses returns a ConversationResult; the stateless clients return
        # the assistant text directly.
        text = getattr(result, "content", None)
        if text is None:
            text = result
        return _parse_compaction_response(str(text))


# Register this handler
_handler = CompactCmdHandler()
register_command(_handler)
