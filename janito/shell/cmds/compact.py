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

from ...openai_client import RequestCancelled
from .base import CmdHandler
from .history import _responses_item_to_row
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
    if conversation_items and conversation_items[0].get("role") == "system":
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


def _effective_rows(shell) -> list[tuple[str, str]]:
    """Return ``(role, content)`` rows for the whole effective history.

    Mirrors the source selection of the ``/history`` command (see
    ``janito.shell.cmds.history``) so the recorded values (from
    ``_history_row_count``) index directly into these rows in every API mode.
    """
    mode = _history_mode(shell)
    if mode == "stateless":
        return [
            _responses_item_to_row(item) for item in (shell.conversation_items or [])
        ]
    rows: list[tuple[str, str]] = []
    for msg in shell.messages_history:
        if isinstance(msg, dict):
            rows.append((msg.get("role", "unknown"), msg.get("content") or ""))
        else:
            rows.append((msg.role, msg.content or ""))
    if mode == "server_side":
        rows.extend(
            _responses_item_to_row(item) for item in (shell.mirrored_history or [])
        )
        rows.extend(
            _responses_item_to_row(item) for item in (shell.conversation_items or [])
        )
    return rows


def _compact_zone_entries(
    shell, mode: str, skip: int, keep_start: int
) -> list[dict[str, Any]]:
    """Return the raw storage entries of the zone to compact, in API-native form.

    The compaction LLM call must re-send the history in the exact format the
    provider expects, so the flattened ``(role, content)`` rows are *not* used
    here -- the underlying entries are:

    - ``completions`` mode: Completions-style message dicts from
      ``messages_history``, including assistant messages with ``tool_calls``
      and ``role: "tool"`` results from tool rounds.
    - ``stateless`` Responses: Responses input items from
      ``conversation_items``, including ``function_call`` /
      ``function_call_output`` items.
    - ``server_side`` Responses: the display-only ``mirrored_history`` items
      plus any pending ``conversation_items`` (the system prompt lives in
      ``messages_history`` and is excluded via ``skip``).

    ``skip`` is the row index where the compact zone starts (0 or 1, after the
    optional system prompt) and ``keep_start`` the row index where it ends
    (the ``history_turns`` value); both index into the ``/history``
    display rows, so ``messages_history`` + ``mirrored_history`` +
    ``conversation_items`` are mapped back to their storage slices.
    """
    if mode == "completions":
        return list(shell.messages_history[skip:keep_start])
    if mode == "stateless":
        return list((shell.conversation_items or [])[skip:keep_start])
    # Server-side Responses: display rows = messages_history + mirrored_history
    # + pending conversation_items.
    msgs_len = len(shell.messages_history)
    mirrored = shell.mirrored_history or []
    pending = shell.conversation_items or []
    entries: list[dict[str, Any]] = []
    # messages part (Completions-style dicts).  In practice server-side keeps
    # only the system prompt here and skip=1 excludes it, so this is empty.
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
    return entries


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
    keep_zone_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the post-compaction history (reference implementation).

    - The system prompt stays at the top.
    - The compaction result is injected as an assistant "[RECAP OF PRIOR
      WORK]" message.
    - The untouched recent history (``keep_zone_messages``) is appended.

    Returns Completions-style ``{"role": ..., "content": ...}`` messages;
    Responses modes convert them into input items when applying the context.
    """
    new_history: list[dict[str, Any]] = []
    if system_prompt:
        new_history.append({"role": "system", "content": system_prompt})
    narrative = format_compacted_json_to_narrative(compacted)
    recap = f"[RECAP OF PRIOR WORK] {narrative}".strip()
    new_history.append({"role": "assistant", "content": recap})
    new_history.extend(keep_zone_messages)
    return new_history


def _message_item(role: str, text: str) -> dict[str, Any]:
    """Build a Responses ``message`` input item (system/user use input_text,
    assistant uses output_text, matching how the client builds its items)."""
    block_type = "output_text" if role == "assistant" else "input_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": block_type, "text": text}],
    }


def _find_recap(new_history: list[dict[str, Any]]) -> str:
    """Return the recap content from a built new context (the first assistant
    message, which /compact always injects)."""
    for msg in new_history:
        if msg.get("role") == "assistant":
            return msg.get("content") or ""
    return ""


def _keep_zone_messages(shell, mode: str, keep_start: int) -> list[dict[str, Any]]:
    """Return the storage entries (dicts/items) of the untouched keep zone.

    ``keep_start`` is a row index into the effective /history display (a
    recorded turn-start value).  Completions mode returns ``messages_history`` dicts;
    the Responses modes return Responses input items.
    """
    if mode == "completions":
        return list(shell.messages_history[keep_start:])
    if mode == "stateless":
        return list((shell.conversation_items or [])[keep_start:])
    # Server-side Responses: rows are messages_history + mirrored_history +
    # pending conversation_items; keep zone may span the last two.
    msgs_len = len(shell.messages_history)
    mirrored = shell.mirrored_history or []
    pending = shell.conversation_items or []
    if keep_start <= msgs_len:
        return list(mirrored) + list(pending)
    offset = keep_start - msgs_len
    if offset >= len(mirrored):
        return list(pending[offset - len(mirrored) :])
    return list(mirrored[offset:]) + list(pending)


def _apply_new_context(shell, mode: str, new_history, keep_zone_messages) -> None:
    """Reset the conversation state to the compacted context.

    Resets every turn/response tracker: the compacted baseline is the
    new conversation start, so /rewind steps back from there and the next turn
    starts a fresh server conversation (Responses modes) or uses the rebuilt
    client-side history (Completions modes).
    """
    if mode == "completions":
        shell.messages_history = new_history
        shell.conversation_items = None
        shell.conversation_turn = 0
    else:
        recap = _find_recap(new_history)
        if mode == "stateless":
            new_items: list[dict[str, Any]] = []
            system_prompt = shell.get_system_prompt()
            if system_prompt:
                new_items.append(_message_item("system", system_prompt))
            new_items.append(_message_item("assistant", recap))
            new_items.extend(keep_zone_messages)
            shell.conversation_items = new_items
            shell.conversation_turn = len(new_items)
        else:  # server_side: system prompt stays in messages_history /
            # instructions; the recap + keep zone seed the next fresh server
            # turn as input items.
            new_items = [_message_item("assistant", recap)]
            new_items.extend(keep_zone_messages)
            shell.conversation_items = new_items
            shell.conversation_turn = len(new_items)
    # The compacted baseline is the new conversation start: every turn
    # and server-conversation tracker is reset so /rewind steps back from
    # here and the next turn starts fresh.
    shell.history_turns = []
    shell.previous_response_id = None
    shell.response_chain = []
    shell.response_turn = 0
    shell.mirrored_history = []
    shell.mirrored_turn = 0


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

        mode = _history_mode(shell)
        rows = _effective_rows(shell)
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
        compact_entries = _compact_zone_entries(shell, mode, skip, keep_start)
        if not compact_entries:
            _console.print("Conversation too short to compact effectively.")
            return

        print()
        _console.print("Compacting conversation history...")
        compacted = self._compact(shell, mode, compact_entries)
        if compacted is None:
            return

        keep_zone = _keep_zone_messages(shell, mode, keep_start)
        new_history = _build_new_context(
            shell.get_system_prompt(), compacted, keep_zone
        )
        _apply_new_context(shell, mode, new_history, keep_zone)
        _console.print(
            f"Compacted: {len(compact_rows)} message(s) replaced by a recap "
            f"(last {KEEP_TURNS} turns kept verbatim). "
            f"History now has {len(_effective_rows(shell))} message(s)."
        )

    def _compact(self, shell, mode: str, compact_entries: list[dict[str, Any]]) -> Any:
        """Run the compression-engine LLM call; return the parsed JSON.

        The call reuses the session's ``send_prompt_func`` (so the current
        provider / model / API type apply) with a **local** conversation built
        from the compaction system prompt plus the raw history entries of the
        compact zone -- the main conversation is never touched by this side
        call, and tool-call rounds are preserved in their native format:

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
        send_prompt_func = getattr(shell, "send_prompt_func", None)
        if send_prompt_func is None:
            print(
                "\nError: No prompt function available. Are you in an active session?\n"
            )
            return None

        if mode == "completions":
            compact_messages: list[dict[str, Any]] | None = [
                {"role": "system", "content": SYSTEM_COMPACT_PROMPT},
                *compact_entries,
            ]
            compact_items = None
        else:
            compact_messages = None
            compact_items = list(compact_entries)
            if mode == "stateless":
                compact_items.insert(0, _message_item("system", SYSTEM_COMPACT_PROMPT))

        prompt = (
            "Compress the conversation above into the required JSON object. "
            "Output ONLY the JSON. No markdown, no explanations."
        )
        try:
            result = send_prompt_func(
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
