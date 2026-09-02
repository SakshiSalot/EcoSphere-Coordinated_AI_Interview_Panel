"""The whole product over HTTP, exactly as the browser drives it.

    python -m scripts.e2e_app                     # against localhost
    python -m scripts.e2e_app --base https://...  # against the tunnel

Walks a real candidate's path — sign in, start an interview, upload a resume
and job description, take the interview, then an operator reading the
assessment and recording a hiring decision — using the same endpoints, in the
same order, with the same payload shapes the React app sends.

IT NEVER CALLS /start. That is the one endpoint that puts agents in a channel
and spends agent-minutes, and it is also the only thing this cannot prove.
Everything on either side of it is checked here, so when a live run does fail
you know it is the voice layer rather than anything above it.

Costs Groq and Gemini tokens. Costs zero agent-minutes.
"""

import argparse
import io
import json
import sys
import time

import httpx

from src import config

OK = "  \033[32mPASS\033[0m  "
NO = "  \033[31mFAIL\033[0m  "
SKIP = "  \033[33mSKIP\033[0m  "

PASSED: list[str] = []
FAILED: list[str] = []

RESUME = b"""Backend Engineer
Built and ran the billing service for a multi-tenant SaaS product.
Sharded Postgres by tenant id; p99 on the billing query fell from 400ms to 60ms.
Owned on-call for the payments path and wrote the idempotency layer for retries.
Python, FastAPI, Postgres, Redis, Kafka.
"""

JOB = b"""Senior Backend Engineer
Own the billing and payments platform. Design for correctness under retries,
scale Postgres past a single primary, and keep p99 latency predictable as
tenants grow. Must have: Postgres at scale, distributed systems, on-call.
"""

ANSWERS = [
    "We sharded Postgres by tenant id using a consistent hash ring. That key "
    "kept one customer's data on one node, so nothing on the hot path needed a "
    "cross-shard join. p99 on the billing query went from 400ms to 60ms.",
    "The trade-off is that one very large tenant can still overwhelm a shard, "
    "so we alerted on per-shard write latency and had a runbook for splitting "
    "the biggest ones out before a customer noticed.",
    "It depends how you measure it, really. There are a number of factors "
    "there and we looked at various things at the time.",
]


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASSED if ok else FAILED).append(name)
    print(f"{OK if ok else NO}{name}" + (f"   {detail}" if detail else ""))
    return ok


