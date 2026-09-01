"""How many marks each question was worth.

The other half of the scoring model. `scorer.py` answers "how much of a good
answer did they give?" — a fraction, and it depends on the answer. This file
answers "how many marks was that question worth?" — which depends only on the
shape of the interview, and is therefore pure arithmetic with no model call, no
key, no cost and no non-determinism.

    marks earned = coverage x marks available - penalty

WHY THIS RUNS AT THE END. "Each interviewer's share is divided evenly across
the questions they ask" needs a denominator that does not exist until the
interview is over: the conductor decides who speaks turn by turn, so Priya may
ask five questions or two. The deck says as much in its own words — marks
"are totalled just before the feedback step". Until then every AnswerScore
stays on its rubric's ten-point scale, which is all `running_score` needs to
steer the difficulty ladder.

TWO NUMBERS, TWO PURPOSES. They are not the same figure and should not be
conflated:

  * `SessionState.running_score` = earned / available. "How are they doing on
    what they have actually been asked?" Live, drives the ladder.
  * `final_mark()` = earned / nominal. "How did they do against the standard?"
    Goes in the report.

The difference is what makes difficulty weighting mean anything — see
`_difficulty_scaled` below.
"""

import logging
from dataclasses import replace

from src.state.models import AnswerScore

log = logging.getLogger("analysis.allocation")

# The scale a candidate is reported out of. Arbitrary but fixed: every
# candidate is measured against the same nominal total regardless of how many
# questions they were asked, which is what keeps a six-question interview
# comparable with a twenty-question one.
DEFAULT_TOTAL = 100.0

# Deck rule 1: equal to start, adjustable per job. Absent roles are dropped
# rather than defaulted, so a two-persona panel does not silently lose a third
# of the available marks to an interviewer who never spoke.
DEFAULT_WEIGHTS = {"technical": 1.0, "product": 1.0, "behavioural": 1.0}

# Deck rule 5. Reuses the bands the conductor already tracks on every Turn.
DIFFICULTY_MULTIPLIER = {"easy": 0.8, "medium": 1.0, "hard": 1.25}
DEFAULT_BAND = "medium"

# Deck rule 4: "3 for the main answer and 2 for the follow-up". Any further
# follow-ups divide the remainder between them.
MAIN_SHARE = 0.6


