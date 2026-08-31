"""Persona loading, panel selection, and prompt assembly.

The panel is chosen by NAME, not by size. PS11 says the panel "may include a
technical interviewer, hiring manager, customer, product manager or
behavioural interviewer" — so which roles sit on it is a property of the job
being interviewed for, not a number.

Adding a sixth persona is a YAML entry. Nothing in the conductor, the gateway
or the planner needs to change.
"""

import functools
import logging

import yaml

from src import config

log = logging.getLogger("personas")

# Set by a script for one run (e.g. `--roles technical,customer`). The
# conductor reads the panel globally on every turn, so an explicit override
# has to live somewhere it can see.
_OVERRIDE: list[str] | None = None


@functools.lru_cache(maxsize=1)
def load_personas() -> dict:
    with open(config.PERSONAS_FILE, encoding="utf-8") as f:
        personas = yaml.safe_load(f) or {}
    for key, p in personas.items():
        for required in ("name", "title", "uid", "prompt", "tts"):
            if required not in p:
                raise RuntimeError(f"persona {key!r} is missing {required!r}")
    uids = [p["uid"] for p in personas.values()]
    if len(set(uids)) != len(uids):
        raise RuntimeError(f"personas have duplicate uids: {uids}")
    return personas


def all_roles() -> list[str]:
    """Every persona that exists, in panel order."""
    personas = load_personas()
    return sorted(personas, key=lambda r: personas[r].get("order", 99))


def default_panel() -> list[str]:
    """Roles marked as on the panel by default."""
    personas = load_personas()
    on = [r for r in all_roles() if personas[r].get("default", False)]
    return on or all_roles()[:3]


def set_panel(roles: list[str] | None) -> list[str]:
    """Override the panel for this process. Returns what was set."""
    global _OVERRIDE
    if not roles:
        _OVERRIDE = None
        return active_roles()
    known = all_roles()
    unknown = [r for r in roles if r not in known]
    if unknown:
        raise RuntimeError(f"unknown roles {unknown}. Available: {known}")
    _OVERRIDE = list(roles)
    log.info("panel set to: %s", ", ".join(persona(r)["name"] for r in _OVERRIDE))
    return _OVERRIDE


def active_roles(panel_size: int | None = None) -> list[str]:
    """Who is on the panel right now.

    Precedence: an explicit override from a script, then PANEL_ROLES in .env,
    then the first PANEL_SIZE of the default panel.
    """
    if _OVERRIDE is not None:
        return list(_OVERRIDE)

    if config.PANEL_ROLES:
        named = [r.strip() for r in config.PANEL_ROLES.split(",") if r.strip()]
        known = all_roles()
        chosen = [r for r in named if r in known]
        if chosen:
            return chosen
        log.warning("PANEL_ROLES=%r matched nothing; falling back", config.PANEL_ROLES)

    n = panel_size if panel_size is not None else config.PANEL_SIZE
    panel = default_panel()
    return panel[: max(1, min(n, len(panel)))]


def persona(role: str) -> dict:
    personas = load_personas()
    if role not in personas:
        raise RuntimeError(f"unknown persona {role!r}. Available: {list(personas)}")
    return personas[role]


def concern(role: str) -> str:
    """A short phrase describing what this persona cares about, for the
    question planner. Derived from the persona's own `domains` so a new
    persona needs no planner change."""
    p = persona(role)
    return p.get("concern") or ", ".join(p.get("domains", [])) or p["title"].lower()


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


def disclosure(role: str) -> str:
    """Spoken AI disclosure — PS11 capability 11.

    Prepended to the panel's very first utterance rather than left to Agora's
    `greeting_message`. In testing that field was ignored: Agora called our
    gateway for the opening line and spoke that instead, so the disclosure
    never happened. Owning it in the first utterance makes it unconditional.
    """
    p = persona(role)
    return (
        f"Hello, and welcome. Before we begin, I should be clear that every "
        f"interviewer on this panel is an AI, not a person. "
        f"I'm {p['name']}, your {p['title'].lower()}. "
    )


def greeting(role: str) -> str:
    p = persona(role)
    return (
        f"Hello, and welcome. Before we begin, I want to be clear that every "
        f"interviewer on this panel is an AI, not a person. "
        f"I'm {p['name']}, your {p['title'].lower()}. Shall we start?"
    )


# Kept so existing imports keep working; prefer all_roles().
PANEL_ORDER = ["technical", "product", "behavioural"]
