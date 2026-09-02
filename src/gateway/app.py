"""The OpenAI-compatible gateway.

The single idea the whole architecture rests on: Agora will point an agent's
`llm.url` at any endpoint that speaks OpenAI's /chat/completions protocol. So
instead of writing an app that calls a model, we write a service that pretends
to be one, and Agora drives it.

That gives us three things at once — three agents sharing one brain, a
conductor that can silence a persona by returning an empty completion, and all
the interview logic in ordinary Python we can test with no voice stack running.
"""

import hmac
import logging
import os
import time

from fastapi import (
    FastAPI, File, Form, Header, HTTPException, Request, UploadFile,
)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from src import config
from src.contract import next_utterance
from src.gateway import auth, users
from src.gateway.sse import sse, sse_silent
from src.state import db

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


class Principal:
    """Who is making this request, once we have established it.

    `role` is what the endpoint checks. `user_id` is who to record a decision
    against — None for a machine caller, because Agora and our scripts are not
    people and nobody should be able to attribute a hiring decision to them.
    """

    __slots__ = ("role", "user_id", "kind")

    def __init__(self, role: str, user_id: int | None = None, kind: str = "user"):
        self.role, self.user_id, self.kind = role, user_id, kind


def _bearer(authorization: str) -> str:
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not presented:
        raise HTTPException(status_code=401, detail="unauthorized")
    return presented


def _for_user(user_id: int, role: str, session_id: str) -> Principal:
    """May this signed-in person touch this interview?

    THIS is the authorization, and it is a database question rather than
    anything inside the token: a user reaches the interviews they own or were
    assigned, and no others. A token that merely proved "you are an operator"
    would let every operator read every candidate.
    """
    row = db.interview(session_id)

    if row is None:
        # Nothing recorded yet — this is /setup creating one. Only an operator
        # may bring an interview into existence.
        if role == users.OPERATOR:
            return Principal(auth.OPERATOR, user_id)
        raise HTTPException(status_code=403, detail="forbidden")

    if role == users.OPERATOR and row["operator_id"] == user_id:
        return Principal(auth.OPERATOR, user_id)
    if role == users.CANDIDATE and row["candidate_id"] == user_id:
        return Principal(auth.CANDIDATE, user_id)

    log.warning("403 %s: user %d (%s) is not on this interview",
                session_id, user_id, role)
    raise HTTPException(status_code=403, detail="forbidden")


def _caller(
    authorization: str, session_id: str, allow: tuple[str, ...]
) -> Principal:
    """Authenticate, then authorize. Every session endpoint goes through here.

    Three ways in, tried in order:

      1. the shared key      — Agora and our own scripts, no person attached
      2. a user token        — a candidate or an operator who signed in
      3. a session token     — link-based entry, for a demo without accounts

    The response says only 401 or 403. Telling a caller *why* — expired, wrong
    session, not yours — tells an attacker which part of their guess was
    right; the detail goes to the log, where we can read it and they cannot.
    """
    presented = _bearer(authorization)

    # 1 — the machine key.
    if config.GATEWAY_SHARED_SECRET and hmac.compare_digest(
        presented, config.GATEWAY_SHARED_SECRET
    ):
        caller = Principal(auth.OPERATOR, None, kind="service")
    else:
        # 2 — a signed-in person.
        try:
            user_id, role = auth.verify_user(presented)
        except auth.AuthError:
            # 3 — a session token. Peek at the session rather than
            # get_session(), which CREATES one: an unauthenticated caller
            # hammering random ids would otherwise grow the session table
            # without limit.
            from src.state.session import all_sessions

            existing = all_sessions().get(session_id)
            epoch = existing.token_epoch if existing else 0
            try:
                token_role = auth.verify(presented, session_id, epoch)
            except auth.AuthError as exc:
                log.warning("401 %s: %s", session_id, exc)
                raise HTTPException(status_code=401, detail="unauthorized")
            # A link-based recruiter token carries operator-level access.
            caller = Principal(
                auth.CANDIDATE if token_role == auth.CANDIDATE else auth.OPERATOR,
                None, kind="link",
            )
        else:
            caller = _for_user(user_id, role, session_id)

    if caller.role not in allow:
        log.warning("403 %s: %s not permitted here", session_id, caller.role)
        raise HTTPException(status_code=403, detail="forbidden")
    return caller


