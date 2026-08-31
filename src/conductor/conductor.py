"""Floor control — who speaks, and why.

Three agents all hear the candidate, so all three call the gateway on every
turn. The conductor is the only thing preventing them from answering
simultaneously. Because the silent path performs no inference, two-thirds of
our model calls cost nothing.

The policy below is deliberately rule-based rather than a model call. Floor
control has to be instant, deterministic, and explainable on stage — none of
which a model gives you.
"""

import logging

from src.conductor.personas import active_roles
from src.state.session import SessionState

log = logging.getLogger("conductor")

MAX_CONSECUTIVE = 3  # turns one persona may hold before the floor rotates


def holds_floor(session: SessionState, role: str) -> bool:
    return session.floor_holder == role


def decide_floor(session: SessionState) -> tuple[str, str]:
    """Choose who speaks next. Returns (role, reason).

    Rules are ordered by precedence. Each exists for a reason:

      scenario active  -> locked to its owner. A role-play interrupted after
                          one turn by another persona is a non sequitur, not
                          a scenario.
      pending handoff  -> the named role. A persona explicitly asked.
      contradiction    -> the persona who asked the original question. They
                          have standing to challenge it.
      2x vague         -> current holder keeps the floor and pins them down.
                          Moving on rewards the deflection.
      tech, no business-> product. This is the problem statement's own example
                          and our demo centrepiece.
      3 turns held     -> rotate, so all personas stay visibly active and a
                          judge sees a panel rather than one bot with two
                          silent extras.
    """
    roles = active_roles()
    current = session.floor_holder if session.floor_holder in roles else roles[0]

    # 1 — a role-play owns the floor until it completes
    if session.active_scenario and session.scenario_owner in roles:
        return _set(session, session.scenario_owner, "scenario in progress", current)

    # 2 — an explicit handoff request from a persona
    if session.pending_handoff:
        to_role, reason = session.pending_handoff
        session.pending_handoff = None
        if to_role in roles:
            return _set(session, to_role, reason or "handoff requested", current)

    # 3 — a contradiction goes back to whoever asked the original question
    contradictions = session.flags.recent("contradiction", 1)
    if contradictions and not contradictions[0].routed:
        f = contradictions[0]
        f.routed = True  # route a given contradiction once, not every turn
        asker = _asker_of(session, f.ref_turn_id)
        if asker in roles:
            return _set(session, asker, "contradiction with an earlier answer", current)

    # 4 — two vague answers in a row: hold and pin them down
    if session.consecutive_vague() >= 2:
        return _set(session, current, "answer still vague — pinning down", current)

    # 5 — technically sound, but nobody mentioned a customer
    if _needs_business_challenge(session) and "product" in roles and current != "product":
        return _set(
            session,
            "product",
            "technically sound but no customer impact stated",
            current,
        )

    # 6 — rotate so the panel stays a panel
    if session.consecutive_turns >= MAX_CONSECUTIVE:
        nxt = roles[(roles.index(current) + 1) % len(roles)]
        return _set(session, nxt, "rotating the floor", current)

    return _set(session, current, "continuing", current)


def _set(session: SessionState, role: str, reason: str, previous: str) -> tuple[str, str]:
    if role == previous:
        session.consecutive_turns += 1
    else:
        session.consecutive_turns = 1
        log.info("floor: %s -> %s (%s)", previous, role, reason)
    session.floor_holder = role
    return role, reason


def _asker_of(session: SessionState, turn_id: int | None) -> str:
    """Which persona asked the question that produced this turn."""
    if not turn_id:
        return session.floor_holder
    for tid in range(turn_id - 1, 0, -1):
        t = session.turns.get(tid)
        if t and not t.is_candidate:
            return t.speaker
    return session.floor_holder


def _needs_business_challenge(session: SessionState) -> bool:
    """The PS11 example scenario, detected.

    Set by the async analysis worker when an answer scores well on technical
    concepts but mentions no user, customer, cost or business outcome. Kept as
    a flag rather than computed here so the conductor stays instant.
    """
    for f in session.flags.all()[-3:]:
        if f.kind == "no_business_framing" and not f.routed:
            f.routed = True
            return True
    return False


def note_candidate_turn(session: SessionState, text: str) -> int:
    """Record what the candidate just said, at the current difficulty."""
    return session.turns.add("candidate", text, difficulty=session.difficulty)


def note_persona_turn(session: SessionState, role: str, text: str) -> int:
    return session.turns.add(role, text, difficulty=session.difficulty)
