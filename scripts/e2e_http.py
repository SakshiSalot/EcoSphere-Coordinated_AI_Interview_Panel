"""End-to-end through the gateway's HTTP API only — no microphone.

    python -m scripts.e2e_http

Drives the exact calls the browser will make, and the exact calls Agora makes
on every turn, then checks what actually landed in the session:

    setup   -> questions generated from a real CV and job advert
    start   -> all three interviewers join the channel  (a few agent-seconds)
    stop    -> and leave again immediately
    turns   -> every persona is asked, as Agora does; exactly one may answer
    state   -> rubrics built, answers scored, flags raised
    finish  -> the assessment totals

The one thing it cannot test is audio: whether a human can interrupt a
persona, and whether the voices are distinguishable. That needs a microphone.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

from src import config

ANSWERS = [
    # strong technical, and deliberately no user, customer or cost named
    "I built a retrieval pipeline over scanned and handwritten PDFs. Dense "
    "embeddings in ChromaDB with a hybrid lexical re-rank on top, and an "
    "adaptive OCR stage that switches between Tesseract and a vision model "
    "based on character count and image coverage. Every answer cites its "
    "source page.",
    # a follow-up with a number in it
    "I measured it by holding out a set of two hundred questions with known "
    "source pages, then tracking whether the cited page was correct. We were "
    "at about eighty-four percent before the re-ranker and ninety-one after.",
    # a dodge: fluent, technical-sounding, says nothing checkable
    "It depends how you measure it, really. There are a number of factors and "
    "you generally want to align on what the stakeholders consider acceptable "
    "before committing to a specific threshold.",
    # a specific behavioural answer
    "A reviewer thought the adaptive OCR stage was over-engineering. I asked "
    "him to run the fixed-Tesseract path on the handwritten set first. It "
    "scored under forty percent, so we kept the switch, but I should have "
    "shown him that number before writing the code rather than after.",
]


def _cv() -> str:
    from pypdf import PdfReader

    p = Path("inputs/resume/harsh.pdf")
    if not p.exists():
        return "Four years ML. Built a RAG pipeline with ChromaDB and re-ranking."
    return "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)


def _jd() -> str:
    p = Path("inputs/jd/ml-engineer.txt")
    return p.read_text() if p.exists() else "Machine Learning Engineer, applied AI."


PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    mark = "\033[32mPASS\033[0m" if ok else "\033[31mFAIL\033[0m"
    print(f"  {mark}  {name}" + (f"   {detail}" if detail and not ok else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=config.GATEWAY_PUBLIC_URL or "http://localhost:7860")
    ap.add_argument("--session", default=f"e2e-{int(time.time())}")
    ap.add_argument("--roles", default="technical,product,behavioural")
    ap.add_argument("--pace", type=float, default=6.0)
    ap.add_argument("--skip-join", action="store_true",
                    help="do not spend agent-seconds proving the panel joins")
    a = ap.parse_args()

    base, sid = a.base.rstrip("/"), a.session
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    # This harness is an operator: it runs where the gateway key already is.
    # Set once on the client so every session call carries it — the session
    # endpoints reject an unauthenticated caller, which is the point.
    c = httpx.Client(
        timeout=240.0,
        headers={"Authorization": f"Bearer {config.GATEWAY_SHARED_SECRET}"},
    )

    print(f"\n\033[1mEND TO END VIA HTTP\033[0m  {base}  session={sid}")
    print("─" * 74)

    # 1 ── questions from the CV and the advert -------------------------
    print("\n\033[1m1. setup — questions from a real CV and job advert\033[0m")
    r = c.post(f"{base}/session/{sid}/setup", json={
        "job_title": "Machine Learning Engineer",
        "job_description": _jd(), "resume_text": _cv(),
        "candidate_name": "Harsh Raj", "per_role": 2, "roles": roles,
    }).json()
    check("the interview is personalised", r.get("personalised") is True, str(r)[:120])
    check("questions were planned for every persona",
          r.get("planned", 0) >= len(roles), f"planned={r.get('planned')}")
    check("the panel is the one we asked for", r.get("roles") == roles, str(r.get("roles")))
    for q in r.get("questions", [])[:3]:
        print(f"        [{q['role']:<12} {q['difficulty']:6}] {q['text'][:66]}")

    # 2 ── the panel joins the channel ----------------------------------
    if not a.skip_join:
        print(f"\n\033[1m2. start — do all {len(roles)} interviewers join?\033[0m")
        j = c.post(f"{base}/session/{sid}/start", json={"idle_timeout": 30})
        ok = j.status_code == 200
        agents = j.json().get("agents", {}) if ok else {}
        check(f"all {len(roles)} agents joined", len(agents) == len(roles),
              j.text[:140])
        check("the browser gets an RTC token it can join the channel with",
              bool(j.json().get("rtc_token")) if ok else False)
        s = c.post(f"{base}/session/{sid}/stop").json()
        check("and every one of them leaves again", s.get("count") == len(agents))

    # 3 ── the conversation, exactly as Agora drives it -----------------
    print("\n\033[1m3. turns — every persona asked, exactly one may answer\033[0m")
    history: list[dict] = []
    spoke_log: list[str] = []
    headers = {"Authorization": f"Bearer {config.GATEWAY_SHARED_SECRET}"}

    for i, answer in enumerate([None] + ANSWERS):
        if answer is not None:
            history.append({"role": "user", "content": answer})
        elif not history:
            history.append({"role": "user", "content": "(the candidate has just joined)"})

        spoke = []
        for role in roles:
            resp = c.post(f"{base}/v1/{sid}/{role}/chat/completions",
                          headers=headers, json={"messages": history})
            text = "".join(
                json.loads(l[6:])["choices"][0]["delta"].get("content") or ""
                for l in resp.text.splitlines()
                if l.startswith("data: ") and "[DONE]" not in l
            )
            if text.strip():
                spoke.append((role, text))

        if len(spoke) != 1:
            check(f"turn {i + 1}: exactly one persona speaks", False,
                  f"{len(spoke)} spoke: {[s[0] for s in spoke]}")
            break
        role, said = spoke[0]
        spoke_log.append(role)
        history.append({"role": "assistant", "content": said})
        print(f"        \033[36m{role:<12}\033[0m {said[:78]}")
        time.sleep(a.pace)

    check("exactly one persona spoke on every turn", not FAIL or "turn" not in FAIL[-1])
    check("more than one persona was heard from",
          len(set(spoke_log)) > 1, f"only {set(spoke_log)}")
    check("the AI disclosure opens the interview",
          "an ai" in (history[1]["content"].lower() if len(history) > 1 else ""))

    # 4 ── did the marking engine actually run? -------------------------
    print("\n\033[1m4. state — rubrics, scores and flags\033[0m")
    time.sleep(12)  # marking is asynchronous by design
    st = c.get(f"{base}/session/{sid}/state").json()
    print(f"        turns={st.get('turns')}  difficulty={st.get('difficulty')}  "
          f"running_score={st.get('running_score')}")
    for f in st.get("flags", []):
        print(f"        flag turn {f['turn_id']:>2}  {f['kind']}")
    check("the transcript was recorded", st.get("turns", 0) >= len(ANSWERS))
    check("at least one answer was scored", (st.get("running_score") or 0) > 0,
          f"running_score={st.get('running_score')}")
    check("the dodge was flagged",
          any(f["kind"] == "vague" for f in st.get("flags", [])),
          str([f["kind"] for f in st.get("flags", [])]))

    # 5 ── the assessment ------------------------------------------------
    print("\n\033[1m5. finish — the assessment totals\033[0m")
    fin = c.post(f"{base}/session/{sid}/finish")
    ok = fin.status_code == 200
    body = fin.json() if ok else {}
    check("the assessment is produced", ok, fin.text[:160])
    if ok:
        print("        " + json.dumps(body)[:300])

    print("\n" + "─" * 74)
    if FAIL:
        print(f"\033[31m{len(FAIL)} failed\033[0m, {len(PASS)} passed")
        for f in FAIL:
            print(f"    {f}")
        return 1
    print(f"\033[32mall {len(PASS)} passed\033[0m — everything except audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