def _not_finished(session_id: str) -> None:
    """Refuse to put agents back into an interview that is already assessed.

    /finish revokes session tokens by raising the epoch, but a signed-in
    candidate holds a USER token, which is authorised by who owns the interview
    and never looks at the epoch. So revocation alone does not stop them
    rejoining — and rejoining a finished interview spends agent-minutes on a
    conversation whose marks are already recorded and already decided on.

    Reading their own completed transcript stays allowed. That is theirs, it is
    redacted, and there is no reason to take it away.
    """
    row = db.interview(session_id)
    if row is not None and row["status"] == "ended":
        raise HTTPException(
            status_code=409,
            detail="This interview has already been completed.",
        )


def _signed_in(authorization: str) -> Principal:
    """A signed-in person, for endpoints that are not about one interview —
    the dashboard listing, /auth/me. No shared-key shortcut: these are about
    who you are, and a machine is nobody."""
    try:
        user_id, role = auth.verify_user(_bearer(authorization))
    except auth.AuthError:
        raise HTTPException(status_code=401, detail="unauthorized")
    if users.by_id(user_id) is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return Principal(role, user_id)


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


# --- accounts -----------------------------------------------------------

# A login endpoint with no throttle is the obvious way in: an attacker can try
# passwords as fast as the network allows, and scrypt only makes each attempt
# expensive for US. Counted per username rather than per IP, because the thing
# being protected is an account and an attacker can change IP far more easily
# than they can change which account they want.
_FAILURES: dict[str, list[float]] = {}
_MAX_FAILURES = 5
_LOCKOUT = 15 * 60


def _throttle(username: str) -> None:
    recent = [t for t in _FAILURES.get(username, []) if time.time() - t < _LOCKOUT]
    _FAILURES[username] = recent
    if len(recent) >= _MAX_FAILURES:
        log.warning("login locked out for %r after %d failures", username, len(recent))
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again in fifteen minutes.",
        )


@app.post("/auth/register")
async def register(request: Request, authorization: str = Header(default="")):
    """Create an account.

    Candidates sign themselves up — they are the people being interviewed and
    there is nobody to do it for them. An OPERATOR account can only be created
    by an existing operator or with the shared key, because an operator reads
    assessments and records hiring decisions; self-service there would let
    anyone award themselves the ability to see every candidate they own.
    """
    body = await request.json()
    role = (body.get("role") or users.CANDIDATE).strip()

    if role == users.OPERATOR:
        presented = (authorization or "").removeprefix("Bearer ").strip()
        privileged = bool(config.GATEWAY_SHARED_SECRET) and hmac.compare_digest(
            presented, config.GATEWAY_SHARED_SECRET
        )
        if not privileged:
            caller = _signed_in(authorization)
            if caller.role != users.OPERATOR:
                raise HTTPException(403, "only an operator can create an operator")

    try:
        user_id = users.create(
            body.get("username", ""), body.get("password", ""),
            role, body.get("full_name", ""),
        )
    except users.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    row = users.by_id(user_id)
    return {"user": users.public(row), "token": auth.mint_user(user_id, role)}


