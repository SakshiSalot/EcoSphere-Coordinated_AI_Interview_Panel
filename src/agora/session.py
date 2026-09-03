"""Multi-agent lifecycle — this is where it stops being a chatbot and
becomes a panel.

Each persona joins as its own Agora agent with its own UID, its own voice, and
its own URL segment on the gateway. The URL segment is what lets the gateway
tell which persona is calling, since Agora sends an identical body for every
agent.

Teardown must cover all of them. One survivor from a crashed session bills
silently for as long as it takes somebody to notice, which is the most common
way hackathon teams lose their free minutes.
"""

import asyncio
import logging

from src import config
from src.agora import agent as agora
from src.agora.agent import CANDIDATE_UID
from src.agora.tokens import build_token
from src.conductor.personas import active_roles, persona

log = logging.getLogger("agora.session")


# Which agents are live, per interview. The gateway needs this so a browser
# can start and stop a panel over HTTP — a web page cannot run a Python
# script, and every agent it starts must be stoppable by something other than
# the terminal that started it.
_ACTIVE: dict[str, dict[str, str]] = {}


def active(session_id: str) -> dict[str, str]:
    return dict(_ACTIVE.get(session_id, {}))


def remember(session_id: str, agents: dict[str, str]) -> None:
    _ACTIVE[session_id] = dict(agents)


def forget(session_id: str) -> dict[str, str]:
    return _ACTIVE.pop(session_id, {})


async def start(
    channel: str,
    session_id: str | None = None,
    roles: list[str] | None = None,
    idle_timeout: int | None = None,
) -> dict[str, str]:
    """Join the panel. Returns {role: agent_id} for everyone who got in.

    Agents join concurrently — serially, three joins take three round trips
    and the candidate hears nothing for several seconds.

    No `greeting_message` is set on any agent. Agora ignored it in testing and
    called the gateway for the opening line instead; the AI disclosure now
    lives in the first utterance, which we control. Setting a greeting as well
    would risk two personas speaking at once on join — the one failure that
    looks worst on stage.
    """
    session_id = session_id or channel
    roles = roles or active_roles()

    failures: list[str] = []

    async def join_one(role: str) -> tuple[str, str] | None:
        p = persona(role)

        # The persona's own voice first, then anything else this account
        # accepts. A vendor outage changes how Priya sounds; it should not
        # decide whether the interview happens at all.
        voices = [p["tts"], *agora.TTS_FALLBACKS]

        for attempt, tts in enumerate(voices):
            candidate = dict(p)
            candidate["tts"] = tts
            body = agora.build_join_body(
                session_id=session_id,
                role=role,
                persona=candidate,
                channel=channel,
                token=build_token(channel, p["uid"]),
                agent_uid=p["uid"],
                # ONLY the candidate. Subscribing to "*" makes the panel
                # interview itself.
                remote_uids=[str(CANDIDATE_UID)],
            )
            if idle_timeout:
                body["properties"]["idle_timeout"] = idle_timeout
            try:
                agent_id = await agora.join(body)
                if attempt:
                    log.warning(
                        "%s joined on a FALLBACK voice (%s) — the configured "
                        "one could not be reached, so they will not sound as "
                        "intended", p["name"], tts.get("vendor"),
                    )
                return role, agent_id
            except Exception as exc:
                detail = str(exc)[:200]
                log.error("%s failed to join on %s: %s",
                          p["name"], tts.get("vendor"), detail)
                failures.append(f"{p['name']} ({tts.get('vendor')}): {detail}")

        return None

    results = await asyncio.gather(*(join_one(r) for r in roles))
    agents = {r: a for r, a in (x for x in results if x)}

    log.info(
        "panel of %d: %s",
        len(agents),
        ", ".join(f"{persona(r)['name']}={a[:8]}" for r, a in agents.items()),
    )

    # A partial panel is worse than none — the conductor may hand the floor to
    # somebody who is not in the room, and the interview stalls in silence.
    if len(agents) != len(roles):
        missing = [persona(r)["name"] for r in roles if r not in agents]
        await stop(agents)
        # The REASON travels with the failure. "Missing: ['Priya', 'Arjun']"
        # says only that something went wrong and sends whoever reads it to
        # the logs; the actual cause — an Agora outage, a rejected model
        # name, an unreachable gateway — is what tells them whether to retry
        # or to change something.
        why = failures[0] if failures else "no detail from Agora"
        raise RuntimeError(
            f"panel incomplete, tore down. Missing: {missing}. First failure — {why}"
        )

    return agents


async def stop(agents: dict[str, str]) -> None:
    """Stop every agent, even if some fail. Always call this from `finally`."""
    async def leave_one(role: str, agent_id: str) -> None:
        try:
            await agora.leave(agent_id)
        except Exception as exc:
            log.error("failed to stop %s (%s): %s", role, agent_id, str(exc)[:150])

    await asyncio.gather(*(leave_one(r, a) for r, a in agents.items()))
    log.info("panel stopped (%d agents)", len(agents))


async def survivors() -> int:
    """How many agents are still running account-wide. Read this after every
    test — the list is eventually consistent, so give it a couple of seconds
    before trusting a non-zero answer."""
    left = await agora.list_agents()
    return left.get("data", {}).get("count", 0)


async def stop_all() -> int:
    """Emergency brake: stop every agent on the account."""
    left = await agora.list_agents()
    lst = left.get("data", {}).get("list", []) or []
    for a in lst:
        aid = a.get("agent_id") or a.get("id")
        if aid:
            log.warning("stopping stray agent %s", aid)
            await agora.leave(aid)
    return len(lst)
