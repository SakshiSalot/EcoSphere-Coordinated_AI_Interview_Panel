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
import time
from typing import Iterator, Optional

from src.analysis import pipeline
from src.conductor.conductor import decide_floor, holds_floor, note_persona_turn
from src.conductor.turn import observe, speak
from src.state.session import SessionState, get_session

log = logging.getLogger("contract")

# Agora calls every agent for the same turn, near-simultaneously. Only the
# first arrival may advance the interview state; the rest read what it wrote.
_TURN_LOCK = threading.Lock()

_PLACEHOLDER = re.compile(r"^\s*\(.*\)\s*$")  # "(the candidate has just joined)"

# Identical text arriving within this many seconds is the same turn reaching us
# from another agent, not the candidate repeating themselves. Nobody finishes a
# sentence and says it again word for word inside ten seconds.
SAME_TURN_WINDOW = 10.0


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

        # One utterance per answer. Agora re-asks whenever it thinks a turn
        # ended — a cough, a pause, a false barge-in — and answering each of
        # those with a fresh question is how a live run produced four
        # different questions in a row while the candidate was still thinking
        # about the first.
        answers = len(session.turns.by_speaker("candidate"))
        if speaking and session.spoke_after_answers == answers:
            log.info("%s/%s: nothing new said — staying silent", session_id, role)
            speaking = False
        elif speaking:
            session.spoke_after_answers = answers

    if not speaking:
        return None

    text, _calls = speak(session, role)
    if not text.strip():
        # A persona that called a tool but said nothing should stay silent
        # rather than emit an empty utterance the TTS will trip over.
        return None

    question_turn_id = note_persona_turn(session, role, text)

    # Build this question's marking scheme while the candidate is answering it.
    # Returns immediately; the work happens on a background thread. Doing it
    # now rather than when the answer lands is also what keeps the mark
    # defensible — a rubric written before the answer exists cannot be shaped
    # by it.
    pipeline.on_question_asked(session, question_turn_id)

    return iter(chunks(text))


def _advance(session: SessionState, messages: list) -> None:
    """Record the candidate's newest answer, react to it, and pick the floor.

    Compares TEXT, never message counts.

    Counting looked simpler and was wrong twice over. Each Agora agent keeps
    its OWN history, so a silent persona's payload barely grows while the
    floor holder's does; and every history is capped at `max_history`, so once
    the interview passes ten messages the count stops rising altogether. A
    live run died exactly there — five good turns, then "nothing new said"
    forever, because `len(said) <= already` had become permanently true.

    Idempotent: whichever persona's request arrives first does this work, and
    the other two see the result.
    """
    latest = _latest_candidate_text(messages)
    if not latest:
        return

    last = session.turns.last_candidate()

    if last is not None:
        if _same(last.text, latest):
            # Identical text means one of two very different things, and the
            # only signal that separates them reliably is TIME:
            #
            #   moments ago -> this is another persona's request for the same
            #                  turn. Agora calls every agent at once, and they
            #                  land in an unpredictable order.
            #   a while ago -> the candidate genuinely said the same thing
            #                  again, which is what a stuck or evasive one does.
            #
            # Anything based on "has a persona replied yet" is ordering-
            # dependent and duplicates the turn when a slow agent's request
            # arrives after the floor holder has already spoken.
            if time.time() - last.started_at < SAME_TURN_WINDOW:
                return

        # Same turn, more words. Agora calls the moment it thinks a turn ended
        # and calls again as the candidate carries on, so the last message
        # grows between calls. Extend rather than drop, or the transcript — and
        # everything marked from it — keeps half a sentence.
        if _extends(last.text, latest):
            session.turns.extend(last.turn_id, latest)
            # Let the floor holder answer the COMPLETED sentence. Without this
            # the panel replies to a fragment and then never speaks again.
            session.spoke_after_answers = -1
            log.info("turn %d extended to %d chars", last.turn_id, len(latest))
            return

    turn_id = session.turns.add("candidate", latest, difficulty=session.difficulty)

    # Two tiers of the same detector, writing to one flag store. The heuristic
    # runs inline because the conductor needs a signal on this turn; the judge
    # is dispatched to a thread and lands a couple of seconds later, seeing the
    # dodges a word list cannot.
    observe(session, turn_id, latest)
    pipeline.on_answer_recorded(session, turn_id)

    role, reason = decide_floor(session)
    log.info("turn %d -> floor: %s (%s)", turn_id, role, reason)


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()





def _extends(recorded: str, latest: str) -> bool:
    """Is `latest` the same utterance, continued?"""
    a = " ".join(recorded.split()).lower()
    b = " ".join(latest.split()).lower()
    return len(b) > len(a) and b.startswith(a[: max(12, len(a) // 2)])


def _latest_candidate_text(messages: list) -> str:
    """The most recent thing the candidate said, or "".

    Only the last one matters: the history is per-agent and truncated, so
    anything earlier may or may not be present depending on which persona is
    asking.
    """
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = (m.get("content") or "").strip()
        if not content or _PLACEHOLDER.match(content):
            continue
        return content
    return ""


def chunks(text: str) -> list[str]:
    """Emit the reply as a single delta.

    Artificial chunking was actively harmful here. Agora appears to strip
    whitespace at the edges of each delta, so splitting on word boundaries
    concatenated to "real load.What broke first?" — the words ran together and
    the TTS read them that way.

    There was also nothing to gain: this path generates the whole reply before
    returning, so slicing it up afterwards cannot make speech start any
    earlier. Real chunks arrive with their own spacing once the streaming
    path is wired, and that is where the latency win actually lives.
    """
    return [text] if text else []
