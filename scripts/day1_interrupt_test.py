"""Day 1 gate: join ONE agent and prove a human can interrupt it mid-sentence.

This is the single assumption every remaining day rests on. If it fails, the
answer is the Agora Discord today — not a workaround tomorrow.

    python -m scripts.day1_interrupt_test

Order matters here. The candidate joins the channel FIRST, and only then does
the agent join. Two reasons, both learned the hard way:

  * The greeting plays the moment the agent joins. If nobody is in the room
    yet, the disclosure is spoken to an empty channel and never heard.
  * `idle_timeout` starts counting immediately. Thirty seconds is not enough
    to paste three credentials into a browser, so the agent was dying before
    the test began.

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
from src.agora import channel as agora_channel
from src.agora.tokens import build_token
from src.conductor.personas import greeting, persona

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("day1")

WEBDEMO = "https://webdemo.agora.io/basicVoiceCall/index.html"


async def _enter() -> None:
    await asyncio.get_running_loop().run_in_executor(None, input)


async def main(ch: str, role: str, idle: int) -> int:
    config.require_agora()

    p = persona(role)
    agent_uid = p["uid"]

    # UID 0 mints a token valid for ANY user id in this channel. The web demo
    # picks its own uid, so a token bound to one specific uid is silently
    # rejected and the browser never joins — which looks exactly like "the
    # agent is broken".
    candidate_token = build_token(ch, 0)

    print()
    print("=" * 72)
    print("  STEP 1 — JOIN THE CHANNEL YOURSELF, BEFORE THE AGENT")
    print("=" * 72)
    print(f"  Open      : {WEBDEMO}")
    print(f"  App ID    : {config.AGORA_APP_ID}")
    print(f"  Channel   : {ch}")
    print(f"  Token     : {candidate_token or '(none — App-ID-only mode)'}")
    print("=" * 72)
    print("\n  Click Join and allow the microphone.")
    print("  Then press Enter here — the agent joins only after you are in.\n")
    await _enter()

    who = await agora_channel.users_in(ch)
    if who.get("users"):
        print(f"  In the channel: {who['users']}  — good.\n")
    else:
        print("\n  \033[31mNobody is in that channel.\033[0m")
        if who.get("error"):
            print(f"  ({who['error']})")
        print("  The browser did not actually join. Check the App ID and channel")
        print("  name match exactly, and that the page did not show an error.")
        print("\n  Press Enter to bring the agent in anyway, or Ctrl-C to stop.\n")
        await _enter()

    body = agora.build_join_body(
        session_id=ch, role=role, persona=p, channel=ch,
        token=build_token(ch, agent_uid), agent_uid=agent_uid,
        remote_uids=["*"], greeting=greeting(role),
    )
    body["properties"]["idle_timeout"] = idle

    agent_id = ""
    try:
        agent_id = await agora.join(body)
        print("\n" + "=" * 72)
        print(f"  STEP 2 — {p['name']} IS IN THE CHANNEL")
        print("=" * 72)
        print("    1. Listen. She must say out loud that the panel is AI.")
        print("    2. TALK OVER HER MID-SENTENCE. She must stop.")
        print("    3. Answer her question. She must reply.")
        print("=" * 72)
        print("\n  Press Enter when done to stop billing.\n")
        await _enter()

        turns = await agora.turns(agent_id)
        count = turns.get("total_turn_count", 0)
        print(f"\n  Turns taken: {count}")
        if not count:
            print("  \033[31mZero turns — she never spoke or never heard you.\033[0m")
        for t in turns.get("turns", [])[:6]:
            print(f"    {t}")

        hist = await agora.history(agent_id)
        print(f"\n  History: {str(hist)[:400]}\n")
    finally:
        if agent_id:
            await agora.leave(agent_id)
        # The agent list is eventually consistent — checked immediately after
        # a leave it still reports the agent, which reads as a billing leak.
        await asyncio.sleep(3)
        left = await agora.list_agents()
        n = left.get("data", {}).get("count", "?")
        print(f"  Agents still running after teardown: {n}  (must be 0)")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="interview-day1")
    ap.add_argument("--role", default="technical",
                    choices=["technical", "product", "behavioural"])
    ap.add_argument("--idle", type=int, default=120,
                    help="idle timeout in seconds; 30 is too short for a manual test")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.channel, a.role, a.idle)))