@app.post("/auth/login")
async def login(request: Request):
    """Sign in, creating a candidate account on first use.

    A candidate who has never signed in before gets an account with whatever
    credentials they type; from then on those credentials must match. Nobody
    hands an interview candidate a username in advance, and a separate sign-up
    step is one more thing to get wrong before an interview that is already
    stressful.

    OPERATOR accounts are never created this way — they are seeded from a
    terminal. An operator reads assessments and records hiring decisions, so
    the accounts that can do that must be deliberate.

    THE TRADE, stated plainly: an unknown username now succeeds while a known
    one with the wrong password fails, so account existence is discoverable.
    On a hiring system that reveals who is interviewing. The fix, when it
    matters more than convenience, is for the operator to create the candidate
    account when they set the interview up, and to turn this off.
    """
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password", "")
    _throttle(username)

    row = users.authenticate(username, password)

    if row is None and users.by_username(username) is None:
        # First time. Create it, and tell the browser so it can SAY so — a
        # mistyped username would otherwise silently produce a second, empty
        # account and the candidate would wonder where their interview went.
        try:
            user_id = users.create(username, password, users.CANDIDATE,
                                   body.get("full_name", ""))
        except users.UserError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = users.by_id(user_id)
        log.info("first sign-in: created candidate %r", row["username"])
        return {
            "user": users.public(row),
            "token": auth.mint_user(row["id"], row["role"]),
            "expires_in": auth.USER_TTL,
            "created": True,
        }

    if row is None:
        _FAILURES.setdefault(username, []).append(time.time())
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    _FAILURES.pop(username, None)
    log.info("login: %s (%s)", row["username"], row["role"])
    return {
        "user": users.public(row),
        "token": auth.mint_user(row["id"], row["role"]),
        "expires_in": auth.USER_TTL,
        "created": False,
    }


@app.get("/auth/me")
async def me(authorization: str = Header(default="")):
    """Who this token belongs to. The page calls it on load to decide whether
    a stored token is still good."""
    caller = _signed_in(authorization)
    return {"user": users.public(users.by_id(caller.user_id))}


# --- what each person can see ------------------------------------------


