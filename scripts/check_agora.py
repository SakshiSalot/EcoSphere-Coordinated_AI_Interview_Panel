"""Verify Agora credentials before spending a single agent-minute.

    python -m scripts.check_agora

Checks, in the order they fail:

  1. .env is populated at all.
  2. Customer ID / Secret authenticate — GET /agents is a free, read-only call
     that returns 401 on bad Basic auth and 404 on a wrong App ID.
  3. The App Certificate actually signs a token.
  4. GATEWAY_PUBLIC_URL answers /health from the public internet, which is the
     only thing Agora cares about.

Nothing here starts an agent, so it costs nothing and can be run as often as
you like while wiring the console up.
"""

import asyncio
import base64
import sys

import httpx

from src import config
from src.agora.tokens import build_token

OK = "  \033[32mPASS\033[0m  "
NO = "  \033[31mFAIL\033[0m  "
HM = "  \033[33mWARN\033[0m  "

TIMEOUT = httpx.Timeout(15.0, connect=8.0)


def _mask(v: str) -> str:
    if not v:
        return "(empty)"
    return f"{v[:4]}…{v[-4:]} ({len(v)} chars)"


def check_env() -> bool:
    print("\n1. .env")
    fields = [
        ("AGORA_APP_ID", config.AGORA_APP_ID, True),
        ("AGORA_APP_CERTIFICATE", config.AGORA_APP_CERTIFICATE, False),
        ("AGORA_CUSTOMER_ID", config.AGORA_CUSTOMER_ID, True),
        ("AGORA_CUSTOMER_SECRET", config.AGORA_CUSTOMER_SECRET, True),
    ]
    ok = True
    for name, value, required in fields:
        if value:
            print(f"{OK}{name:<24}{_mask(value)}")
        elif required:
            print(f"{NO}{name:<24}not set")
            ok = False
        else:
            print(f"{HM}{name:<24}not set — App-ID-only mode, tokens will be empty")
    return ok


async def check_rest() -> bool:
    """GET /agents. Free, read-only, and the fastest way to tell a bad
    Customer Secret apart from a bad App ID."""
    print("\n2. REST credentials (Customer ID / Secret)")
    raw = f"{config.AGORA_CUSTOMER_ID}:{config.AGORA_CUSTOMER_SECRET}"
    headers = {
        "Authorization": "Basic " + base64.b64encode(raw.encode()).decode(),
        "Content-Type": "application/json",
    }
    url = f"{config.AGORA_REST_BASE}/{config.AGORA_APP_ID}/agents"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, headers=headers)
    except Exception as e:
        print(f"{NO}could not reach api.agora.io: {e}")
        return False

    if r.status_code == 401:
        print(f"{NO}401 — Customer ID or Secret is wrong.")
        print("        Console › Developer Toolkit › RESTful API › Add a secret.")
        print("        The secret is shown ONCE; regenerate if you lost it.")
        return False
    if r.status_code == 403:
        print(f"{NO}403 — credentials are valid but this project is not")
        print("        entitled. Enable Conversational AI Engine for the project.")
        return False
    if r.status_code == 404:
        print(f"{NO}404 — App ID {config.AGORA_APP_ID!r} not found under this account.")
        return False
    if r.status_code >= 400:
        print(f"{NO}{r.status_code}: {r.text[:300]}")
        return False

    running = r.json()
    print(f"{OK}authenticated, and the project accepts agent calls")
    data = running.get("data") if isinstance(running, dict) else None
    live = (data or {}).get("list") if isinstance(data, dict) else None
    if live:
        print(f"{HM}{len(live)} agent(s) ALREADY RUNNING and billing:")
        for a in live:
            print(f"        {a.get('agent_id')}  {a.get('name')}  {a.get('status')}")
        print("        Stop them: python -m scripts.stop_agents")
    else:
        print(f"{OK}no orphaned agents billing right now")
    return True


def check_token() -> bool:
    print("\n3. App Certificate signs an RTC token")
    if not config.AGORA_APP_CERTIFICATE:
        print(f"{HM}no certificate — build_token() returns \"\".")
        print("        Fine for a first join, but enable it before the demo.")
        return True
    try:
        t = build_token("preflight", 1001)
    except Exception as e:
        print(f"{NO}{e}")
        return False
    if not t:
        print(f"{NO}builder returned an empty token")
        return False
    print(f"{OK}token minted: {_mask(t)}")
    return True


async def check_gateway() -> bool:
    print("\n4. Gateway reachable from the public internet")
    if not config.GATEWAY_PUBLIC_URL:
        print(f"{NO}GATEWAY_PUBLIC_URL not set.")
        print("        cloudflared tunnel --url http://localhost:7860")
        return False
    url = f"{config.GATEWAY_PUBLIC_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url)
    except Exception as e:
        print(f"{NO}{url} unreachable: {e}")
        return False
    if r.status_code != 200:
        print(f"{NO}{url} -> {r.status_code}")
        return False
    print(f"{OK}{url} -> {r.json()}")
    return True


async def main() -> int:
    print("=" * 68)
    print("  Agora preflight — no agents started, nothing billed")
    print("=" * 68)

    results = [check_env()]
    if results[0]:
        results.append(await check_rest())
        results.append(check_token())
    results.append(await check_gateway())

    print()
    if all(results):
        print("  All green. Run: python -m scripts.day1_interrupt_test\n")
        return 0
    print("  Fix the FAIL lines above, then re-run.\n")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
