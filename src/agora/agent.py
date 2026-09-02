"""Agora Conversational AI Engine REST client.

  POST  {base}/{appid}/join
  POST  {base}/{appid}/agents/{agent_id}/leave
  POST  {base}/{appid}/agents/{agent_id}/interrupt
  POST  {base}/{appid}/agents/{agent_id}/update
  POST  {base}/{appid}/agents/{agent_id}/speak
  GET   {base}/{appid}/agents/{agent_id}/history
  GET   {base}/{appid}/agents/{agent_id}/turns
  GET   {base}/{appid}/agents

Authentication is HTTP Basic over Customer ID and Customer Secret — a
different credential pair from the App Certificate, which signs RTC tokens.
Confusing the two costs an hour.
"""

import base64
import logging

import httpx

from src import config

log = logging.getLogger("agora")

TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# The candidate joins on a FIXED uid so the agents can subscribe to that uid
# and nothing else.
#
# With `remote_rtc_uids: ["*"]` every agent subscribes to every other agent:
# Arjun hears Priya's greeting, transcribes it, and answers it as though the
# candidate had said it. A live run showed exactly that — Priya's AI
# disclosure arriving at Arjun as input, over and over. It also doubles the
# speech recognition we pay for and lets the panel talk to itself.
CANDIDATE_UID = 2001

# 480 ms of quiet ended the turn, which is shorter than the pause in the
# middle of an ordinary sentence. Agora was calling the gateway mid-answer
# with a partial transcript — " Uh, well, when the request is received, the." —
# and then again as it grew. Roughly a second is the usual conversational
# floor for end-of-turn.
DEFAULT_VAD = {
    "silence_duration_ms": 950,
    "speech_duration_ms": 15000,
    "threshold": 0.5,
    "interrupt_duration_ms": 160,
    "prefix_padding_ms": 300,
}


def _auth_header() -> str:
    raw = f"{config.AGORA_CUSTOMER_ID}:{config.AGORA_CUSTOMER_SECRET}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _url(path: str) -> str:
    return f"{config.AGORA_REST_BASE}/{config.AGORA_APP_ID}/{path.lstrip('/')}"


def _headers() -> dict:
    return {"Content-Type": "application/json", "Authorization": _auth_header()}


def build_join_body(
    *,
    session_id: str,
    role: str,
    persona: dict,
    channel: str,
    token: str,
    agent_uid: int,
    remote_uids: list[str],
    greeting: str | None = None,
) -> dict:
    """Assemble the join payload for one persona.

    `remote_rtc_uids` controls which participants the agent listens to. It is
    also the fallback turn-taking mechanism: an agent subscribed to nobody
    cannot hear the candidate and therefore cannot respond.
    """
    llm_url = f"{config.GATEWAY_PUBLIC_URL}/v1/{session_id}/{role}/chat/completions"

    body = {
        "name": f"{session_id}-{role}",
        "properties": {
            "channel": channel,
            "token": token,
            "agent_rtc_uid": str(agent_uid),
            "remote_rtc_uids": remote_uids,
            "enable_string_uid": False,
            "idle_timeout": config.AGORA_IDLE_TIMEOUT,
            "asr": persona.get("asr", {"language": "en-US"}),
            "llm": {
                "url": llm_url,
                "api_key": config.GATEWAY_SHARED_SECRET,
                "style": "openai",
                "system_messages": [
                    {"role": "system", "content": persona["prompt"]}
                ],
                "max_history": persona.get("max_history", 10),
                "params": {
                    "model": "echosphere-panel",
                    "max_tokens": 300,
                },
                "input_modalities": ["text"],
                "output_modalities": ["text", "audio"],
            },
            "tts": persona["tts"],
            "vad": persona.get("vad", DEFAULT_VAD),
            "advanced_features": {"enable_aivad": False, "enable_bhvs": False},
        },
    }

    # Only the persona that opens the interview greets — three simultaneous
    # greetings is the fastest way to make a panel sound broken.
    if greeting:
        body["properties"]["llm"]["greeting_message"] = greeting
        body["properties"]["llm"]["failure_message"] = "One moment."

    return body


async def join(body: dict) -> str:
    """Start an agent. Returns its agent_id."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(_url("join"), headers=_headers(), json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"join failed {r.status_code}: {r.text}")
    data = r.json()
    agent_id = data.get("agent_id") or data.get("agentId") or ""
    log.info("joined %s as agent_id=%s", body.get("name"), agent_id)
    return agent_id


async def leave(agent_id: str) -> None:
    """Stop an agent. An agent left running after a crashed test keeps
    billing until its idle timeout — this is the most common way teams
    quietly lose their free minutes."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(_url(f"agents/{agent_id}/leave"), headers=_headers())
    log.info("left agent_id=%s -> %s", agent_id, r.status_code)


async def interrupt(agent_id: str) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        await client.post(_url(f"agents/{agent_id}/interrupt"), headers=_headers())


async def update(agent_id: str, properties: dict) -> None:
    """Adjust agent parameters at runtime — used to swap `remote_rtc_uids`
    on a handoff if empty completions turn out not to silence an agent."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(
            _url(f"agents/{agent_id}/update"),
            headers=_headers(),
            json={"properties": properties},
        )
    log.info("update agent_id=%s -> %s %s", agent_id, r.status_code, r.text[:200])


async def history(agent_id: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(_url(f"agents/{agent_id}/history"), headers=_headers())
    return r.json() if r.status_code < 400 else {"error": r.text}


async def turns(agent_id: str) -> dict:
    """Per-turn conversation metrics — the source of the measured latency
    figures that go in the deck."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(_url(f"agents/{agent_id}/turns"), headers=_headers())
    return r.json() if r.status_code < 400 else {"error": r.text}


async def list_agents() -> dict:
    """Find orphaned agents from crashed tests before they drain the budget."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(_url("agents"), headers=_headers())
    return r.json() if r.status_code < 400 else {"error": r.text}
