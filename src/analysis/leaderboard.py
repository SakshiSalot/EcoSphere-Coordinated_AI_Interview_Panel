"""Ranking the candidates for one opening.

The point of grouping interviews under a job is that they become comparable.
Two things have to be true for that to mean anything, and both are decisions
rather than arithmetic:

  * EVERY CANDIDATE IS MEASURED AGAINST THE SAME ADVERT. The job description
    lives on the job, not on the interview, so nobody is scored against
    slightly different requirements.

  * A RANK IS NOT A VERDICT. This orders candidates; it does not decide
    anything. The operator sees the number, the evidence behind it, and the
    flags — and makes the call. That is the whole argument of the product, and
    a leaderboard is exactly the screen where it is easiest to forget.

TWO KINDS OF NORMALISATION, and they are not the same argument.

  * AGAINST THE INSTRUMENT — necessary, and done. Two candidates sit different
    interviews: different lengths, different difficulty, different generated
    coding problems. Leaving that uncompensated means ranking the luck of the
    draw. `allocation.py` handles the spoken round (every mark is a proportion
    of one fixed nominal, and difficulty bands scale marks without being
    renormalised away), and `coding/round.py` scales the coding score by the
    problem's own rated difficulty using the same multipliers.

  * AGAINST THE COHORT — rejected. A z-score makes a candidate's mark depend on
    who else happened to apply that week, so identical performance ranks
    differently in a strong field. For an exam grading on a curve that is
    defensible; for deciding whether to hire one person it is not. Scores stay
    absolute, and the cohort spread is shown to the operator as context rather
    than folded into anyone's number.

What cannot be fully compensated is rubric strictness: each question gets its
own generated marking scheme, and some are harder to satisfy than others. That
is why the basis — how many answers, how hard it got — sits next to every
score, and why a thin interview is called out rather than quietly ranked.
"""

import json
import logging

log = logging.getLogger("leaderboard")

# Below this many answers, a percentage is a small sample rather than a
# measurement. Three answers out of 100 marks swings enormously on one bad
# turn; nine does not. The number is still shown — hiding it would be worse —
# but it is shown with a warning, because two candidates on 62% are not
# necessarily equivalent when one of them was asked twice as much.
RELIABLE_ANSWERS = 4

STAGE_LABEL = {
    "invited": "Not started",
    "voice_done": "Conversation done",
    "coding_done": "Coding done",
    "complete": "Complete",
}


def combined(voice: float | None, coding: float | None, voice_weight: float,
             coding_enabled: bool) -> tuple[float | None, str]:
    """One number per candidate, and an honest note about what is in it.

    A missing round is NOT counted as zero. A candidate who has not sat the
    coding exercise yet has not failed it, and ranking them below someone who
    has would be a scoring artefact rather than a judgement.
    """
    if not coding_enabled:
        return (voice, "conversation only") if voice is not None else (None, "not started")

    if voice is not None and coding is not None:
        return (
            round(voice * voice_weight + coding * (1 - voice_weight), 3),
            f"conversation {int(voice_weight * 100)}% · coding "
            f"{int((1 - voice_weight) * 100)}%",
        )
    if voice is not None:
        return voice, "conversation only — coding round outstanding"
    if coding is not None:
        return coding, "coding only — conversation outstanding"
    return None, "not started"


def _basis(row) -> dict:
    """What the score was actually measured on.

    The marking already compensates for both things that vary between two
    candidates' interviews — length, because every mark is a proportion of a
    fixed nominal, and difficulty, because bands scale marks without being
    renormalised away. But a leaderboard that shows only the result hides that
    work, and a number with no visible basis looks arbitrary whether or not it
    is. So the basis goes on the screen.
    """
    if not row["assessment_json"]:
        return {}
    try:
        a = json.loads(row["assessment_json"])
    except json.JSONDecodeError:
        return {}

    answers = a.get("answers") or 0
    available = a.get("available") or 0.0
    nominal = a.get("total") or 100.0

    # Available marks run above the nominal when the ladder pushed the
    # candidate into harder questions, and below when it dropped to easy.
    ratio = (available / nominal) if nominal else 1.0
    reached = "hard" if ratio > 1.08 else "easy" if ratio < 0.92 else "medium"

    return {
        "answers": answers,
        "difficulty_reached": reached,
        "marks_available": round(available, 1),
        "reliable": answers >= RELIABLE_ANSWERS,
    }


def build(job_row, interview_rows) -> dict:
    """The table an operator reads, sorted best first.

    Candidates who have not finished sit at the bottom regardless of a partial
    score, because a half-finished interview is not evidence of anything and
    putting it above a completed one invites exactly the wrong comparison.
    """
    coding_enabled = bool(job_row["coding_enabled"])
    voice_weight = float(job_row["voice_weight"])

    rows = []
    for r in interview_rows:
        score, basis = combined(
            r["voice_score"], r["coding_score"], voice_weight, coding_enabled
        )
        complete = r["stage"] == "complete"
        basis_detail = _basis(r)
        rows.append({
            "session_id": r["session_id"],
            "candidate": r["candidate_name"] or "(unclaimed)",
            "claimed": r["candidate_id"] is not None,
            "invite_code": r["invite_code"],
            "stage": r["stage"],
            "stage_label": STAGE_LABEL.get(r["stage"], r["stage"]),
            "voice_score": r["voice_score"],
            "coding_score": r["coding_score"],
            "score": score,
            "basis": basis,
            "complete": complete,
            "decision": r["decision"],
            **basis_detail,
        })

    rows.sort(key=lambda x: (x["complete"], x["score"] if x["score"] is not None else -1),
              reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank"] = i if row["score"] is not None else None

    scored = [r["score"] for r in rows if r["score"] is not None and r["complete"]]
    spread = None
    if len(scored) >= 2:
        spread = {
            "best": max(scored),
            "worst": min(scored),
            "median": sorted(scored)[len(scored) // 2],
            "n": len(scored),
        }

    thin = [r["candidate"] for r in rows
            if r.get("answers") is not None and r.get("reliable") is False]

    return {
        "job_id": job_row["job_id"],
        "title": job_row["title"],
        "closed": bool(job_row["closed"]),
        "coding_enabled": coding_enabled,
        "voice_weight": voice_weight,
        "candidates": rows,
        "completed": sum(1 for r in rows if r["complete"]),
        "spread": spread,
        # Said on the screen, not just in the code: a ranked list is the place
        # a human most easily stops reading the evidence.
        "thin_evidence": thin,
        "note": "Ranked by score. Marks already account for how long each "
                "interview ran and how hard it got — every score is a "
                "proportion of the same nominal total, and harder questions "
                "carry more marks. The decision is yours: open a candidate to "
                "read the evidence behind their number.",
        "caution": (
            f"{', '.join(thin)} answered fewer than {RELIABLE_ANSWERS} "
            f"questions. Their percentage is a small sample, not a "
            f"measurement — read the transcript before comparing them."
            if thin else None
        ),
    }