def category_shares(
    roles: list[str],
    total: float = DEFAULT_TOTAL,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Deck rule 1 — split the total across the interviewers who actually spoke.

    Normalised over the roles present rather than over all configured roles.
    A panel run at PANEL_SIZE=2, or an interview where the behavioural
    interviewer never got the floor, would otherwise leave a third of the marks
    unawardable and cap every candidate at 67%.
    """
    weights = weights or DEFAULT_WEIGHTS
    present = {r: max(0.0, weights.get(r, 1.0)) for r in dict.fromkeys(roles)}
    divisor = sum(present.values())

    if divisor <= 0:
        log.error("every category weight is zero - splitting the total evenly")
        even = total / len(present) if present else 0.0
        return {r: even for r in present}

    return {r: total * w / divisor for r, w in present.items()}


def allocate(
    scores: list[AnswerScore],
    *,
    difficulty_by_turn: dict[int, str] | None = None,
    question_by_turn: dict[int, int] | None = None,
    total: float = DEFAULT_TOTAL,
    weights: dict[str, float] | None = None,
) -> list[AnswerScore]:
    """Rescale every answer from its rubric's scale onto the interview's.

    Args:
        scores: what `scorer.score_answer` produced, on the rubric's ten-point
            scale. Not mutated — new objects come back, so a caller can hold
            both the raw marking and the allocated result.
        difficulty_by_turn: answer turn_id -> "easy" | "medium" | "hard". Built
            from the TurnLedger, which already records the band on every turn.
            Missing entries are treated as medium.
        question_by_turn: answer turn_id -> the turn_id of the question it
            answers. This is what groups a follow-up with its parent so the two
            split one question's marks instead of counting as two. Answers with
            no entry stand alone, which is the safe reading.
        total: the nominal scale. Every candidate is reported out of this.
        weights: per-role importance for this job. Equal by default.

    Returns:
        New AnswerScores whose `score`, `max_score` and `penalty` are in
        interview marks. `normalised` is unchanged for each answer — this
        redistributes weight between answers, it never re-judges one.
    """
    if not scores:
        return []

    difficulty_by_turn = difficulty_by_turn or {}
    question_by_turn = question_by_turn or {}

    shares = category_shares([s.role for s in scores], total, weights)

    # role -> question -> the answers to it, in order. dict preserves insertion
    # order, so the first answer to a question is the main one.
    by_role: dict[str, dict[int, list[AnswerScore]]] = {}
    for score in scores:
        question = question_by_turn.get(score.turn_id, score.turn_id)
        by_role.setdefault(score.role, {}).setdefault(question, []).append(score)

    allocated: list[AnswerScore] = []

    for role, questions in by_role.items():
        # Deck rule 3: an interviewer's share, divided evenly across the
        # questions they asked. Follow-ups do not count as extra questions.
        per_question = shares.get(role, 0.0) / len(questions)

        for question_turn, answers in questions.items():
            band = difficulty_by_turn.get(question_turn) or _band_of(answers, difficulty_by_turn)
            available = _difficulty_scaled(per_question, band)

            for portion, answer in zip(_follow_up_split(len(answers)), answers):
                allocated.append(_rescaled(answer, available * portion))

    log.info(
        "allocated %.0f nominal marks over %d answers across %s",
        total, len(allocated), ", ".join(f"{r} {s:.1f}" for r, s in shares.items()),
    )
    return allocated


def _band_of(answers: list[AnswerScore], difficulty_by_turn: dict[int, str]) -> str:
    """The difficulty of a question, when the question's own turn is unknown.

    Falls back to the band recorded against its first answer — the ledger
    stamps every turn with the level in force when it happened, so an answer's
    band is the band its question was asked at.
    """
    for answer in answers:
        band = difficulty_by_turn.get(answer.turn_id)
        if band:
            return band
    return DEFAULT_BAND


def _difficulty_scaled(marks: float, band: str) -> float:
    """Deck rule 5, applied WITHOUT renormalising back to the total.

    That is the whole point, and it is easy to get wrong. Scaling the bands and
    then rescaling the role back to its original share makes difficulty a no-op
    whenever a candidate's questions all sit in one band: a uniformly hard
    interview would total exactly what a uniformly easy one does, and "reaching
    and clearing harder questions scores higher" would be false.

    Leaving it unnormalised means marks available run roughly 80 to 125 against
    a nominal 100, and `final_mark` divides by the nominal. So a candidate who
    was pushed to hard questions and covered 80% of them reaches full nominal
    marks, while the same 80% on easy questions reaches 64%. Which is the
    intent: the ladder only drops to easy when the candidate is struggling, so
    a lower ceiling there is the difficulty system doing its job rather than a
    penalty applied twice.
    """
    multiplier = DIFFICULTY_MULTIPLIER.get(band, DIFFICULTY_MULTIPLIER[DEFAULT_BAND])
    return marks * multiplier


def _follow_up_split(count: int) -> list[float]:
    """Deck rule 4 — one question's marks shared between its answers.

    The main answer keeps the larger portion because it is where the candidate
    had the floor unprompted; a follow-up is the interviewer supplying the
    structure the answer was missing.
    """
    if count <= 1:
        return [1.0]
    rest = (1.0 - MAIN_SHARE) / (count - 1)
    return [MAIN_SHARE] + [rest] * (count - 1)


def _rescaled(answer: AnswerScore, available: float) -> AnswerScore:
    """Move one answer onto the interview's scale, preserving its judgement.

    Coverage and the penalty are converted as proportions rather than copied,
    so `normalised` comes out identical. Allocation decides what an answer was
    worth; it must never change how well the answer was judged.
    """
    if answer.max_score <= 0:
        return replace(answer, score=0.0, max_score=available, penalty=0.0)

    coverage = answer.score / answer.max_score
    penalty_fraction = answer.penalty / answer.max_score

    return replace(
        answer,
        score=round(coverage * available, 3),
        max_score=round(available, 3),
        penalty=round(penalty_fraction * available, 3),
    )


def final_mark(
    allocated: list[AnswerScore], total: float = DEFAULT_TOTAL
) -> dict:
    """The figure that goes in the report.

    Divides by the NOMINAL total, not by the marks that happened to be
    available — that is what lets a candidate who reached hard questions score
    higher than one who only ever saw easy ones. Capped at the total, because a
    hard-question multiplier should let a candidate reach full marks, not
    exceed them.

    Returns a breakdown rather than a bare number: a single figure in a hiring
    report that cannot be taken apart is not a defensible assessment.
    """
    earned = sum(max(0.0, s.score - s.penalty) for s in allocated)
    available = sum(s.max_score for s in allocated)
    capped = min(earned, total)

    per_role: dict[str, dict[str, float]] = {}
    for score in allocated:
        bucket = per_role.setdefault(score.role, {"earned": 0.0, "available": 0.0})
        bucket["earned"] += max(0.0, score.score - score.penalty)
        bucket["available"] += score.max_score

    for bucket in per_role.values():
        bucket["earned"] = round(bucket["earned"], 2)
        bucket["available"] = round(bucket["available"], 2)
        bucket["fraction"] = round(
            bucket["earned"] / bucket["available"] if bucket["available"] else 0.0, 3
        )

    return {
        "earned": round(capped, 2),
        "total": total,
        "fraction": round(capped / total, 3) if total else 0.0,
        # Kept separate and reported: "out of 100, on questions worth 118"
        # is the sentence that explains a difficulty-weighted mark to a
        # candidate, and hiding it makes the number look arbitrary.
        "available": round(available, 2),
        "answers": len(allocated),
        "by_role": per_role,
        "penalties": round(sum(s.penalty for s in allocated), 2),
    }
