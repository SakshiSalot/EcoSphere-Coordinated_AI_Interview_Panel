"""Joining the marking engine to a live session.

Everything under `src/analysis` is deliberately ignorant of `SessionState` so
it stays pure and testable. This is the one file that is not: it reads the
ledgers, decides what to mark, and writes the results back. When the project
outgrows in-memory sessions, this is the only file that changes.

TWO RULES FOR CALLING IT FROM A WORKER

  * Never on the critical path. `mark_answer` makes a model call and takes
    seconds; putting it in `observe()` would stall every turn of the interview.
    Call it from a thread once the candidate's answer is recorded.
  * One write at the end. Each function computes everything outside shared
    state and finishes with a single append. The conversation holds
    `contract._TURN_LOCK` at unpredictable moments, and a read-then-write from
    a background thread is a race that will never reproduce when you look for
    it. `list.append` is atomic; `x = f(x)` is not.

Failure is always silent to the interview and loud in the log. An answer that
cannot be marked is simply not recorded, and `running_score` averages over the
answers that were — so one rate-limited turn costs the candidate nothing.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from src.analysis import allocation, judge, rubrics, scorer
from src.state.models import AnswerScore
from src.state.session import SessionState

log = logging.getLogger("analysis.pipeline")

# Two workers, deliberately. More does not help — the judge's rate limit is
# requests per minute, not concurrency — and a bounded pool means a slow or
# hanging model call can never accumulate threads across a long interview.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="marking")

# The off switch. `src.mock.offline` turns marking off the same way it stubs
# the providers: that suite's entire value is that it needs no keys and no
# network, and hooks firing real model calls from a regression test would
# quietly spend the day's quota every time anyone ran it.
#
# MARKING_ENABLED=0 does the same from the environment, for anyone who wants
# the conductor without the marks.
_enabled = os.getenv("MARKING_ENABLED", "1").strip() not in ("0", "false", "no")

_pending: list = []
_pending_lock = threading.Lock()


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = on


def enabled() -> bool:
    return _enabled and judge.available()

# How many answers one question thread may absorb before the next question
# starts a fresh one. The conductor rotates the floor after three turns
# anyway, so a persona holding it longer than that has moved on to new ground.
MAX_THREAD_ANSWERS = 3


def question_turn_for(session: SessionState, answer_turn_id: int) -> int | None:
    """The persona turn that prompted this answer.

    The same backwards walk `conductor._asker_of` already does. A rubric
    belongs to a question and a score belongs to an answer — this is the step
    that joins them.
    """
    for turn_id in range(answer_turn_id - 1, 0, -1):
        turn = session.turns.get(turn_id)
        if turn and not turn.is_candidate:
            return turn_id
    return None


def rubric_for(session: SessionState, question_turn_id: int) -> dict | None:
    """The marking scheme for one question, generated once and reused.

    Called when the question is ASKED, not when it is answered — during the
    ten to sixty seconds the candidate spends replying. A rubric written before
    the answer exists cannot be shaped by it, which is the difference between
    a defensible mark and one that flatters every candidate.

    Returns None when the turn is not a markable question, or when the model
    could not be reached. Both mean "record no score", never "score it zero".
    """
    existing = session.rubrics.get(question_turn_id)
    if existing is not None:
        return existing

    turn = session.turns.get(question_turn_id)
    if turn is None or turn.is_candidate:
        return None
    if not rubrics.is_markable_question(turn.text):
        log.debug("turn %d is not a question - no rubric", question_turn_id)
        return None

    # A follow-up shares its parent's rubric. That saves a model call and, more
    # importantly, is what makes the two answers split one question's marks
    # rather than counting as two questions.
    root = _thread_root(session, question_turn_id)
    if root is not None:
        shared = session.rubrics[root]
        session.rubrics[question_turn_id] = shared
        log.info("turn %d follows up on turn %d - reusing its rubric",
                 question_turn_id, root)
        return shared

    # The lead persona opens with the AI disclosure and its first question in
    # one breath. Marking against "Hello, and welcome. Before we begin..."
    # produces concepts about the greeting.
    asked = rubrics.strip_disclosure(turn.text)

    try:
        rubric = rubrics.generate_rubric(asked, turn.speaker, session.job)
    except (ValueError, judge.JudgeUnavailable) as exc:
        log.error("no rubric for turn %d: %s", question_turn_id, exc)
        return None

    # Stamped on the rubric rather than kept in a side table, so the thread a
    # follow-up belongs to travels with the thing it is marked against.
    rubric["question_turn"] = question_turn_id
    session.rubrics[question_turn_id] = rubric
    return rubric


def _thread_root(session: SessionState, question_turn_id: int) -> int | None:
    """The question this one is a follow-up to, if it genuinely is one.

    Two conditions, and the second one was learned the hard way. The same
    persona must have asked the previous question — and the conductor must
    have kept them there because the answer was VAGUE.

    The first condition alone is not enough. A technical interviewer holding
    the floor is usually drilling a chain of *different* sub-questions, not
    re-asking one: conditional expressions, then back-off strategy, then where
    the request id is stored. Treating those as one thread marks each answer
    against the first question's rubric, and a candidate who answers the third
    question well scores zero for it. That is far more damaging than the model
    call a reused rubric saves.

    A vague flag on the answer in between is the signal that the interviewer
    is asking the same thing again — it is the conductor's own rule 4, "two
    vague answers in a row: current interviewer stays and pins them down".
    """
    asking = session.turns.get(question_turn_id)
    if asking is None:
        return None

    for turn_id in range(question_turn_id - 1, 0, -1):
        previous = session.turns.get(turn_id)
        if previous is None or previous.is_candidate:
            continue
        if previous.speaker != asking.speaker:
            return None  # someone else took the floor: new ground

        if not _was_pinned_down(session, turn_id, question_turn_id):
            return None  # a new question from the same persona, not a re-ask

        rubric = session.rubrics.get(turn_id)
        if rubric is None:
            return None
        root = rubric.get("question_turn", turn_id)
        if _thread_size(session, root) >= MAX_THREAD_ANSWERS:
            return None
        return root

    return None


def _was_pinned_down(
    session: SessionState, previous_question: int, question_turn_id: int
) -> bool:
    """Was the answer between these two questions flagged vague?

    Reuses the flags the instant heuristics already raise, so this costs
    nothing and stays consistent with the floor-control decision the conductor
    made at the time.
    """
    vague = session.flags.turns_of_kind("vague")
    return any(
        turn_id in vague
        for turn_id in range(previous_question + 1, question_turn_id)
    )


def _thread_size(session: SessionState, root: int) -> int:
    return sum(1 for r in session.rubrics.values() if r.get("question_turn") == root)


def mark_answer(session: SessionState, answer_turn_id: int) -> AnswerScore | None:
    """Judge one candidate answer against its question's rubric, and record it.

    Safe to call from a background thread. Returns None when the answer could
    not be marked — deliberately, rather than a zero or a guess.
    """
    answer = session.turns.get(answer_turn_id)
    if answer is None or not answer.is_candidate:
        return None

    # Agora retries a request it thinks timed out, and the harness can ask for
    # a turn twice. Marking an answer twice would double its weight in the
    # allocation and could apply the same penalty again.
    if any(existing.turn_id == answer_turn_id for existing in session.scores):
        log.debug("turn %d already scored", answer_turn_id)
        return None

    question_turn_id = question_turn_for(session, answer_turn_id)
    if question_turn_id is None:
        return None

    rubric = rubric_for(session, question_turn_id)
    if rubric is None:
        return None

    question = session.turns.get(question_turn_id)
    try:
        score, evasion = scorer.judge_answer(
            rubrics.strip_disclosure(question.text) or question.text,
            answer.text,
            rubric,
            turn_id=answer_turn_id,
            role=question.speaker,
        )
    except (ValueError, judge.JudgeUnavailable) as exc:
        log.error("turn %d goes unscored: %s", answer_turn_id, exc)
        return None

    _record_evasion(session, answer_turn_id, evasion)

    # The deduction comes from shared state, not from a verdict the scorer
    # kept to itself: the conductor routes the floor on these same flags, so
    # marks and turn-taking can never disagree about whether this was a dodge.
    score.penalty = scorer.penalty_for(
        session.flags.for_turn(answer_turn_id), score.max_score
    )

    session.record_score(score)  # the single write; see the module docstring
    return score


def _record_evasion(session: SessionState, turn_id: int, evasion) -> None:
    """Put the judge's dodge finding where everything else reads flags.

    One flag per turn per kind. The instant heuristic runs inline so the
    conductor has a signal immediately; the judge arrives seconds later and is
    far better at it. They are two tiers of the same detector, not two
    detectors — so when the heuristic already flagged this turn, the judge
    confirms rather than duplicating.

    Disagreements are logged rather than resolved. The heuristic flagging what
    the judge clears is worth seeing: it is how you find out the word list is
    catching something it should not.
    """
    already = turn_id in session.flags.turns_of_kind("vague")

    if evasion is None:
        if already:
            log.info(
                "turn %d: heuristic flagged vague, the judge did not - "
                "keeping the flag, the conductor has already routed on it",
                turn_id,
            )
        return

    if already:
        log.info("turn %d: the judge confirms the heuristic's vague flag", turn_id)
        return

    session.flags.add(
        turn_id=turn_id,
        kind=evasion.kind,
        detail=evasion.detail,
        quote=evasion.quote,
        source=evasion.source,
    )
    log.info(
        "turn %d: judge raised a vague flag the heuristic missed - %s",
        turn_id, evasion.detail[:90],
    )


def finalise(
    session: SessionState,
    total: float = allocation.DEFAULT_TOTAL,
    weights: dict[str, float] | None = None,
) -> dict:
    """Total the interview: rubric-scale marks become interview marks.

    Runs once, after the last answer. Everything the allocation needs — how
    many questions each interviewer asked, which band each was pitched at,
    which answers belong to one thread — is only knowable now, which is why
    the deck says marks are "totalled just before the feedback step".

    Replaces `session.scores` with the allocated versions, so `running_score`
    reports the real figure afterwards rather than the live approximation.
    """
    difficulty_by_turn = {t.turn_id: t.difficulty for t in session.turns.all()}

    question_by_turn: dict[int, int] = {}
    for score in session.scores:
        asked_at = question_turn_for(session, score.turn_id)
        rubric = session.rubrics.get(asked_at) if asked_at else None
        question_by_turn[score.turn_id] = (rubric or {}).get(
            "question_turn", asked_at or score.turn_id
        )

    allocated = allocation.allocate(
        session.scores,
        difficulty_by_turn=difficulty_by_turn,
        question_by_turn=question_by_turn,
        total=total,
        weights=weights,
    )
    session.scores[:] = allocated

    return allocation.final_mark(allocated, total)


# --- the two hooks the live loop calls ----------------------------------
# Both return immediately. Neither may ever raise into the conversation: an
# interview that stops because the marking engine had a bad minute is a far
# worse failure than an interview with one unmarked answer.


def on_question_asked(session: SessionState, question_turn_id: int) -> None:
    """A persona has just spoken. Build its rubric while the candidate thinks.

    Call right after the persona's turn is recorded. The rubric is then ready
    before the answer arrives, and — the part that matters — it was written
    without sight of the answer, so it cannot have been shaped by it.
    """
    _submit("rubric", rubric_for, session, question_turn_id)


def on_answer_recorded(session: SessionState, answer_turn_id: int) -> None:
    """The candidate has just answered. Mark it, off the critical path.

    Call right after the candidate's turn is recorded — NOT inline. This makes
    a model call taking seconds, and the conversation is holding a lock.
    """
    _submit("marking", mark_answer, session, answer_turn_id)
    _submit("consistency", check_consistency, session, answer_turn_id)


# The most a single answer will be compared against. An interview of twenty
# answers all about latency would otherwise cost twenty judge calls on the last
# turn alone. Newest first, because a candidate who changes their story usually
# changes it from the version they gave a minute ago.
MAX_COMPARISONS = 2


class _Contradiction(BaseModel):
    """Whether two statements by the same person can both be true."""

    contradicts: bool
    quote_earlier: str
    quote_later: str
    why: str


def _check_against_resume(session: SessionState, turn, answer_turn_id: int) -> bool:
    """The qualitative half of the CV check. Returns True if it flagged.

    The inline detector catches an outright disowning — "I've never used
    Redis" against a CV that lists Redis. It cannot catch the softer and more
    common version: a CV claiming they LED the migration and an answer making
    clear they watched it. That needs reading, which is what this is for.

    Skipped when the answer is short. "Yes, that's right" cannot contradict a
    CV, and spending a model call to establish that on every acknowledgement
    would double the cost of the interview for nothing.
    """
    from src.analysis import claims as claim_check
    from src.conductor.turn import _already_flagged  # noqa: F401 — shared rule

    resume = (session.candidate.resume_text or "").strip()
    if not resume or len(turn.text.split()) < 12:
        return False

    # The deterministic tier already raised this turn: do not spend a call to
    # say the same thing twice, and do not let two tiers double-flag one answer.
    if any(f.kind == "contradiction" and f.turn_id == answer_turn_id
           for f in session.flags.all()):
        return False

    try:
        verdict = judge.structured(
            claim_check.resume_judge_prompt(resume, turn.text, answer_turn_id),
            _Contradiction,
            what=f"cv-consistency/turn-{answer_turn_id}",
            max_output_tokens=600,
        )
    except judge.JudgeUnavailable:
        return False

    if not verdict.contradicts:
        return False
    if not (claim_check.verified(verdict.quote_earlier, resume)
            and claim_check.verified(verdict.quote_later, turn.text)):
        log.warning("turn %d: CV contradiction discarded — could not be quoted",
                    answer_turn_id)
        return False

    session.flags.add(
        turn_id=answer_turn_id,
        kind="contradiction",
        detail=f"Against their CV: {verdict.why}",
        quote=verdict.quote_earlier,
        quote_b=verdict.quote_later,
        ref_turn_id=None,      # the other side is the CV, not a turn
        source="judge",
    )
    log.info("turn %d contradicts the CV: %s", answer_turn_id, verdict.why[:80])
    return True


def check_consistency(session: SessionState, answer_turn_id: int) -> None:
    """Contradictions tier two: the ones arithmetic cannot see.

    "I led that migration" against "I wasn't really involved in the migration"
    has no numbers in it, and no regular expression will ever catch it. This
    will, at the cost of a model call — so the call is only made for answers
    that touch a topic the candidate has already spoken about, which is both a
    cheap filter and the same narrowing that makes the judgement reliable.

    THE QUOTES ARE VERIFIED against the two turns before anything is recorded.
    A contradiction is the most damaging thing this panel can assert about
    somebody, and a model asked to quote will paraphrase; a flag citing words
    the candidate never said would be worse than missing the contradiction
    entirely.
    """
    from src.analysis import claims as claim_check
    from src.conductor.turn import _already_flagged

    turn = session.turns.get(answer_turn_id)
    if turn is None or not turn.is_candidate:
        return

    # The CV first. It is the claim the candidate made in writing, it is the
    # contradiction an interviewer most wants raised while they are still in
    # the room, and it needs no earlier answer to exist — so it is checkable
    # from the very first turn, when nothing else here is.
    if _check_against_resume(session, turn, answer_turn_id):
        return

    topics = {m.topic for m in claim_check.measurements(turn.text)}
    # No quantified topic is not a reason to skip: the qualitative
    # contradictions are exactly the ones with no numbers. Fall back to
    # comparing against the most recent substantial answers.
    earlier = [
        t for t in session.turns.by_speaker("candidate")
        if t.turn_id < answer_turn_id and len(t.text.split()) >= 12
    ]
    if topics:
        related = [
            t for t in earlier
            if topics & {m.topic for m in claim_check.measurements(t.text)}
        ]
        earlier = related or earlier

    for prior in list(reversed(earlier))[:MAX_COMPARISONS]:
        if _already_flagged(session, prior.turn_id, answer_turn_id):
            continue
        try:
            verdict = judge.structured(
                claim_check.judge_prompt(
                    prior.text, turn.text, prior.turn_id, answer_turn_id
                ),
                _Contradiction,
                what=f"consistency/turn-{answer_turn_id}",
                max_output_tokens=600,
            )
        except judge.JudgeUnavailable as exc:
            log.info("consistency check skipped for turn %d: %s",
                     answer_turn_id, exc)
            return

        if not verdict.contradicts:
            continue

        if not (claim_check.verified(verdict.quote_earlier, prior.text)
                and claim_check.verified(verdict.quote_later, turn.text)):
            log.warning(
                "turn %d: contradiction discarded — the judge could not quote it",
                answer_turn_id,
            )
            continue

        session.flags.add(
            turn_id=answer_turn_id,
            kind="contradiction",
            detail=verdict.why,
            quote=verdict.quote_earlier,
            quote_b=verdict.quote_later,
            ref_turn_id=prior.turn_id,
            source="judge",
        )
        log.info("turn %d contradicts turn %d (judge): %s",
                 answer_turn_id, prior.turn_id, verdict.why[:80])
        return


def _submit(what: str, fn, *args) -> None:
    if not enabled():
        return
    future = _POOL.submit(_safely, what, fn, *args)
    with _pending_lock:
        _pending.append(future)


def _safely(what: str, fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 — nothing here may reach the interview
        log.exception("%s failed in the background", what)


def drain(timeout: float = 180.0) -> None:
    """Wait for background marking to finish.

    For the harness and for the end of an interview, before the report is
    built. Never call it on the live path — the whole point of the background
    lane is that nothing waits for it.
    """
    with _pending_lock:
        waiting, _pending[:] = list(_pending), []
    for future in waiting:
        try:
            future.result(timeout=timeout)
        except Exception:  # noqa: BLE001 — already logged in _safely
            pass


def evidence_for(session: SessionState) -> list[tuple[int, str, str, str]]:
    """Every awarded concept with the words that earned it.

    (turn_id, role, concept, quote). This is what the cited report renders:
    the quotes were verified against the transcript at scoring time, so no
    sentence built from this list can cite something the candidate did not say.
    """
    out = []
    for score in session.scores:
        for hit in score.concepts:
            if hit.covered and hit.quote:
                out.append((score.turn_id, score.role, hit.concept, hit.quote))
    return out