@app.get("/interviews")
async def my_interviews(authorization: str = Header(default="")):
    """The list behind both home screens.

    An operator sees the interviews they set up, with scores, so they can
    decide. A candidate sees only their own, and never a score — the marks are
    for the person making the hiring decision, not for the person being
    assessed to argue with.
    """
    caller = _signed_in(authorization)

    if caller.role == users.OPERATOR:
        return {"role": caller.role, "interviews": [
            {
                "session_id": r["session_id"],
                "candidate": r["candidate_name"] or r["candidate_username"] or "—",
                "job_title": r["job_title"],
                "status": r["status"],
                "score": r["score"],
                "decision": r["decision"],
                "created_at": r["created_at"],
            }
            for r in db.interviews_for_operator(caller.user_id)
        ]}

    return {"role": caller.role, "interviews": [
        {
            "session_id": r["session_id"],
            "job_title": r["job_title"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in db.interviews_for_candidate(caller.user_id)
    ]}


@app.post("/interviews")
async def start_interview(request: Request, authorization: str = Header(default="")):
    """A candidate starting their own interview.

    Without this a candidate who signs up lands on an empty list with nothing
    to press — the only route in would be an operator creating one for them
    first, which is a dead end for anyone arriving on their own.

    The interview is assigned to an operator so it reaches somebody's dashboard
    to be decided on. With a single operator that is unambiguous; a real
    deployment would route by job or team, which is a query change here and
    nothing else.
    """
    import secrets

    caller = _signed_in(authorization)
    if caller.role != users.CANDIDATE:
        # Operators create interviews through /setup, which also builds the
        # question plan and mints the session tokens.
        raise HTTPException(403, "operators create interviews through setup")

    body = await request.json() if await request.body() else {}
    # Random, not sequential: the session id is also the Agora channel name and
    # appears in URLs, and a guessable one is one fewer thing an attacker needs.
    session_id = f"iv-{secrets.token_urlsafe(9)}"

    owner = db.one("SELECT id FROM users WHERE role = 'operator' ORDER BY id LIMIT 1")
    db.create_interview(
        session_id=session_id,
        candidate_id=caller.user_id,
        operator_id=owner["id"] if owner else None,
        job_title=(body.get("job_title") or "").strip(),
    )
    log.info("candidate %d started interview %s", caller.user_id, session_id)
    return {"session_id": session_id}


@app.get("/interviews/{session_id}/assessment")
async def assessment(session_id: str, authorization: str = Header(default="")):
    """The stored report: marks per interviewer, and every quote behind them.

    Operator only. This is the evidence a hiring decision rests on.
    """
    _caller(authorization, session_id, (auth.OPERATOR,))

    row = db.interview(session_id)
    if row is None:
        raise HTTPException(404, f"no interview {session_id!r}")

    import json as _json

    # The transcript comes back whether or not the marking finished.
    #
    # Refusing the whole record because a number is missing hid the
    # conversation itself: an interview that ended without being totalled
    # showed the operator a 404, even though the evidence they most need to
    # read was sitting in the database.
    transcript = db.load_transcript(session_id)
    marked = bool(row["assessment_json"])

    return {
        "session_id": session_id,
        "job_title": row["job_title"],
        "status": row["status"],
        "decision": row["decision"],
        "transcript": transcript,
        "marked": marked,
        "assessment": _json.loads(row["assessment_json"]) if marked else None,
        "note": None if marked else (
            "Not totalled yet — use Finish to mark this interview."
            if transcript else
            "No transcript recorded. The interview ended before anyone spoke, "
            "or the gateway restarted mid-call."
        ),
    }


@app.delete("/interviews/{session_id}")
async def delete_interview(session_id: str, authorization: str = Header(default="")):
    """Remove an interview. Operator only.

    For clearing test runs and abandoned sessions — a dashboard full of debris
    is a dashboard nobody reads.

    Two refusals, both deliberate:

      * An interview carrying a hiring DECISION is not deletable. That record
        is the reason a person was hired or not, and deleting it quietly is
        exactly what an audit exists to prevent.
      * Agents are stopped BEFORE the row goes. Deleting the record of a live
        interview would leave them in the channel billing with nothing left
        that knows they are there.
    """
    _caller(authorization, session_id, (auth.OPERATOR,))

    row = db.interview(session_id)
    if row is None:
        raise HTTPException(404, f"no interview {session_id!r}")

    if row["decision"]:
        raise HTTPException(
            409,
            "This interview has a hiring decision recorded against it and "
            "cannot be deleted.",
        )

    from src.agora import session as panel
    from src.state.session import drop_session

    agents = panel.forget(session_id)
    if agents:
        await panel.stop(agents)
        log.info("session %s: stopped %d agents before deleting", session_id, len(agents))

    drop_session(session_id)
    db.delete_interview(session_id)
    log.info("session %s deleted", session_id)
    return {"deleted": session_id, "agents_stopped": len(agents)}


@app.post("/interviews/{session_id}/decision")
async def decide(
    session_id: str, request: Request, authorization: str = Header(default="")
):
    """Record the hiring decision — the human in the loop, by name.

    Stored with who made it and when. A system that recommends a hire and
    cannot say which person agreed is not a human in the loop, it is a rubber
    stamp with extra steps.
    """
    caller = _caller(authorization, session_id, (auth.OPERATOR,))
    if caller.user_id is None:
        raise HTTPException(403, "a hiring decision must be made by a person")

    body = await request.json()
    decision = (body.get("decision") or "").strip()
    if decision not in ("hire", "no_hire", "maybe"):
        raise HTTPException(400, "decision must be hire, no_hire or maybe")

    db.record_decision(session_id, decision, caller.user_id)
    log.info("session %s: %s by user %d", session_id, decision, caller.user_id)
    return {"session_id": session_id, "decision": decision}


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
    caller = _caller(authorization, session_id, (auth.OPERATOR,))
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
    # Record it, so it appears on the operator's dashboard and the candidate's
    # home screen, and so authorization for every later request on this
    # session has an owner to check against.
    candidate = users.by_username(body.get("candidate_username", ""))
    db.create_interview(
        session_id=session_id,
        candidate_id=candidate["id"] if candidate else None,
        operator_id=caller.user_id,
        job_title=body.get("job_title", ""),
    )
    result["candidate"] = users.public(candidate)

    result.update(auth.tokens_for(session_id, session.token_epoch))
    result["roles"] = session.roles or None
    log.info(
        "session %s prepared: %d questions, personalised=%s, panel=%s",
        session_id, result["planned"], result["personalised"],
        session.roles or "(default)",
    )
    return result


# A resume is a document, not a novel. Capped because an uploaded file is
# read into memory before anything looks at it, and "it crashed on a 400MB
# PDF" is a denial of service with extra steps.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARS = 20_000
ALLOWED_SUFFIXES = (".pdf", ".txt", ".md", ".rtf")


def _text_from_upload(upload, raw: bytes) -> str:
    """Whatever text is in this file, or a 400 explaining why not."""
    name = (upload.filename or "").lower()
    if not name.endswith(ALLOWED_SUFFIXES):
        raise HTTPException(
            400, "Upload a PDF or a plain text file (.pdf, .txt, .md, .rtf)."
        )

    if name.endswith(".pdf"):
        try:
            import io

            from pypdf import PdfReader

            pages = PdfReader(io.BytesIO(raw)).pages
            text = "\n".join(p.extract_text() or "" for p in pages)
        except Exception as exc:  # noqa: BLE001 — any unreadable PDF is a 400
            log.warning("could not read %r: %s", upload.filename, exc)
            raise HTTPException(
                400, "That PDF could not be read. Try exporting it again, or paste the text."
            )
    else:
        text = raw.decode("utf-8", errors="replace")

    text = text.strip()
    if not text:
        # A scan with no text layer looks identical to a working PDF until you
        # try to read it, and silently interviewing against an empty resume is
        # worse than refusing.
        raise HTTPException(
            400,
            "No text could be extracted — the file may be a scan. "
            "Paste the text instead.",
        )
    return text[:MAX_EXTRACTED_CHARS]


@app.post("/session/{session_id}/prepare")
async def prepare(
    session_id: str,
    resume: UploadFile = File(default=None),
    job_file: UploadFile = File(default=None),
    job_description: str = Form(default=""),
    job_title: str = Form(default=""),
    authorization: str = Header(default=""),
):
    """Personalise the interview from a resume and a job description.

    The CANDIDATE may do this for their own interview. That is the point: the
    panel asks about the role they applied for and the work they have actually
    done, rather than a generic backend quiz — which is exactly what a
    candidate notices and a judge discounts.

    Everything expensive happens here, before anyone joins the channel.
    Generating questions mid-conversation would add seconds to a live turn.
    """
    caller = _caller(authorization, session_id, (auth.OPERATOR, auth.CANDIDATE))

    from src.intake.plan import setup_session
    from src.state.session import all_sessions, reset_session

    resume_text = ""
    if resume is not None and resume.filename:
        raw = await resume.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "That file is too large — 5 MB maximum.")
        resume_text = _text_from_upload(resume, raw)

    description = (job_description or "").strip()
    if job_file is not None and job_file.filename:
        raw = await job_file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "That file is too large — 5 MB maximum.")
        description = _text_from_upload(job_file, raw)

    if not resume_text and not description:
        raise HTTPException(
            400, "Add a resume or a job description — otherwise there is nothing to tailor."
        )

    # Reset is right here: this runs before the interview starts, and a plan
    # built on top of a half-finished session would interleave old turns with
    # new questions.
    previous = all_sessions().get(session_id)
    session = reset_session(session_id)
    session.token_epoch = (previous.token_epoch if previous else 0)

    row = db.interview(session_id)
    result = setup_session(
        session,
        job_title=job_title.strip() or (row["job_title"] if row else ""),
        job_description=description,
        resume_text=resume_text,
        candidate_name=(users.public(users.by_id(caller.user_id)).get("full_name", "")
                        if caller.user_id else ""),
    )

    if job_title.strip():
        db.write("UPDATE interviews SET job_title = ? WHERE session_id = ?",
                 (job_title.strip(), session_id))

    log.info(
        "session %s prepared by %s: %d questions, resume=%s, jd=%s",
        session_id, caller.role, result.get("planned", 0),
        bool(resume_text), bool(description),
    )
    return {
        **result,
        "resume_chars": len(resume_text),
        "job_chars": len(description),
    }


