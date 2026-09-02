"""Turning a real job advert and a real resume into a question plan.

This is what makes the interview about *this* role and *this* person rather
than a generic backend quiz. Without it the panel improvises: plausible
questions that would be asked of anyone, which is exactly what a candidate
notices and a judge discounts.

The plan is built once, at session start. Generating questions mid-turn would
add seconds to a live conversation, and conversational quality is scored.

The plan steers COVERAGE, not phrasing. Each persona receives its next
planned question as a suggestion; how it asks, and what it follows up with,
stays with the model. That division is deliberate — a script would remove the
adaptiveness the problem statement is asking for.
"""

import logging

from src.conductor.personas import active_roles, concern, persona
from src.gateway import gemini
from src.state.models import CandidateProfile, JobSpec, PlannedQuestion
from src.state.session import SessionState

log = logging.getLogger("intake")

def _schema(roles: list[str]) -> dict:
    """Built per call, because the role enum has to match this panel — the
    model must not be able to assign a question to somebody who is not in the
    room."""
    return {
        "type": "object",
        "properties": {
            "must_haves": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The role's hard requirements, from the advert.",
            },
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete things this candidate claims to have done.",
            },
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "enum": roles},
                        "text": {"type": "string"},
                        "difficulty": {
                            "type": "string",
                            "enum": ["easy", "medium", "hard"],
                        },
                        "topic": {"type": "string"},
                        "grounded_in": {
                            "type": "string",
                            "description": "The exact resume line or job "
                            "requirement this question comes from.",
                        },
                    },
                    "required": ["role", "text", "difficulty", "topic", "grounded_in"],
                },
            },
        },
        "required": ["must_haves", "highlights", "questions"],
    }


def _prompt(job: JobSpec, candidate: CandidateProfile, per_role: int,
            roles: list[str]) -> str:
    return f"""You are designing an interview plan for a live voice panel.

THE ROLE
{job.brief(2000) or "(not supplied)"}

THE CANDIDATE'S BACKGROUND
{candidate.resume_text.strip()[:2500] or "(not supplied)"}

THE PANEL — {per_role} questions each:
""" + "\n".join(
        f"- {r} ({persona(r)['name']}, {persona(r)['title']}): cares about {concern(r)}"
        for r in roles
    ) + f"""

RULES
1. Every question must be GROUNDED. Name a specific system, technology, number
   or claim from the candidate's background, or a specific requirement from the
   advert. Put that source in `grounded_in`.
   Good: "You moved to per-tenant sharding — how did you choose the shard key?"
   Bad:  "How would you scale a database?"
2. ONE question each, and it must be answerable out loud in under a minute.
   One sentence, under 25 words. Never join two questions with "and" — a
   compound question is unanswerable on a call, because the candidate can only
   hold the last clause in their head.
   Good: "How did you choose the shard key?"
   Bad:  "How did you choose the shard key, what happened when a tenant
          outgrew a shard, and how did you detect it?"
3. Spread difficulty: roughly one easy, {max(1, per_role - 2)} medium, one hard
   per interviewer, so the panel has somewhere to go in both directions.
4. The behavioural questions must still connect to this candidate's actual
   history — a specific project or decision they mention, not a generic prompt.
5. Do not ask two interviewers the same question from different angles.
6. If the advert requires something the resume never evidences, write one
   question that probes that gap directly. That is the most valuable question
   on the list.

Return {per_role * len(roles)} questions in total."""


def build_plan(
    job: JobSpec,
    candidate: CandidateProfile,
    per_role: int = 4,
    roles: list[str] | None = None,
) -> tuple[list[PlannedQuestion], list[str], list[str]]:
    """Returns (questions, must_haves, highlights).

    Raises if Gemini cannot produce a plan — the caller decides whether to
    fall back to the generic question bank, because that decision needs to be
    visible rather than silent.
    """
    roles = roles or active_roles()
    data = gemini.structured(_prompt(job, candidate, per_role, roles), _schema(roles))

    questions: list[PlannedQuestion] = []
    for q in data.get("questions", []):
        role = q.get("role")
        text = (q.get("text") or "").strip()
        if role not in roles or not text:
            continue
        questions.append(
            PlannedQuestion(
                role=role,
                text=text,
                difficulty=q.get("difficulty", "medium"),
                topic=(q.get("topic") or "").strip().lower(),
            )
        )

    # Interleave so each persona has something ready whenever the floor moves,
    # rather than four technical questions followed by four product ones.
    ordered: list[PlannedQuestion] = []
    by_role = {r: [q for q in questions if q.role == r] for r in roles}
    for i in range(max((len(v) for v in by_role.values()), default=0)):
        for r in roles:
            if i < len(by_role[r]):
                ordered.append(by_role[r][i])

    log.info(
        "plan: %d questions (%s)",
        len(ordered),
        ", ".join(f"{r}={len(by_role[r])}" for r in roles),
    )
    return ordered, data.get("must_haves", []), data.get("highlights", [])


def setup_session(
    session: SessionState,
    *,
    job_title: str = "",
    job_description: str = "",
    topics: list[str] | None = None,
    resume_text: str = "",
    candidate_name: str = "",
    per_role: int = 4,
    roles: list[str] | None = None,
) -> dict:
    """Populate a session from a job advert and a resume.

    Safe to call with nothing supplied: the panel then falls back to the
    generic bank, and the summary says so plainly rather than pretending the
    interview is personalised when it is not.
    """
    session.job = JobSpec(
        title=job_title.strip(),
        description=job_description.strip(),
        topics=[t.strip() for t in (topics or []) if t.strip()],
    )
    session.candidate = CandidateProfile(
        name=candidate_name.strip(), resume_text=resume_text.strip()
    )

    if not (session.job.is_set or session.candidate.is_set):
        log.warning("no job or resume supplied — the panel will improvise")
        return {"planned": 0, "personalised": False,
                "note": "no job description or resume supplied"}

    try:
        questions, must_haves, highlights = build_plan(
            session.job, session.candidate, per_role, roles
        )
    except Exception as exc:
        log.error("plan generation failed (%s) — falling back to generic questions", exc)
        return {"planned": 0, "personalised": False, "note": f"planning failed: {exc}"}

    session.plan = questions
    session.job.must_haves = must_haves
    session.candidate.highlights = highlights

    return {
        "planned": len(questions),
        "personalised": True,
        "must_haves": must_haves,
        "highlights": highlights,
        "questions": [
            {"role": q.role, "difficulty": q.difficulty, "topic": q.topic, "text": q.text}
            for q in questions
        ],
    }
