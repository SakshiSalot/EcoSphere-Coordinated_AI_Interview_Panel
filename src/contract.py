"""The one contract between the two lanes.

Written on Day 1 and never changed. The platform lane builds all the HTTP, SSE,
Agora and provider plumbing *around* this function; the intelligence lane
builds every interview decision *behind* it. The mock harness calls the
identical function, which is why the interview brain can be built and tested
with Agora switched off.
"""

import logging
import re
import threading
from typing import Iterator, Optional

from src.conductor.conductor import decide_floor, holds_floor, note_persona_turn
from src.conductor.turn import observe, speak
from src.state.session import SessionState, get_session

log = logging.getLogger("contract")

# Agora calls every agent for the same turn, near-simultaneously. Only the
# first arrival may advance the interview state; the rest read what it wrote.
_TURN_LOCK = threading.Lock()

_PLACEHOLDER = re.compile(r"^\s*\(.*\)\s*$")  # "(the candidate has just joined)"


def next_utterance(
    session_id: str,
    role: str,
    messages: list,
) -> Optional[Iterator[str]]:
    """Decide whether `role` speaks this turn, and if so produce its reply.

    Returns:
        None            -> this persona is silent this turn. The gateway emits
                           an empty completion and makes no model call: no
                           cost, no speech.
        Iterator[str]   -> this persona holds the floor. The gateway streams
                           these text chunks to Agora as SSE.
    """
    session = get_session(session_id)

    with _TURN_LOCK:
        _advance(session, messages)
        speaking = holds_floor(session, role)

    if not speaking:
        return None

    text, _calls = speak(session, role)
    if not text.strip():
        # A persona that called a tool but said nothing should stay silent
        # rather than emit an empty utterance the TTS will trip over.
        return None

    note_persona_turn(session, role, text)
    return iter(chunks(text))


def _advance(session: SessionState, messages: list) -> None:
    """Record the candidate's newest answers, react to them, and pick the floor.

    Idempotent: whichever persona's request arrives first does this work, and
    the other two see the result.

    Deduplication is by *count*, not by comparing text. A candidate who
    repeats themselves — which is exactly what a stuck or evasive one does —
    would otherwise have the second answer silently discarded, and the
    vagueness counter would never reach two.
    """
    said = _candidate_texts(messages)
    already = len(session.turns.by_speaker("candidate"))
    if len(said) <= already:
        return

    for text in said[already:]:
        turn_id = session.turns.add("candidate", text, difficulty=session.difficulty)
        observe(session, turn_id, text)

    role, reason = decide_floor(session)
    log.info("turn %d -> floor: %s (%s)", len(session.turns), role, reason)


def _candidate_texts(messages: list) -> list[str]:
    out: list[str] = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = (m.get("content") or "").strip()
        if not content or _PLACEHOLDER.match(content):
            continue
        out.append(content)
    return out


def chunks(text: str, size: int = 48) -> list[str]:
    """Split a reply into small pieces so speech starts before the sentence
    is finished. Splits on word boundaries — a chunk that ends mid-word makes
    the TTS mispronounce it."""
    words, out, buf = text.split(), [], ""
    for w in words:
        if buf and len(buf) + len(w) + 1 > size:
            out.append(buf + " ")
            buf = w
        else:
            buf = f"{buf} {w}".strip()
    if buf:
        out.append(buf)
    return out