@app.get("/session/{session_id}/join")
async def join_credentials(session_id: str, authorization: str = Header(default="")):
    """What the browser needs to enter the voice channel — WITHOUT the panel.

    Separate from /start deliberately, and the separation is the whole point.
    An agent speaks its greeting the moment it joins, so the panel must arrive
    after the candidate is already in the room; otherwise the spoken AI
    disclosure is delivered to an empty channel and never heard, and the agents
    bill while they wait.

    The browser cannot join before /start unless it can get these credentials
    without it — which is what this is. It mints an RTC token and starts
    nothing, so it costs no agent-minutes.
    """
    _caller(authorization, session_id, (auth.OPERATOR, auth.CANDIDATE))
    _not_finished(session_id)

    from src.agora.agent import CANDIDATE_UID
    from src.agora.tokens import build_token

    # A FIXED uid, and a token bound to it.
    #
    # Joining on uid 0 let Agora assign a random one, which meant the agents
    # could not name the candidate in `remote_rtc_uids` and had to subscribe
    # to "*" — so every agent also subscribed to every other agent, heard
    # their speech, transcribed it, and answered it as if the candidate had
    # spoken. Naming the uid here is what lets the panel listen to the
    # candidate and to nobody else.
    return {
        "channel": session_id,
        "app_id": config.AGORA_APP_ID,
        "uid": CANDIDATE_UID,
        "rtc_token": build_token(session_id, CANDIDATE_UID),
    }


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
    _caller(authorization, session_id, (auth.OPERATOR, auth.CANDIDATE))
    _not_finished(session_id)

    from src.agora import session as panel
    from src.agora.tokens import build_token
    from src.conductor.personas import persona
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
        # Names and titles come from here rather than being hardcoded in the
        # browser: personas.yaml is the one place a persona is defined, and a
        # page that duplicates it shows "Priya" after somebody renames her.
        "panel": [
            {"role": r, "name": persona(r)["name"], "title": persona(r)["title"]}
            for r in agents
        ],
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
    #
    # Deliberately NOT guarded by _not_finished: stopping is cleanup, and
    # refusing it on a finished interview is the one way to strand an agent in
    # a channel billing until it times out. Always let somebody hang up.
    _caller(authorization, session_id, (auth.OPERATOR, auth.CANDIDATE))

    from src.agora import session as panel

    agents = panel.forget(session_id)
    if agents:
        await panel.stop(agents)

    # Write the conversation down before the only copy disappears.
    #
    # The transcript lived in memory alone, so closing the tab or restarting
    # the gateway destroyed it and the operator opened an empty record. It is
    # the artefact the whole product exists to produce; the marks can be
    # recomputed from it, but nothing can recover it.
    from src.state.db import save_transcript
    from src.state.session import get_session

    session = get_session(session_id)
    turns = [
        {"turn_id": t.turn_id, "speaker": t.speaker, "text": t.text,
         "difficulty": t.difficulty, "at": t.started_at}
        for t in session.turns.all()
    ]
    if turns:
        save_transcript(session_id, turns)

    # Hanging up is not the same as finishing.
    #
    # The panel decides when the interview is over — it covers its plan, says
    # goodbye, and sets `closed`. Only then is the record complete and marked.
    # A dropped connection or a closed tab must leave the interview rejoinable,
    # or a candidate whose wifi blinks is locked out of their own assessment.
    completed = session.closed
    result = None
    if completed:
        db.set_status(session_id, "ended")
        result = _total_interview(session_id)

    log.info(
        "session %s: %d turns saved, %s",
        session_id, len(turns),
        "completed and marked" if completed else "still open — can rejoin",
    )

    return {
        "stopped": list(agents),
        "count": len(agents),
        "turns_saved": len(turns),
        "completed": completed,
        "marked": result is not None,
    }


