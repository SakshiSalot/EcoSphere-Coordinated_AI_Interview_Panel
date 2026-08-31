"""Persona loading and prompt assembly.

Assembling the system prompt in one place means the shared-context digest and
the difficulty level both plug into a single function later, instead of being
scattered through the codebase.
"""

import functools

import yaml

from src import config

# Order matters: PANEL_SIZE=2 must keep the technical-to-product handoff, which
# is the demo centrepiece and the problem statement's own example scenario.
PANEL_ORDER = ["technical", "product", "behavioural"]


@functools.lru_cache(maxsize=1)
def load_personas() -> dict:
    with open(config.PERSONAS_FILE, encoding="utf-8") as f:
        personas = yaml.safe_load(f)
    missing = [r for r in PANEL_ORDER if r not in personas]
    if missing:
        raise RuntimeError(f"personas.yaml is missing roles: {missing}")
    return personas


def active_roles(panel_size: int | None = None) -> list[str]:
    n = panel_size if panel_size is not None else config.PANEL_SIZE
    return PANEL_ORDER[: max(1, min(n, len(PANEL_ORDER)))]


def persona(role: str) -> dict:
    return load_personas()[role]


def persona_prompt(
    role: str,
    difficulty: str = "medium",
    shared_context: str = "",
) -> str:
    """The full system message for one persona on one turn."""
    p = persona(role)
    parts = [p["prompt"].strip()]

    if shared_context:
        parts.append(
            "What the candidate has already told the other interviewers:\n"
            f"{shared_context.strip()}\n"
            "Do not make them repeat it. Build on it."
        )

    parts.append(
        {
            "easy": "Keep this question approachable. The candidate is finding it hard.",
            "medium": "Pitch this question at a solid mid-level.",
            "hard": "Push harder. The candidate is handling this well — go deeper.",
        }.get(difficulty, "Pitch this question at a solid mid-level.")
    )

    return "\n\n".join(parts)


def greeting(role: str) -> str:
    """Spoken AI disclosure — PS11 capability 11. The lead persona says this
    before anything else, and the browser shows a matching banner."""
    p = persona(role)
    return (
        f"Hello, and welcome. Before we begin, I want to be clear that every "
        f"interviewer on this panel is an AI, not a person. "
        f"I'm {p['name']}, your {p['title'].lower()}. Shall we start?"
    )
