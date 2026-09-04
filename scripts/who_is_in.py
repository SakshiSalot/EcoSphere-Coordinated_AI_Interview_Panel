"""Who is actually in the interview channel, right now.

    python -m scripts.who_is_in <session-id>

RUN IT WHILE AN INTERVIEW IS LIVE. "The transcript says Arjun spoke but I
heard nothing" has three possible causes and they look identical from the
outside:

    his agent is not in the channel      -> he never joined, or was dropped
    he is in the channel but not heard   -> synthesis or the browser
    he is not in this list at all        -> the panel started short

Only the first is visible from the server, and this is how you see it. Without
it the answer has to be guessed at from a browser console, which is not
available on somebody else's machine or in a recording of a demo.

Uses the RTC channel API — a different service from Conversational AI, same
Customer ID and Secret.
"""

import asyncio
import sys

from src import config
from src.agora.channel import users_in
from src.conductor.personas import all_roles, persona
from src.agora.agent import CANDIDATE_UID


async def main(channel: str) -> int:
    config.require_agora()

    known = {persona(r)["uid"]: persona(r)["name"] for r in all_roles()}
    known[CANDIDATE_UID] = "THE CANDIDATE"

    result = await users_in(channel)
    if result.get("error"):
        print(f"\n  could not read the channel: {result['error']}\n")
        return 1
    if not result["exists"]:
        print(f"\n  channel {channel!r} does not exist — nobody has joined.\n")
        return 1

    present = set(result["users"])
    print(f"\n  channel {channel}\n")
    for uid, name in known.items():
        here = uid in present
        mark = "\033[32min\033[0m " if here else "\033[31mOUT\033[0m"
        print(f"    {mark}  {uid:5}  {name}")

    stranger = present - set(known)
    if stranger:
        print(f"\n    unrecognised uids in the channel: {sorted(stranger)}")

    missing = [n for u, n in known.items()
               if u not in present and u != CANDIDATE_UID]
    print()
    if CANDIDATE_UID not in present:
        print("  The CANDIDATE is not in the channel. Agents will talk to an "
              "empty room and bill for it.")
    elif missing:
        print(f"  In the channel but not present: {', '.join(missing)}.")
        print("  If one of those is the interviewer you cannot hear, the "
              "problem is the join, not the audio.")
    else:
        print("  Everyone expected is in the channel. A voice you cannot hear "
              "is therefore being synthesised and published, or failing in "
              "the browser — not missing from the room.")
    print()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
