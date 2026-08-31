"""Who is actually in an RTC channel.

A separate REST API from the Conversational AI one, but the same Customer
ID / Secret. Worth having: "the agent joined but nothing happened" is
ambiguous until you can answer whether the candidate was ever in the room.
"""

import base64
import logging

import httpx

from src import config

log = logging.getLogger("agora.channel")

BASE = "https://api.agora.io/dev/v1"


def _headers() -> dict:
    raw = f"{config.AGORA_CUSTOMER_ID}:{config.AGORA_CUSTOMER_SECRET}"
    return {"Authorization": "Basic " + base64.b64encode(raw.encode()).decode()}


async def users_in(channel: str) -> dict:
    """Returns {"exists": bool, "users": [uid, ...]}."""
    url = f"{BASE}/channel/user/{config.AGORA_APP_ID}/{channel}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers=_headers())
        if r.status_code >= 400:
            return {"exists": False, "users": [], "error": r.text[:200]}
        data = r.json().get("data", {})
        return {
            "exists": bool(data.get("channel_exist")),
            "users": data.get("users", []) or [],
        }
    except Exception as exc:  # network, auth, shape change
        return {"exists": False, "users": [], "error": str(exc)[:200]}
