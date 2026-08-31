"""Find a TTS config this Agora account will actually accept.

Joins an agent, then leaves immediately. A few seconds of agent time per
attempt, which is far cheaper than discovering the answer during a live test.

    python -m scripts.probe_tts
"""

import argparse
import asyncio
import sys

from src import config
from src.agora import agent as agora
from src.agora.tokens import build_token
from src.conductor.personas import persona

# Candidates in the order we would prefer them. MiniMax first because the
# docs list it as available under managed mode; Microsoft is here only to
# confirm the SKU error is about the vendor and not our field names.
MINIMAX_URL = "wss://api-uw.minimax.io/ws/v1/t2a_v2"


def _mm(model: str, voice: str) -> dict:
    return {
        "credential_mode": "managed",
        "vendor": "minimax",
        "params": {
            "url": MINIMAX_URL,
            "model": model,
            "voice_setting": {"voice_id": voice, "speed": 1.0},
            "audio_setting": {"sample_rate": 44100},
        },
    }


CANDIDATES = [
    ("minimax 2.6-turbo · English_captivating_female1", _mm("speech-2.6-turbo", "English_captivating_female1")),
    ("minimax 2.8-turbo · English_captivating_female1", _mm("speech-2.8-turbo", "English_captivating_female1")),
    ("minimax 02-turbo  · English_captivating_female1", _mm("speech-02-turbo", "English_captivating_female1")),
    ("minimax 2.6-turbo · English_expressive_narrator", _mm("speech-2.6-turbo", "English_expressive_narrator")),
    ("minimax 2.6-turbo · English_magnetic_male",       _mm("speech-2.6-turbo", "English_magnetic_male")),
    ("minimax 2.6-turbo · English_Gentle-voiced_man",   _mm("speech-2.6-turbo", "English_Gentle-voiced_man")),
    ("minimax 2.6-turbo · Wise_Woman",                  _mm("speech-2.6-turbo", "Wise_Woman")),
]


async def probe(label: str, tts: dict, channel: str) -> bool:
    p = dict(persona("technical"))
    p["tts"] = tts
    body = agora.build_join_body(
        session_id=channel, role="technical", persona=p, channel=channel,
        token=build_token(channel, p["uid"]), agent_uid=p["uid"],
        remote_uids=["*"],
    )
    agent_id = ""
    try:
        agent_id = await agora.join(body)
        print(f"  \033[32mACCEPTED\033[0m  {label}")
        return True
    except RuntimeError as exc:
        msg = str(exc)
        detail = msg.split('"detail":"')[-1].split('"')[0] if '"detail"' in msg else msg
        print(f"  \033[31mrejected\033[0m  {label}\n              {detail[:150]}")
        return False
    finally:
        if agent_id:
            await agora.leave(agent_id)


async def main(channel: str) -> int:
    config.require_agora()
    print("\nProbing TTS configurations — each join is torn down immediately.\n")
    winners = []
    for label, tts in CANDIDATES:
        if await probe(label, tts, channel):
            winners.append(label)
        await asyncio.sleep(1.0)

    left = await agora.list_agents()
    total = left.get("data", {}).get("count", "?")
    print(f"\nAgents still running: {total}  (must be 0)")

    if winners:
        print(f"\n\033[32mUse one of:\033[0m\n" + "\n".join(f"  {w}" for w in winners))
        return 0
    print("\n\033[31mNothing accepted — paste the errors above.\033[0m")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="probe-tts")
    sys.exit(asyncio.run(main(ap.parse_args().channel)))
