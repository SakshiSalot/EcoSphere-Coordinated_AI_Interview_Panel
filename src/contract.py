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

    if last is not None and _same_utterance(session, last, latest):
        # Touched, so the window is measured from now rather than from when
        # this turn was first created. Four agents reporting a long answer
        # trickle in over the whole time it takes to say it.
        session.turns.extend(last.turn_id)
        # EVERY AGENT HEARS THE CANDIDATE SEPARATELY, and they do not agree.
        #
        # Each Agora agent runs its own speech recognition over the same audio,
        # with its own endpointing, so four agents produce four different
        # transcripts of one sentence. A live four-persona run logged these
        # three within the same second:
        #
        #   behavioural     "Hello.  I'm not sure about that.  Obviously the few agents working at "
        #   hiring_manager  "Hello.  I'm not sure about that.  Obviously the few agents working at "
        #   technical       " Obviously the few agents working at SMI each own for each direction, "
        #
        # The old test asked whether the text was IDENTICAL or a growing
        # PREFIX. Priya's rendering is neither — it starts mid-sentence — so it
        # became a third candidate turn, and one answer was recorded as three.
        # Those phantom answers then tripped the one-utterance-per-answer
        # guard and silenced the rest of the panel for the turn.
        #
        # So within the window, anything arriving is the SAME utterance heard
        # by a different ear. Time is the reliable signal; the words are not.
        if _better(last.text, latest) and not _swallows_earlier(session, last, latest):
            session.turns.extend(last.turn_id, latest)
            # Let the floor holder answer the COMPLETED sentence. Without this
            # the panel replies to a fragment and then never speaks again.
            session.spoke_after_answers = -1
            log.info("turn %d: kept the fuller transcript (%d -> %d chars)",
                     last.turn_id, len(last.text), len(latest))
        return

    # Cut off whatever they already said. A persona that stays silent never has
    # its Agora history advanced, so its recogniser keeps accumulating: by the
    # fourth question its "latest" is EVERY answer so far, run together. When
    # that arrives outside the same-utterance window it becomes a new turn, and
    # the transcript then shows the whole interview replayed inside one answer —
    # which is what the marking engine scores and what the operator reads.
    latest = _without_recorded(session, latest)
    if not latest:
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







def _same_utterance(session: SessionState, last, latest: str) -> bool:
    """Is this another agent's rendering of the answer we already recorded?

    Two signals, and neither works alone — both were tried and both failed in
    a live run.

    NOBODY HAS REPLIED YET is the strong one. A candidate cannot produce a
    genuinely new answer until somebody has asked them something, so while the
    most recent turn is still theirs, anything arriving is the same speech
    reaching us through a different agent's recogniser. This holds however
    different the words are, which matters because the words really do differ.

    But it is not sufficient on its own: a slow agent's request can land after
    the floor holder has already replied, and treating that as new speech is
    what duplicated turns before. So when someone HAS replied, fall back to
    time plus overlap — recent, and recognisably the same sentence.
    """
    # Measured from when this utterance was LAST reported, not from when it was
    # first recorded. A thirty-second answer is reported repeatedly across
    # those thirty seconds as each agent's recogniser settles; measuring from
    # the start meant the window expired while the candidate was still talking,
    # and the final report — the fullest one — was filed as a second answer.
    # That is exactly what put the same reply under two interviewers.
    if time.time() - last.ended_at >= SAME_TURN_WINDOW:
        return False

    replied_since = any(
        t.turn_id > last.turn_id and not t.is_candidate
        for t in session.turns.all()
    )
    if not replied_since:
        return True
    return _overlaps(last.text, latest)


def _overlaps(a: str, b: str) -> bool:
    """Do these two look like transcripts of the same sentence?

    Word overlap rather than a prefix test. Agents disagree about where an
    utterance STARTS as well as where it ends — one begins "Hello. I'm not
    sure about that." and another begins mid-sentence with "Obviously the few
    agents..." — so a prefix comparison reports two renderings of one sentence
    as unrelated.
    """
    x = set(" ".join(a.split()).lower().split())
    y = set(" ".join(b.split()).lower().split())
    if not x or not y:
        return False
    return len(x & y) / min(len(x), len(y)) >= 0.5


