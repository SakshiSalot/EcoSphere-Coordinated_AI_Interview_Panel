"""The OpenAI-compatible gateway.

The single idea the whole architecture rests on: Agora will point an agent's
`llm.url` at any endpoint that speaks OpenAI's /chat/completions protocol. So
instead of writing an app that calls a model, we write a service that pretends
to be one, and Agora drives it.

That gives us three things at once — three agents sharing one brain, a
conductor that can silence a persona by returning an empty completion, and all
the interview logic in ordinary Python we can test with no voice stack running.
"""

import logging
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src import config
from src.contract import next_utterance
from src.gateway.sse import sse, sse_silent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gateway")

app = FastAPI(title="EchoSphere Panel Gateway", docs_url=None, redoc_url=None)

def _valid_roles() -> set[str]:
    """Read from personas.yaml so a new persona is immediately routable —
    a hardcoded set here 404s the very agent we just joined."""
    from src.conductor.personas import all_roles

    return set(all_roles())


@app.get("/health")
async def health():
    """Cheap liveness probe — also the first thing to curl from a phone
    hotspot. If it answers from a network you do not control, Agora can
    reach it too."""
    return {
        "ok": True,
        "panel_size": config.PANEL_SIZE,
        "silence_mode": config.SILENCE_MODE,
    }


@app.post("/session/{session_id}/setup")
async def setup(session_id: str, request: Request):
    """Prepare an interview from a job advert and a resume.

    Called once before the agents join. Everything expensive happens here,
    where nobody is waiting — generating questions mid-conversation would add
    seconds to a live turn, and conversational quality is scored.
    """
    from src.conductor.personas import all_roles
    from src.intake.plan import setup_session
    from src.state.session import reset_session

    body = await request.json()
    session = reset_session(session_id)

    # The panel is chosen per interview and must live on the session — the
    # conductor runs in this process and would otherwise never hand the floor
    # to a persona the caller asked for.
    requested = [r.strip() for r in (body.get("roles") or []) if r.strip()]
    if requested:
        known = all_roles()
        unknown = [r for r in requested if r not in known]
        if unknown:
            raise HTTPException(400, f"unknown roles {unknown}; available {known}")
        session.roles = requested
        session.floor_holder = requested[0]
    result = setup_session(
        session,
        job_title=body.get("job_title", ""),
        job_description=body.get("job_description", ""),
        topics=body.get("topics") or [],
        resume_text=body.get("resume_text", ""),
        candidate_name=body.get("candidate_name", ""),
        per_role=int(body.get("per_role", 4)),
        roles=body.get("roles") or None,
    )
    result["roles"] = session.roles or None
    log.info(
        "session %s prepared: %d questions, personalised=%s, panel=%s",
        session_id, result["planned"], result["personalised"],
        session.roles or "(default)",
    )
    return result


@app.post("/session/{session_id}/start")
async def start_panel(session_id: str, request: Request):
    """Bring the panel into the voice channel.

    Exists so the whole interview is driveable over HTTP. `scripts/panel.py`
    can orchestrate from a terminal, but the candidate's web page cannot run a
    Python script — it needs to set up, start, and stop an interview with three
    fetch calls.

    Returns the candidate's own RTC token, because the browser has to join the
    same channel and must never be handed the App Certificate to mint one
    itself.
    """
    from src.agora import session as panel
    from src.agora.tokens import build_token
    from src.state.session import get_session

    body = await request.json() if await request.body() else {}
    session = get_session(session_id)
    channel = (body.get("channel") or session_id).strip()

    if panel.active(session_id):
        raise HTTPException(409, "this interview already has agents in the call")

    roles = session.roles or None
    try:
        agents = await panel.start(
            channel,
            session_id=session_id,
            roles=roles,
            idle_timeout=int(body.get("idle_timeout") or config.AGORA_IDLE_TIMEOUT),
        )
    except Exception as exc:
        log.error("panel failed to start for %s: %s", session_id, exc)
        raise HTTPException(502, f"panel failed to start: {exc}")

    panel.remember(session_id, agents)
    log.info("session %s: panel of %d joined %s", session_id, len(agents), channel)

    return {
        "channel": channel,
        "app_id": config.AGORA_APP_ID,
        "candidate_token": build_token(channel, 0),
        "agents": agents,
        "roles": list(agents),
    }


