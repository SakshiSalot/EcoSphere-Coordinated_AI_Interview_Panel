"""The coding question, written for this candidate.

WHY NOT A QUESTION API. The obvious move is to pull a problem from a public
set, and it is the wrong one here. Those problems are the ones every candidate
has already memorised, they have nothing to do with the job advert, and the
whole argument of this product is that the interview is about *this* role and
*this* person. A generated question grounded in their own CV cannot be
recited from memory, and it lets the technical interviewer say "you mentioned
you built a retry layer — write me the core of it".

Generated once, at setup, alongside the spoken question plan: the candidate
must never sit watching a spinner while a model writes their problem.

Tests are visible on purpose. This is a conversation, not a submission portal —
the interviewer watches the candidate work, and hidden tests would turn it into
a guessing game with an audience.
"""

import logging

from src.coding import sandbox
from src.gateway import gemini
from src.state.models import CandidateProfile, JobSpec

log = logging.getLogger("coding.question")

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "prompt": {
            "type": "string",
            "description": "The problem, in 3-5 sentences. Plain prose, no "
                           "markdown headings, no code blocks.",
        },
        "grounded_in": {
            "type": "string",
            "description": "The resume line or job requirement this comes from.",
        },
        "difficulty": {
            "type": "string",
            "enum": ["easy", "medium", "hard"],
            "description": "How demanding this problem is for the level the "
                           "advert describes — not in the abstract.",
        },
        "signature": {
            "type": "string",
            "description": "Starter code: the function stub the candidate "
                           "fills in, plus reading input and printing the "
                           "result, so their answer runs as-is.",
        },
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stdin": {"type": "string"},
                    "expected": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["stdin", "expected", "why"],
            },
        },
        "talking_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What the interviewer should press on while they "
                           "write: complexity, an edge case, a trade-off.",
        },
    },
    "required": ["title", "prompt", "grounded_in", "difficulty", "signature",
                 "tests", "talking_points"],
}


def _prompt(job: JobSpec, candidate: CandidateProfile, language: str) -> str:
    return f"""Write ONE short coding problem for a live voice interview.

THE ROLE
{job.brief(1200) or "(not supplied)"}

THE CANDIDATE'S BACKGROUND
{candidate.resume_text.strip()[:1800] or "(not supplied)"}

RULES
1. GROUND IT in something they claim to have built, or something the advert
   requires. Name it in `grounded_in`. If they built a retry layer, the problem
   is about retries — not about reversing a linked list.
2. SOLVABLE IN TEN TO FIFTEEN MINUTES while talking out loud. One clear idea.
   This is a conversation, not a contest.
3. NO PUZZLE TRICKS. Nothing that turns on spotting one clever observation.
   The interviewer is judging how they think and explain, not whether they have
   seen this exact problem before.
4. The `signature` must be COMPLETE, RUNNABLE {language} — read from stdin,
   call the function, print the result. The candidate fills in the body only.
   The stub must RETURN A PLACEHOLDER OF THE RIGHT TYPE (0, 0.0, [], "") rather
   than nothing, so that pressing Run before writing anything prints a wrong
   answer instead of a stack trace. A candidate whose first action produces a
   traceback assumes the tooling is broken.
5. Three or four tests. Simple stdin, exact expected stdout. Include one edge
   case and say in `why` what each is for.
6. `talking_points`: three things to press on while they write — the
   complexity, an edge case they may miss, a trade-off worth defending.
7. RATE IT HONESTLY in `difficulty`, for the level this advert describes.
   Every candidate for one opening gets a different generated problem, so this
   rating is what stops the one who happened to be handed the harder problem
   being punished for it. Overrating a simple problem inflates their mark;
   underrating a demanding one costs them.

Language: {language}."""


def generate(
    job: JobSpec,
    candidate: CandidateProfile,
    language: str = "python",
    attempts: int = 2,
) -> dict:
    """Returns the question, or raises.

    The starter code is RUN before the question is handed out. A stub that
    crashes on the first press of Run reads as broken tooling, and the
    candidate has not written a line yet — so a question whose starter does not
    execute is regenerated rather than shipped.

    The caller decides what to do about total failure; silently substituting a
    generic puzzle would undo the one thing that makes this round worth having.
    """
    last_error = ""
    for attempt in range(1, attempts + 1):
        prompt = _prompt(job, candidate, language)
        if last_error:
            prompt += (
                f"\n\nYour previous attempt's starter code crashed when run "
                f"unchanged:\n{last_error}\nReturn a placeholder of the right "
                f"type from the stub so it runs cleanly."
            )
        question = _build(gemini.structured(prompt, SCHEMA, temperature=0.6),
                          language)

        try:
            out = sandbox.run(question["starter"], language,
                              question["tests"][0]["stdin"])
        except sandbox.SandboxError as exc:
            # The sandbox being down is not the question's fault. Ship it.
            log.warning("could not verify starter code (%s) — shipping anyway", exc)
            return question

        if out["ok"]:
            log.info("starter code verified on attempt %d", attempt)
            return question

        last_error = (out["stderr"] or out["verdict"])[:400]
        log.warning("attempt %d: starter code does not run — %s",
                    attempt, last_error.splitlines()[-1][:90] if last_error else "?")

    log.warning("shipping a question whose starter code still fails to run")
    return question


def _build(data: dict, language: str) -> dict:

    tests = [
        {"stdin": str(t.get("stdin", "")), "expected": str(t.get("expected", "")),
         "why": t.get("why", "")}
        for t in data.get("tests", [])
        if str(t.get("expected", "")).strip()
    ]
    if not tests:
        raise RuntimeError("the generated question had no usable tests")

    question = {
        "title": data.get("title", "Coding exercise").strip(),
        "prompt": data.get("prompt", "").strip(),
        "grounded_in": data.get("grounded_in", "").strip(),
        "difficulty": data.get("difficulty", "medium"),
        "language": language,
        "starter": data.get("signature", "").rstrip(),
        "tests": tests,
        "talking_points": data.get("talking_points", []),
    }
    return question