def _without_recorded(session: SessionState, latest: str) -> str:
    """Drop any already-recorded answer from the front of this transcript.

    Compared word by word rather than by exact string: the accumulation is
    re-recognised each time, so "hamper much" comes back as "hamper. Much." and
    an exact substring test misses it entirely. Words survive that; punctuation
    and capitalisation do not.

    Longest recorded answer first, so the largest prefix is removed rather than
    the earliest one — otherwise stripping the first answer leaves the second
    and third still glued to the front.
    """
    def key(text: str) -> list:
        return [w.strip(".,!?;:").lower() for w in text.split() if w.strip(".,!?;:")]

    recorded = sorted(session.turns.by_speaker("candidate"),
                      key=lambda t: -len(t.text))
    original = latest
    stripped = []

    # REPEATEDLY, because the accumulation is every previous answer in a row.
    # Removing one prefix and stopping left the next one still glued to the
    # front — the transcript looked fixed for the second answer and wrong for
    # every answer after it.
    changed = True
    while changed:
        changed = False
        words = latest.split()
        plain = key(latest)

        for turn in recorded:
            earlier = key(turn.text)
            # Four words is enough to be a real clause — "I'm not sure about
            # that" is a whole answer a nervous candidate gives repeatedly.
            # Below that it is a word or two that could genuinely open two
            # different answers.
            if len(earlier) < 4 or len(earlier) >= len(plain):
                continue
            if plain[: len(earlier)] != earlier:
                continue

            # Map back to the original text by word count — normalisation only
            # touched punctuation, so the counts line up.
            kept, skipped = [], 0
            for word in words:
                if skipped < len(earlier) and word.strip(".,!?;:"):
                    skipped += 1
                    continue
                kept.append(word)
            trimmed = " ".join(kept).strip()
            if not trimmed:
                # Nothing left means they said the same thing again rather than
                # continuing — a real answer, and what a stuck or evasive
                # candidate does. Keep it rather than losing the turn.
                return original
            latest, changed = trimmed, True
            stripped.append(turn.turn_id)
            break

    if stripped:
        log.info("stripped turns %s from an accumulated transcript (%d -> %d chars)",
                 stripped, len(original), len(latest))
    return latest


def _swallows_earlier(session: SessionState, last, latest: str) -> bool:
    """Does this transcript contain answers we have already recorded?

    A persona that stays silent never has its Agora history advanced, so its
    recogniser keeps accumulating: by the fourth question its "latest user
    message" is every answer the candidate has given, run together. Because
    `_better` prefers the LONGEST rendering, that accumulation would replace
    the current answer with a concatenation of the whole interview — which is
    exactly what put previous answers inside the current one from Arjun's turn
    onward.

    Longest is the right rule for two renderings of ONE sentence. It is the
    wrong rule when the longer one is several sentences the candidate said at
    different times, and this is how the two are told apart.
    """
    normalised = " ".join(latest.split()).lower()
    for turn in session.turns.by_speaker("candidate"):
        if turn.turn_id == last.turn_id:
            continue
        earlier = " ".join(turn.text.split()).lower()
        # Long enough to be a real answer rather than "yes" appearing twice.
        if len(earlier) >= 40 and earlier in normalised:
            log.info("turn %d: ignoring a transcript that had swallowed turn %d",
                     last.turn_id, turn.turn_id)
            return True
    return False


def _better(recorded: str, latest: str) -> bool:
    """Is this rendering of the same utterance worth keeping over what we have?

    Longer wins, and by a clear margin rather than a character. Two things make
    a later arrival longer: the candidate carried on talking between calls, and
    one agent's recogniser simply caught more of the sentence than another's.
    Both are reasons to prefer it — the transcript is what everything else is
    marked against, and keeping whichever agent happened to arrive first meant
    sometimes keeping a fragment that started mid-sentence.

    The margin stops a one-word difference in punctuation or filler from
    rewriting the turn on every one of the four calls.
    """
    a = " ".join(recorded.split())
    b = " ".join(latest.split())
    return len(b) > len(a) + 8


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