@app.post("/session/{session_id}/stop")
async def stop_panel(session_id: str):
    """Remove every agent from the call.

    Idempotent, and safe to call from a browser `beforeunload` handler — an
    agent left behind bills until its idle timeout, and a candidate closing
    the tab is the most likely way that happens.
    """
    from src.agora import session as panel

    agents = panel.forget(session_id)
    if agents:
        await panel.stop(agents)
    return {"stopped": list(agents), "count": len(agents)}


@app.get("/session/{session_id}/state")
async def state(session_id: str):
    """What the recruiter dashboard renders, and the quickest way to see
    whether the panel is behaving during a live test."""
    from src.state.session import get_session

    s = get_session(session_id)
    snap = s.snapshot()
    snap["plan"] = [
        {"role": q.role, "difficulty": q.difficulty, "topic": q.topic,
         "text": q.text, "asked": q.asked}
        for q in s.plan
    ]
    snap["transcript"] = [
        {"turn_id": t.turn_id, "speaker": t.speaker, "text": t.text}
        for t in s.turns.all()
    ]
    return snap


@app.post("/session/{session_id}/finish")
async def finish(session_id: str):
    """Total the interview and hand back the assessment.

    Marking triggers itself — an answer arriving is the signal — but totalling
    does not. Agora never tells us the interview is over; the agents simply
    leave the channel. So somebody has to say so, and this is where they say it.

    Call it after the agents leave. Safe to call twice: coverage is preserved
    as a proportion when the marks are rescaled, so the numbers do not move.

    Returns the assessment DATA — marks per interviewer, the total, penalties,
    and every concept with the words that earned it. The written report renders
    this; it makes no further judgement, because every mark already carries a
    turn number and a quote verified against the transcript.
    """
    from src.analysis import pipeline
    from src.state.session import get_session

    session = get_session(session_id)
    if not len(session.turns):
        raise HTTPException(404, f"no interview recorded for session {session_id!r}")

    # The last answer or two are usually still being judged when the agents
    # leave. Without this the report silently omits the end of the interview —
    # which is exactly where a candidate is pushed hardest.
    pipeline.drain()

    result = pipeline.finalise(session)
    result["evidence"] = [
        {"turn_id": turn_id, "role": role, "concept": concept, "quote": quote}
        for turn_id, role, concept, quote in pipeline.evidence_for(session)
    ]
    result["flags"] = [
        {"turn_id": f.turn_id, "kind": f.kind, "detail": f.detail,
         "quote": f.quote, "source": f.source}
        for f in session.flags.all()
    ]

    log.info(
        "session %s finished: %.1f/%.0f over %d answers, %d evidence quotes",
        session_id, result["earned"], result["total"],
        result["answers"], len(result["evidence"]),
    )
    return result


@app.post("/v1/{session_id}/{role}/chat/completions")
async def chat_completions(
    session_id: str,
    role: str,
    request: Request,
    authorization: str = Header(default=""),
):
    started = time.perf_counter()

    # Agora sends llm.api_key as a bearer token. Without this check the
    # endpoint is open to anyone who finds the URL.
    if config.GATEWAY_SHARED_SECRET:
        presented = authorization.removeprefix("Bearer ").strip()
        if presented != config.GATEWAY_SHARED_SECRET:
            log.warning("rejected %s/%s — bad or missing bearer token", session_id, role)
            raise HTTPException(status_code=401, detail="unauthorized")

    if role not in _valid_roles():
        raise HTTPException(status_code=404, detail=f"unknown role {role!r}")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body is not JSON")

    messages = body.get("messages", [])
    log.info(
        "-> %s/%-11s  %d messages  last=%r",
        session_id,
        role,
        len(messages),
        (messages[-1].get("content", "") if messages else "")[:70],
    )

    stream = next_utterance(session_id, role, messages)

    # The silence mechanism the whole turn-taking design depends on:
    # no model call, no cost, no speech.
    if stream is None:
        log.info("<- %s/%-11s  SILENT (%.0f ms)", session_id, role,
                 (time.perf_counter() - started) * 1000)
        return StreamingResponse(
            sse_silent(config.SILENCE_MODE),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    log.info("<- %s/%-11s  SPEAKING (%.0f ms to first byte)", session_id, role,
             (time.perf_counter() - started) * 1000)
    return StreamingResponse(
        sse(stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Never return a bare 500 to Agora — it reads as a dead endpoint. Log
    loudly, answer with a valid empty completion, and keep the call alive."""
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=200, content={"error": str(exc)})


if __name__ == "__main__":
    # So `python -m src.gateway.app` works as well as `make gateway-live`.
    # Reload is deliberately OFF here: session state lives in memory, and a
    # reload firing mid-interview wipes the transcript and the question plan.
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
