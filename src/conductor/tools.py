"""The six tools the personas call, and what each one does to shared state.

These six implement five PS11 capabilities through one mechanism: turn-taking,
vagueness, contradictions, difficulty and role-play. They are also visible on
stage — surfacing tool calls in the UI is the cheapest way to make invisible
reasoning legible to a judge.

`strict: true` and `additionalProperties: false` stop the model inventing
fields, which small models do freely when the schema permits it.
"""

import json
import logging

from src.conductor.personas import all_roles
from src.conductor.scenarios import SCENARIOS
from src.state.session import SessionState

log = logging.getLogger("tools")

# Every persona that exists, so adding one to personas.yaml immediately makes
# it a valid handoff target with no code change.
ROLES = all_roles()

# The scenario ids, as an enum in the schema rather than a free string. A model
# handed `{"scenario_id": "string"}` invents plausible ids — "difficult_customer"
# — which used to set `active_scenario` to something no lookup could resolve,
# locking the floor to a role-play that did not exist.
SCENARIO_IDS = [s.id for s in SCENARIOS]


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS = [
    _fn(
        "request_handoff",
        "Hand the floor to a different interviewer, with a reason. Use this "
        "when the answer needs a perspective that is not yours — for example a "
        "technically sound answer that never mentions a customer.",
        {
            "to_role": {"type": "string", "enum": ROLES},
            "reason": {
                "type": "string",
                "description": "One short sentence naming what is missing.",
            },
        },
        ["to_role", "reason"],
    ),
    _fn(
        "flag_vague_answer",
        "Record that the candidate deflected or spoke in generalities. Quote "
        "the exact words that were vague.",
        {
            "quote": {"type": "string"},
            "why": {"type": "string"},
        },
        ["quote", "why"],
    ),
    _fn(
        "flag_contradiction",
        "Record that the candidate has contradicted something they said "
        "earlier. Both quotes are required.",
        {
            "turn_a": {"type": "integer"},
            "turn_b": {"type": "integer"},
            "quote_a": {"type": "string"},
            "quote_b": {"type": "string"},
            "why": {"type": "string"},
        },
        ["turn_a", "turn_b", "quote_a", "quote_b", "why"],
    ),
    _fn(
        "adjust_difficulty",
        "Move the difficulty of your next question up or down.",
        {"direction": {"type": "string", "enum": ["up", "down"]}},
        ["direction"],
    ),
    _fn(
        "launch_scenario",
        "Start a role-play scenario from the list in your instructions. You "
        "keep the floor until it resolves, and no other interviewer speaks.",
        {"scenario_id": {"type": "string", "enum": SCENARIO_IDS}},
        ["scenario_id"],
    ),
    _fn(
        "end_scenario",
        "End the role-play you are running and step back out of character. "
        "Call this as soon as it resolves.",
        {"outcome": {
            "type": "string",
            "description": "One sentence on what the candidate actually did.",
        }},
        ["outcome"],
    ),
    _fn(
        "record_evidence",
        "Record a scored observation about a competency, with the verbatim "
        "quote that justifies it.",
        {
            "competency": {"type": "string"},
            "concept": {"type": "string"},
            "quote": {"type": "string"},
            "polarity": {"type": "string", "enum": ["supports", "undermines"]},
        },
        ["competency", "concept", "quote", "polarity"],
    ),
]


# --- handlers -----------------------------------------------------------


def handle_tool_call(session: SessionState, name: str, arguments: str | dict) -> dict:
    """Apply one tool call to shared state.

    Arguments are always parsed with json.loads rather than string-matched:
    models vary in how they escape strings, so substring matching on
    serialised arguments breaks unpredictably.
    """
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            log.warning("tool %s: unparseable arguments %r", name, arguments[:200])
            return {"ok": False, "error": "bad arguments"}
    else:
        args = arguments or {}

    fn = _HANDLERS.get(name)
    if fn is None:
        log.warning("unknown tool call: %s", name)
        return {"ok": False, "error": f"unknown tool {name}"}

    result = fn(session, args)
    log.info("tool %s(%s) -> %s", name, _brief(args), result)
    return result


def _brief(args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:40]!r}" for k, v in args.items())


def _request_handoff(s: SessionState, a: dict) -> dict:
    to_role = a.get("to_role", "")
    if to_role not in ROLES:
        return {"ok": False, "error": "unknown role"}
    if to_role == s.floor_holder:
        return {"ok": False, "error": "already holds the floor"}
    s.pending_handoff = (to_role, a.get("reason", ""))
    return {"ok": True, "pending_handoff": to_role}


