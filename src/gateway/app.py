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
from src.gateway import auth
from src.gateway.sse import sse, sse_silent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gateway")

app = FastAPI(title="EchoSphere Panel Gateway", docs_url=None, redoc_url=None)

# What a candidate may see of their own interview: enough to render the page —
# who is speaking, whether it has ended — and nothing that tells them how they
# are doing. Marks, flags, the difficulty band and the question plan all stay
# out: a candidate who can read them is answering a different exam.
_CANDIDATE_VISIBLE = ("session_id", "floor_holder", "closed", "job_title", "turns")


def _caller(authorization: str, session_id: str, allow: tuple[str, ...]) -> str:
    """Authenticate the caller, and check their role is allowed here.

    Every session endpoint goes through this. Without it, guessing a session id
    on the public tunnel is enough to read a candidate's interview or to call
    /start and spend the free agent-minutes.

    The response says only 401 or 403. Telling a caller *why* — expired, wrong
    session, bad signature — tells an attacker which part of their guess was
    right; the detail goes to the log, where we can see it and they cannot.
    """
    # Peek at the session rather than get_session(): that CREATES one, so an
    # unauthenticated caller hammering random ids would grow the session table
    # without limit. Absent session means epoch 0, which is what a token for a
    # brand-new interview carries.
    from src.state.session import all_sessions

    existing = all_sessions().get(session_id)
    epoch = existing.token_epoch if existing else 0

    try:
        role = auth.caller_role(authorization, session_id, epoch)
    except auth.AuthError as exc:
        log.warning("401 %s: %s", session_id, exc)
        raise HTTPException(status_code=401, detail="unauthorized")

    if role not in allow:
        log.warning("403 %s: %s not permitted here", session_id, role)
        raise HTTPException(status_code=403, detail="forbidden")
    return role


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
async def setup(
    session_id: str, request: Request, authorization: str = Header(default="")
):
    """Prepare an interview from a job advert and a resume.

    Called once before the agents join. Everything expensive happens here,
    where nobody is waiting — generating questions mid-conversation would add
    seconds to a live turn, and conversational quality is scored.

    OPERATOR ONLY, because this is where the session tokens are minted. It is
    the bootstrap: whoever creates an interview must already hold the gateway
    key, and everyone else works from the tokens it hands back.
    """
    _caller(authorization, session_id, (auth.OPERATOR,))
    from src.conductor.personas import all_roles
    from src.intake.plan import setup_session
    from src.state.session import all_sessions, reset_session

    body = await request.json()

    # Carry the epoch forward and raise it. Resetting to 0 would mean tokens
    # from the previous interview on this id verify again against the new one.
    previous = all_sessions().get(session_id)
    session = reset_session(session_id)
    session.token_epoch = (previous.token_epoch if previous else 0) + 1

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
    # Two tokens, because the candidate is the person being assessed. One
    # shared token would let them read /state mid-interview, see "turn 6:
    # 2.0/10, flagged vague", and simply answer again — the assessment would
    # stop measuring the candidate and start measuring who read the API.
    result.update(auth.tokens_for(session_id, session.token_epoch))
    result["roles"] = session.roles or None
    log.info(
        "session %s prepared: %d questions, personalised=%s, panel=%s",
        session_id, result["planned"], result["personalised"],
        session.roles or "(default)",
    )
    return result


@app.post("/session/{session_id}/start")
async def start_panel(
    session_id: str, request: Request, authorization: str = Header(default="")
):
    """Bring the panel into the voice channel.

    Exists so the whole interview is driveable over HTTP. `scripts/panel.py`
    can orchestrate from a terminal, but the candidate's web page cannot run a
    Python script — it needs to set up, start, and stop an interview with three
    fetch calls.

    Returns the candidate's own RTC token, because the browser has to join the
    same channel and must never be handed the App Certificate to mint one
    itself.
    """
    # Anyone holding a token for this interview may bring the panel in — the
    # candidate's own page does it when they join. But it must be a token for
    # THIS session: /start spends agent-minutes, so an open one is a way to
    # drain the free 300 from anywhere on the internet.
    _caller(authorization, session_id,
            (auth.OPERATOR, auth.RECRUITER, auth.CANDIDATE))

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
        # `rtc_token`, NOT `candidate_token`. /setup already returns a
        # `candidate_token` and it is a completely different secret: that one
        # authenticates to THIS gateway, this one joins an Agora channel.
        # Sharing a name guarantees the browser eventually sends each where the
        # other belongs, and both failures are silent.
        "rtc_token": build_token(channel, 0),
        "agents": agents,
        "roles": list(agents),
    }