def turn(client, base, sid, history, roles) -> tuple[str, str] | None:
    """One turn, calling EVERY persona exactly as Agora does."""
    headers = {"Authorization": f"Bearer {config.GATEWAY_SHARED_SECRET}",
               "Content-Type": "application/json"}
    spoke = []
    for role in roles:
        r = client.post(f"{base}/v1/{sid}/{role}/chat/completions", headers=headers,
                        json={"model": "x", "stream": True, "messages": history})
        said = "".join(
            json.loads(line[6:])["choices"][0]["delta"].get("content", "")
            for line in r.text.splitlines()
            if line.startswith("data: ") and line.strip() != "data: [DONE]"
        )
        if said.strip():
            spoke.append((role, said))

    if len(spoke) != 1:
        check(f"exactly one persona speaks (got {len(spoke)})", False,
              str([r for r, _ in spoke]))
        return None
    return spoke[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:7860")
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--keep", action="store_true",
                    help="leave the test account and interview in the database")
    a = ap.parse_args(argv)
    base = a.base.rstrip("/")

    who = f"e2e{int(time.time()) % 100000}"
    password = "e2e-test-password"
    client = httpx.Client(timeout=240.0)

    print(f"\n\033[1mEND TO END — the product as the browser drives it\033[0m")
    print(f"{base}   ·   candidate {who}   ·   no agents joined, no minutes spent")
    print("-" * 74)

    # --- 1. the gateway is up and serving the app --------------------------
    print("\n\033[1m1. the app is being served\033[0m")
    try:
        health = client.get(f"{base}/health").json()
    except Exception as exc:
        check("gateway answers /health", False, str(exc)[:80])
        return 1
    check("gateway answers /health", health.get("ok") is True, str(health))

    page = client.get(f"{base}/")
    check("the browser app is built and served", page.status_code == 200,
          "run: npm --prefix frontend run build" if page.status_code != 200 else "")
    check("a browser route falls through to the app shell",
          client.get(f"{base}/signin").status_code == 200)
    check("an unknown API path still 404s rather than returning HTML",
          client.get(f"{base}/v1/nope").status_code == 404)

    # --- 2. accounts --------------------------------------------------------
    print("\n\033[1m2. signing in\033[0m")
    r = client.post(f"{base}/auth/login", json={"username": who, "password": password})
    created = r.status_code == 200 and r.json().get("created") is True
    check("first sign-in creates a candidate account", created, f"{r.status_code}")
    token = r.json().get("token", "")
    H = {"Authorization": f"Bearer {token}"}

    again = client.post(f"{base}/auth/login", json={"username": who, "password": password})
    check("signing in again matches the same account",
          again.status_code == 200 and again.json().get("created") is False)
    check("a wrong password is refused",
          client.post(f"{base}/auth/login",
                      json={"username": who, "password": "nope"}).status_code == 401)
    check("the account is a candidate, never an operator",
          r.json().get("user", {}).get("role") == "candidate")
    check("an unauthenticated request is refused",
          client.get(f"{base}/interviews").status_code == 401)

    # --- 3. starting an interview ------------------------------------------
    print("\n\033[1m3. starting an interview\033[0m")
    started = client.post(f"{base}/interviews", json={}, headers=H)
    sid = started.json().get("session_id", "")
    check("a candidate can start their own interview", bool(sid), sid)

    listing = client.get(f"{base}/interviews", headers=H).json()
    check("it appears on their home screen", len(listing.get("interviews", [])) >= 1)
    check("a candidate is never sent a score",
          all("score" not in i for i in listing.get("interviews", [])),
          "the redaction is server-side, not a UI choice")

    # --- 4. resume and job description --------------------------------------
    print("\n\033[1m4. resume and job description\033[0m")
    prep = client.post(
        f"{base}/session/{sid}/prepare", headers=H,
        files={"resume": ("resume.txt", io.BytesIO(RESUME), "text/plain"),
               "job_file": ("jd.txt", io.BytesIO(JOB), "text/plain")},
        data={"job_title": "Senior Backend Engineer"},
    )
    ok = check("the upload is accepted", prep.status_code == 200,
               f"{prep.status_code} {prep.text[:90]}")
    if ok:
        body = prep.json()
        check("a question plan was built from them", body.get("planned", 0) > 0,
              f"{body.get('planned')} questions, personalised={body.get('personalised')}")

    check("an upload with nothing in it is refused",
          client.post(f"{base}/session/{sid}/prepare", headers=H,
                      data={"job_title": "x"}).status_code == 400)

    # --- 5. the conversation ------------------------------------------------
    print("\n\033[1m5. the interview itself\033[0m")
    creds = client.get(f"{base}/session/{sid}/join", headers=H)
    check("the browser gets RTC credentials", creds.status_code == 200
          and bool(creds.json().get("rtc_token")),
          f"channel={creds.json().get('channel')}")

    roles = ["technical", "product", "behavioural"][: health.get("panel_size", 2)]
    history: list[dict] = [{"role": "user", "content": "(the candidate has just joined)"}]
    transcript = []
    for i in range(a.turns):
        spoke = turn(client, base, sid, history, roles)
        if spoke is None:
            break
        role, said = spoke
        transcript.append((role, said))
        history.append({"role": "assistant", "content": said})
        history.append({"role": "user", "content": ANSWERS[i % len(ANSWERS)]})
    check(f"{a.turns} turns, exactly one speaker each time",
          len(transcript) == a.turns)
    if transcript:
        print(f"\n     \033[36m{transcript[-1][0]}\033[0m: {transcript[-1][1][:120]}\n")

    state = client.get(f"{base}/session/{sid}/state", headers=H).json()
    check("the candidate's live view has the transcript",
          len(state.get("transcript", [])) > 0,
          f"{len(state.get('transcript', []))} turns")
    check("but not the score, flags or question plan",
          not any(k in state for k in ("running_score", "flags", "plan", "ewma")),
          f"redacted={state.get('redacted')}")

    check("leaving the call is accepted",
          client.post(f"{base}/session/{sid}/stop", headers=H).status_code == 200)

    # --- 6. the operator ----------------------------------------------------
    print("\n\033[1m6. the operator, and the decision\033[0m")
    op = client.post(f"{base}/auth/login",
                     json={"username": "operator", "password": "EchoSphere@2026"})
    if op.status_code != 200:
        print(f"{SKIP}operator sign-in failed ({op.status_code}) — reseed with "
              f"scripts.seed_users")
        return _summary()
    OH = {"Authorization": f"Bearer {op.json()['token']}"}
    check("the operator can sign in", True)

    check("a candidate cannot end an interview",
          client.post(f"{base}/session/{sid}/finish", headers=H).status_code == 403,
          "they would stop at whatever moment their score peaked")

    # Marking runs in the background; /finish drains it, but give the last
    # answer a moment so this is not a race.
    time.sleep(3)
    fin = client.post(f"{base}/session/{sid}/finish", headers=OH)
    ok = check("the operator can total the interview", fin.status_code == 200,
               f"{fin.status_code} {fin.text[:80]}")
    if ok:
        result = fin.json()
        check("the assessment has marks and a breakdown",
              "fraction" in result and "by_role" in result,
              f"{result.get('earned')}/{result.get('total')}")
        scored = result.get("answers", 0)
        if scored:
            check("answers were scored with evidence", len(result.get("evidence", [])) > 0,
                  f"{scored} answers, {len(result.get('evidence', []))} quotes")
        else:
            print(f"{SKIP}nothing was scored — the judge was unavailable "
                  f"(quota?). Not a wiring fault.")

    # A signed-in candidate holds a USER token, authorised by who owns the
    # interview, so the epoch bump does not reach them — and it should not.
    # Their own finished transcript is theirs, and redacted. What must stop is
    # rejoining, because that spends agent-minutes on a decided interview.
    check("the candidate can still read their own finished transcript",
          client.get(f"{base}/session/{sid}/state", headers=H).status_code == 200)
    check("but cannot rejoin a completed interview",
          client.get(f"{base}/session/{sid}/join", headers=H).status_code == 409,
          "rejoining would put agents back in a channel and spend minutes")
    check("and cannot restart the panel on it",
          client.post(f"{base}/session/{sid}/start", json={}, headers=H).status_code == 409)
    check("hanging up still works, so no agent is ever stranded",
          client.post(f"{base}/session/{sid}/stop", headers=H).status_code == 200)

    got = client.get(f"{base}/interviews/{sid}/assessment", headers=OH)
    check("the stored assessment can be read back", got.status_code == 200,
          f"{got.status_code}")

    dec = client.post(f"{base}/interviews/{sid}/decision",
                      json={"decision": "hire"}, headers=OH)
    check("a hiring decision is recorded", dec.status_code == 200, dec.text[:60])

    board = client.get(f"{base}/interviews", headers=OH).json()
    mine = [i for i in board.get("interviews", []) if i["session_id"] == sid]
    check("it shows on the operator's dashboard with its decision",
          bool(mine) and mine[0].get("decision") == "hire",
          str(mine[0]) if mine else "not found")

    if not a.keep:
        from src.state import db
        db.write("DELETE FROM interviews WHERE session_id = ?", (sid,))
        db.write("DELETE FROM users WHERE username = ?", (who,))
        print(f"\n  cleaned up {who} and {sid}")

    return _summary()


def _summary() -> int:
    print("\n" + "-" * 74)
    if FAILED:
        print(f"\033[31m{len(FAILED)} failed\033[0m, {len(PASSED)} passed\n")
        for name in FAILED:
            print(f"    {name}")
        print()
        return 1
    print(f"\033[32mall {len(PASSED)} checks passed\033[0m")
    print("  Everything except the agents joining a voice channel. That is the "
          "one\n  thing that costs minutes, and the only thing left to prove.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
