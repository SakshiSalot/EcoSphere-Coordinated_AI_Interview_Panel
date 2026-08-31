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
from src.agora.tokens import build_token
from src.conductor.personas import active_roles, persona

log = logging.getLogger("agora.session")


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

    async def join_one(role: str) -> tuple[str, str] | None:
        p = persona(role)
        body = agora.build_join_body(
            session_id=session_id,
            role=role,
            persona=p,
            channel=channel,
            token=build_token(channel, p["uid"]),
            agent_uid=p["uid"],
            remote_uids=["*"],
        )
        if idle_timeout:
            body["properties"]["idle_timeout"] = idle_timeout
        try:
            return role, await agora.join(body)
        except Exception as exc:
            log.error("%s failed to join: %s", p["name"], str(exc)[:200])
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
        raise RuntimeError(f"panel incomplete, tore down. Missing: {missing}")

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