@app.post("/session/{session_id}/stop")
async def stop_panel(session_id: str, authorization: str = Header(default="")):
    """Remove every agent from the call.

    Idempotent, and safe to call from a browser `beforeunload` handler — an
    agent left behind bills until its idle timeout, and a candidate closing
    the tab is the most likely way that happens.
    """
    # The candidate may stop their own interview — their page calls this on
    # beforeunload, and an agent left behind bills until its idle timeout.
    _caller(authorization, session_id,
            (auth.OPERATOR, auth.RECRUITER, auth.CANDIDATE))

    from src.agora import session as panel

    agents = panel.forget(session_id)
    if agents:
        await panel.stop(agents)
    return {"stopped": list(agents), "count": len(agents)}


@app.get("/session/{session_id}/state")
async def state(session_id: str, authorization: str = Header(default="")):
    """What the recruiter dashboard renders, and the quickest way to see
    whether the panel is behaving during a live test.

    Served to the candidate too, but redacted. Their page genuinely needs the
    live transcript and who is speaking; it must never see scores, flags or
    the question plan. Same endpoint, different view — which is what
    authorization means, as opposed to a door that is simply locked.
    """
    role = _caller(authorization, session_id,
                   (auth.OPERATOR, auth.RECRUITER, auth.CANDIDATE))

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

    if role == auth.CANDIDATE:
        # An ALLOWLIST, not a list of things to strip. A field added to
        # snapshot() later is invisible to the candidate by default rather
        # than exposed until somebody remembers to redact it — which is the
        # kind of leak nobody notices for months.
        snap = {k: snap[k] for k in _CANDIDATE_VISIBLE if k in snap}
        snap["transcript"] = [
            {"turn_id": t.turn_id, "speaker": t.speaker, "text": t.text}
            for t in s.turns.all()
        ]
        snap["redacted"] = True

    return snap


@app.post("/session/{session_id}/revoke")
async def revoke(session_id: str, authorization: str = Header(default="")):
    """Kill every outstanding token for this interview, immediately.

    For when one is known to have leaked — a join link forwarded to the wrong
    person, a screenshot with the URL in it. Raising the session's epoch
    invalidates every token minted before it without storing any of them.

    It kills the CALLER's token too, including a recruiter's own. That is the
    honest behaviour for a revoke-everything button; the operator key still
    works, and /setup issues a fresh pair.
    """
    _caller(authorization, session_id, (auth.OPERATOR, auth.RECRUITER))

    from src.state.session import get_session

    session = get_session(session_id)
    session.token_epoch += 1
    log.warning("session %s: all tokens revoked (epoch %d)",
                session_id, session.token_epoch)
    return {"revoked": True, "epoch": session.token_epoch}


@app.post("/session/{session_id}/finish")
async def finish(session_id: str, authorization: str = Header(default="")):
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
    # Not the candidate. This returns their marks, and it ends the interview —
    # either would let them stop at whatever moment their score peaked.
    _caller(authorization, session_id, (auth.OPERATOR, auth.RECRUITER))

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

    # The interview is over and the marks are out, so nobody needs access to it
    # any more. Raising the epoch kills every outstanding token — most
    # importantly the candidate's, which travelled in a URL and is therefore
    # the one most likely to have leaked.
    #
    # A fresh recruiter token comes back in the same response, so /finish stays
    # safe to call twice: whoever ended the interview keeps working, and only
    # the candidate's access ends.
    session.token_epoch += 1
    result["recruiter_token"] = auth.mint(
        session_id, auth.RECRUITER, session.token_epoch
    )

    log.info(
        "session %s finished: %.1f/%.0f over %d answers, %d evidence quotes; "
        "tokens revoked (epoch %d)",
        session_id, result["earned"], result["total"],
        result["answers"], len(result["evidence"]), session.token_epoch,
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
