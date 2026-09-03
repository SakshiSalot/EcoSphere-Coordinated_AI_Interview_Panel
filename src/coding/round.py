"""The coding round, as a thing a candidate can leave and come back to.

WHY THIS DOES NOT USE SessionState. The conversation lives in memory because
it lasts twenty minutes and dies with the call. This round does not: a
candidate may take it the next morning, from a different machine, after the
gateway has restarted twice. So every byte of it — the question, the code as
typed, the test results, the mark — is written to the database as it happens.

The mark is deliberately not "did the tests pass". A candidate who reasons
aloud through a correct approach and fumbles an index deserves more than a
candidate who pastes something that happens to pass. Tests are two thirds;
the last third is the interviewer's read of how they worked, which is why the
transcript of the round is kept alongside the code.
"""

import logging
import time

from src.coding import question as question_gen
from src.coding import sandbox
from src.state import db
from src.state.models import CandidateProfile, JobSpec

log = logging.getLogger("coding.round")

TESTS_WEIGHT = 0.67

# The same bands the spoken round uses, so a hard coding problem and a hard
# spoken question are worth the same relative to an easy one.
DIFFICULTY_MULTIPLIER = {"easy": 0.8, "medium": 1.0, "hard": 1.25}


def prepare(session_id: str, job_row, resume_text: str, language: str = "python") -> dict:
    """Write the question for this candidate, once.

    Called when the operator sets the interview up, not when the candidate
    opens it — generating takes several seconds and nobody should watch a
    spinner before they can read their problem.
    """
    existing = db.load_coding(session_id)
    if existing.get("question"):
        return existing["question"]

    q = question_gen.generate(
        JobSpec(title=job_row["title"], description=job_row["description"]),
        CandidateProfile(resume_text=resume_text),
        language=language,
    )
    db.save_coding(session_id, {
        "question": q,
        "source": q["starter"],
        "runs": [],
        "submitted_at": None,
        "result": None,
    })
    log.info("coding question ready for %s: %s", session_id, q["title"])
    return q


def state(session_id: str, *, for_candidate: bool = True) -> dict:
    """What the editor renders. The candidate sees the question, their code and
    their own runs — never the mark."""
    saved = db.load_coding(session_id)
    if not saved.get("question"):
        return {"ready": False}

    q = saved["question"]
    view = {
        "ready": True,
        "title": q["title"],
        "prompt": q["prompt"],
        "grounded_in": q["grounded_in"],
        "language": q["language"],
        "source": saved.get("source") or q["starter"],
        "tests": [{"stdin": t["stdin"], "expected": t["expected"], "why": t["why"]}
                  for t in q["tests"]],
        "submitted": saved.get("submitted_at") is not None,
        "runs": len(saved.get("runs", [])),
    }
    if not for_candidate:
        view["talking_points"] = q.get("talking_points", [])
        view["result"] = saved.get("result")
    return view


def save_source(session_id: str, source: str) -> None:
    """Keep what they have typed. Called as they work, so closing the tab
    loses nothing."""
    saved = db.load_coding(session_id)
    if not saved:
        return
    saved["source"] = source[: sandbox.MAX_SOURCE]
    db.save_coding(session_id, saved)


def run(session_id: str, source: str, stdin: str = "") -> dict:
    """Run once against one input. Does not submit and does not score."""
    saved = db.load_coding(session_id)
    if not saved.get("question"):
        raise sandbox.SandboxError("this interview has no coding round")
    if saved.get("submitted_at"):
        raise sandbox.SandboxError("this round has already been submitted")

    language = saved["question"]["language"]
    out = sandbox.run(source, language, stdin)

    saved["source"] = source[: sandbox.MAX_SOURCE]
    saved.setdefault("runs", []).append({
        "at": time.time(), "verdict": out["verdict"], "ok": out["ok"],
    })
    db.save_coding(session_id, saved)
    return out


def submit(session_id: str, source: str) -> dict:
    """Run every test, mark the round, and close it.

    Idempotent by refusal rather than by recomputation: a second submission
    would let a candidate keep trying until the sandbox happened to agree with
    them.
    """
    saved = db.load_coding(session_id)
    if not saved.get("question"):
        raise sandbox.SandboxError("this interview has no coding round")
    if saved.get("submitted_at"):
        return saved["result"]

    q = saved["question"]
    checked = sandbox.check(source, q["language"], q["tests"])
    fraction = checked["passed"] / max(1, checked["total"])

    # Every candidate for one opening gets a DIFFERENT generated problem, so a
    # raw pass rate is not comparable between them: whoever drew the harder
    # question is punished for the draw. Scaled by the same multipliers the
    # spoken round uses, so both halves of the interview mean the same thing.
    band = q.get("difficulty", "medium")
    multiplier = DIFFICULTY_MULTIPLIER.get(band, 1.0)
    scaled = min(1.0, fraction * multiplier)

    result = {
        "passed": checked["passed"],
        "total": checked["total"],
        "results": checked["results"],
        "tests_fraction": round(fraction, 3),
        "difficulty": band,
        "difficulty_multiplier": multiplier,
        # Only the testable part is known now. The interviewer's read of how
        # they worked is folded in when the whole interview is totalled, which
        # is why this is not the final number.
        "score": round(scaled * TESTS_WEIGHT, 3),
        "weight_note": (
            f"{checked['passed']} of {checked['total']} tests on a {band} "
            f"problem (x{multiplier}). Tests are {int(TESTS_WEIGHT * 100)}% of "
            f"this round; the rest is how they reasoned while writing it."
        ),
    }

    saved["source"] = source[: sandbox.MAX_SOURCE]
    saved["submitted_at"] = time.time()
    saved["result"] = result
    db.save_coding(session_id, saved)
    db.set_round_score(session_id, "coding", result["score"])

    log.info("coding submitted for %s: %d/%d tests",
             session_id, checked["passed"], checked["total"])
    return result
