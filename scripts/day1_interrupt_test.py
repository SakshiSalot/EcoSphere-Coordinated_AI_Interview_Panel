"""Day 1 gate: join ONE agent and prove a human can interrupt it mid-sentence.

This is the single assumption every remaining day rests on. If it fails, the
answer is the Agora Discord today — not a workaround tomorrow.

    python -m scripts.day1_interrupt_test

Teardown is wrapped in `finally` from the very first test on purpose: an agent
left running after a crash keeps billing until its idle timeout, and that is
how teams quietly lose their free minutes.
"""

import argparse
import asyncio
import logging
import sys

from src import config
from src.agora import agent as agora
from src.agora.tokens import build_token
from src.conductor.personas import greeting, persona

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("day1")


async def main(channel: str, role: str, candidate_uid: int) -> int:
    config.require_agora()

    p = persona(role)
    agent_uid = p["uid"]
    session_id = channel

    agent_token = build_token(channel, agent_uid)
    candidate_token = build_token(channel, candidate_uid)

    body = agora.build_join_body(
        session_id=session_id,
        role=role,
        persona=p,
        channel=channel,
        token=agent_token,
        agent_uid=agent_uid,
        remote_uids=["*"],
        greeting=greeting(role),
    )

    print()
    print("=" * 72)
    print("  JOIN THE CHANNEL AS THE CANDIDATE")
    print("=" * 72)
    print(f"  Agora web demo : https://webdemo.agora.io/basicVoiceCall/index.html")
    print(f"  App ID         : {config.AGORA_APP_ID}")
    print(f"  Channel        : {channel}")
    print(f"  Token          : {candidate_token or '(none — App-ID-only mode)'}")
    print(f"  Gateway        : {config.GATEWAY_PUBLIC_URL}")
    print(f"  llm.url        : {body['properties']['llm']['url']}")
    print("=" * 72)
    print()

    agent_id = ""
    try:
        agent_id = await agora.join(body)
        print(f"\n  {p['name']} is in the channel (agent_id={agent_id}).\n")
        print("  THE TEST — do all three, in order:")
        print("    1. Hear the greeting. It must state the panel is AI.")
        print("    2. Speak over her MID-SENTENCE. She must stop and yield.")
        print("    3. Answer normally. She must reply through your gateway.\n")
        print("  Press Enter here when you are done to stop billing.\n")
        await asyncio.get_running_loop().run_in_executor(None, input)

        t = await agora.turns(agent_id)
        print("\n  Per-turn metrics (for the deck's latency figures):")
        print(f"  {t}\n")
        h = await agora.history(agent_id)
        print(f"  History: {h}\n")
    finally:
        if agent_id:
            await agora.leave(agent_id)
        leftovers = await agora.list_agents()
        print(f"  Agents still running after teardown: {leftovers}")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="interview-day1")
    ap.add_argument("--role", default="technical",
                    choices=["technical", "product", "behavioural"])
    ap.add_argument("--candidate-uid", type=int, default=2001)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.channel, a.role, a.candidate_uid)))