def _flag_vague(s: SessionState, a: dict) -> dict:
    turn = s.turns.last_candidate()
    s.flags.add(
        turn_id=turn.turn_id if turn else 0,
        kind="vague",
        detail=a.get("why", ""),
        quote=a.get("quote", ""),
    )
    return {"ok": True, "consecutive_vague": s.consecutive_vague()}


def _flag_contradiction(s: SessionState, a: dict) -> dict:
    # Both quotes required — a challenge without evidence is worse on stage
    # than a missed contradiction.
    if not a.get("quote_a") or not a.get("quote_b"):
        return {"ok": False, "error": "both quotes required"}
    s.flags.add(
        turn_id=int(a.get("turn_b") or 0),
        kind="contradiction",
        detail=a.get("why", ""),
        quote=a.get("quote_a", ""),
        quote_b=a.get("quote_b", ""),
        ref_turn_id=int(a.get("turn_a") or 0),
    )
    return {"ok": True}


def _adjust_difficulty(s: SessionState, a: dict) -> dict:
    from src.conductor.difficulty import nudge

    return {"ok": True, "difficulty": nudge(s, a.get("direction", "up"))}


def _launch_scenario(s: SessionState, a: dict) -> dict:
    """Start a role-play, if there is one by that name.

    Unknown ids are REFUSED rather than stored. Setting `active_scenario` to
    whatever the model said locks the floor to a role-play with no content
    behind it — the persona has nothing to play, and every other interviewer is
    silenced for the rest of the interview.
    """
    from src.conductor.scenarios import by_id

    scenario = by_id(a.get("scenario_id", ""))
    if scenario is None:
        return {"ok": False, "error": "unknown scenario"}
    if scenario.owner != s.floor_holder:
        # Each persona runs its own. The customer's outage role-play in the
        # technical interviewer's hands is two personas doing one job.
        return {"ok": False, "error": "that scenario belongs to another interviewer"}
    if s.active_scenario:
        return {"ok": False, "error": "a scenario is already running"}

    s.active_scenario = scenario.id
    s.scenario_owner = s.floor_holder
    s.scenario_turns = 0
    log.info("scenario %s launched by %s", scenario.id, s.scenario_owner)
    return {"ok": True, "scenario": s.active_scenario, "owner": s.scenario_owner}


def _end_scenario(s: SessionState, a: dict) -> dict:
    """Release the floor. The other half of launch, and it was missing."""
    if not s.active_scenario:
        return {"ok": False, "error": "no scenario is running"}

    turn = s.turns.last_candidate()
    outcome = (a.get("outcome") or "").strip()
    if outcome:
        # The role-play is only worth running if it produced evidence, so what
        # the candidate did is recorded against the competency it was probing
        # rather than evaporating with the character.
        from src.conductor.scenarios import by_id

        scenario = by_id(s.active_scenario)
        s.evidence.add(
            turn_id=turn.turn_id if turn else 0,
            competency=(scenario.probes[0] if scenario else "behaviour"),
            concept=f"role-play: {scenario.title if scenario else s.active_scenario}",
            quote=outcome[:300],
            polarity="supports",
        )

    log.info("scenario %s ended after %d exchanges", s.active_scenario, s.scenario_turns)
    ended, s.active_scenario = s.active_scenario, None
    s.scenario_owner = None
    s.scenario_turns = 0
    return {"ok": True, "ended": ended}


def _record_evidence(s: SessionState, a: dict) -> dict:
    turn = s.turns.last_candidate()
    if not a.get("quote"):
        return {"ok": False, "error": "quote required"}
    s.evidence.add(
        turn_id=turn.turn_id if turn else 0,
        competency=a.get("competency", "general"),
        concept=a.get("concept", ""),
        quote=a.get("quote", ""),
        polarity=a.get("polarity", "supports"),
    )
    return {"ok": True}


_HANDLERS = {
    "request_handoff": _request_handoff,
    "flag_vague_answer": _flag_vague,
    "flag_contradiction": _flag_contradiction,
    "adjust_difficulty": _adjust_difficulty,
    "launch_scenario": _launch_scenario,
    "end_scenario": _end_scenario,
    "record_evidence": _record_evidence,
}