@app.get("/session/{session_id}/state")
async def state(session_id: str, authorization: str = Header(default="")):
    """What the recruiter dashboard renders, and the quickest way to see
    whether the panel is behaving during a live test.

    Served to the candidate too, but redacted. Their page genuinely needs the
    live transcript and who is speaking; it must never see scores, flags or
    the question plan. Same endpoint, different view — which is what
    authorization means, as opposed to a door that is simply locked.
    """
    caller = _caller(authorization, session_id,
                     (auth.OPERATOR, auth.CANDIDATE))

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

    if caller.role == auth.CANDIDATE:
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
    _caller(authorization, session_id, (auth.OPERATOR,))

    from src.state.session import get_session

    session = get_session(session_id)
    session.token_epoch += 1
    log.warning("session %s: all tokens revoked (epoch %d)",
                session_id, session.token_epoch)
    return {"revoked": True, "epoch": session.token_epoch}


def _total_interview(session_id: str) -> dict | None:
    """Mark the interview and store the assessment. Returns None if there is
    nothing to mark.

    Shared by the automatic path (the call ended) and the operator's manual
    Finish. Safe to run twice — coverage is kept as a proportion when marks are
    rescaled, so the numbers do not move.
    """
    from src.analysis import pipeline
    from src.state.session import get_session

    session = get_session(session_id)
    if not len(session.turns):
        return None

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
    db.save_assessment(session_id, result)
    log.info(
        "session %s marked: %.1f/%.0f over %d answers, %d evidence quotes",
        session_id, result["earned"], result["total"],
        result["answers"], len(result["evidence"]),
    )
    return result


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
    _caller(authorization, session_id, (auth.OPERATOR,))

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
    # Persist it. The gateway holds interviews in memory; a report that
    # vanishes when the process restarts is not a report, and an operator will
    # read this days after the interview happened.
    db.save_assessment(session_id, result)

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


# --- the browser app ----------------------------------------------------
# Registered LAST on purpose. FastAPI matches routes in the order they are
# declared, so a catch-all placed earlier would swallow /auth, /session and
# the completions endpoint and hand Agora a page of HTML.

_DIST = config.ROOT / "frontend" / "dist"

if (_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

# Paths that belong to the API. A request to one of these that matched nothing
# is a genuine 404, and should say so rather than returning the app shell —
# a fetch that silently receives HTML instead of JSON is a miserable debug.
_API_PREFIXES = ("auth", "session", "interviews", "health", "v1", "assets")


@app.get("/{full_path:path}")
async def spa(full_path: str):
    """Serve the single-page app, and let it do its own routing.

    Every browser route — /login, /interview/x, /assessment/x — returns the
    same index.html, because the paths only mean something to the router
    inside the page. Without this, refreshing on /interview/x asks the server
    for a file that was never there and gets a 404.
    """
    if full_path.split("/", 1)[0] in _API_PREFIXES:
        raise HTTPException(status_code=404, detail="not found")

    index = _DIST / "index.html"
    if not index.is_file():
        raise HTTPException(
            status_code=503,
            detail="The browser app has not been built. Run: npm --prefix frontend run build",
        )
    return FileResponse(index)


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
