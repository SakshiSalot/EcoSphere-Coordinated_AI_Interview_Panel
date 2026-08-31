"""Kill every running agent on the project.

    python -m scripts.stop_agents

The panic button. A crashed test leaves agents in the channel billing until
their idle timeout, and Agora bills per agent-minute — three orphaned personas
drain three times as fast as you would guess.
"""

import asyncio
import sys

from src.agora import agent as agora


async def main() -> int:
    listing = await agora.list_agents()
    data = listing.get("data") if isinstance(listing, dict) else None
    live = (data or {}).get("list") if isinstance(data, dict) else None

    if not live:
        print(f"Nothing running. ({listing})")
        return 0

    print(f"Stopping {len(live)} agent(s):")
    for a in live:
        agent_id = a.get("agent_id")
        print(f"  {agent_id}  {a.get('name')}")
        await agora.leave(agent_id)

    print(f"\nAfter teardown: {await agora.list_agents()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
