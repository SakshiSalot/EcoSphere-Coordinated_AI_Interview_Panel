"""Run a real interview: personalise from a job advert, then join the panel.

    python -m scripts.panel --jd ml-engineer.txt --resume harsh.pdf
    python -m scripts.panel --roles technical,hiring_manager,customer
    python -m scripts.panel --list-inputs       # what is in inputs/
    python -m scripts.panel --list-roles        # who can sit on the panel
    python -m scripts.panel --stop-all          # emergency: kill every agent

Job adverts live in inputs/jd/ and CVs in inputs/resume/, so they are referred
to by bare filename. inputs/resume/ is gitignored — real CVs are personal data
and this repository goes public at submission.

IMPORTANT — the plan must be sent to the GATEWAY, not built here.

The gateway runs in its own process with its own memory. Building the question
plan inside this script produced a plan that was generated, printed, and then
thrown away: Agora asked the gateway, the gateway had never heard of it, and
the panel improvised generic questions while this terminal displayed a
beautiful personalised plan nobody was using.

Everything expensive still happens before anyone joins the channel, so no
agent-minute is spent waiting on Gemini.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

from src import config
from src.agora import channel as agora_channel
from src.agora import session as panel
from src.agora.tokens import build_token
from src.conductor.personas import active_roles, all_roles, persona, set_panel

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)

WEBDEMO = "https://webdemo.agora.io/basicVoiceCall/index.html"
INPUTS = Path(__file__).resolve().parent.parent / "inputs"


async def _enter() -> None:
    await asyncio.get_running_loop().run_in_executor(None, input)


# --- reading the inputs -------------------------------------------------


def _resolve(path: str, kind: str) -> Path | None:
    direct = Path(path)
    if direct.exists():
        return direct

    folder = INPUTS / kind
    if (folder / path).exists():
        return folder / path
    for ext in (".txt", ".md", ".pdf"):
        if (folder / (path + ext)).exists():
            return folder / (path + ext)

    available = sorted(f.name for f in folder.glob("*")
                       if f.is_file() and not f.name.startswith("."))
    print(f"  \033[33m{path!r} not found in inputs/{kind}/\033[0m")
    if available:
        print(f"  available: {', '.join(available)}")
    return None


def _read(path: str | None, kind: str = "jd") -> str:
    if not path:
        return ""
    p = _resolve(path, kind)
    if p is None:
        return ""

    if p.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader

            text = "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)
        except Exception as exc:
            print(f"  \033[33mcould not read {p.name}: {exc}\033[0m")
            return ""
        if not text.strip():
            print(f"  \033[33m{p.name} has no extractable text — is it a scan?\033[0m")
        return text

    return p.read_text(encoding="utf-8", errors="replace")


# --- talking to the gateway ---------------------------------------------


def _gateway(base: str) -> str:
    return (base or config.GATEWAY_PUBLIC_URL or "http://localhost:7860").rstrip("/")


def _preflight(base: str) -> bool:
    """The gateway Agora will call must be up before we spend a minute."""
    try:
        r = httpx.get(f"{base}/health", timeout=15.0)
        return r.status_code == 200
    except Exception as exc:
        print(f"\n  \033[31mThe gateway at {base} is not answering.\033[0m")
        print(f"  ({str(exc)[:120]})")
        print("  Start it with:  make gateway-live")
        print("  and the tunnel with:  make tunnel")
        return False


# We are the operator: this script runs on a machine that already holds the
# gateway key. Browsers get the per-session tokens /setup hands back instead —
# the shared key must never reach one.
def _operator() -> dict:
    return {"Authorization": f"Bearer {config.GATEWAY_SHARED_SECRET}"}


def _setup(base: str, channel: str, payload: dict) -> dict:
    r = httpx.post(f"{base}/session/{channel}/setup", json=payload,
                   headers=_operator(), timeout=180.0)
    r.raise_for_status()
    return r.json()


def _state(base: str, channel: str) -> dict:
    try:
        return httpx.get(f"{base}/session/{channel}/state",
                         headers=_operator(), timeout=20.0).json()
    except Exception:
        return {}


# --- the run ------------------------------------------------------------


async def main(a) -> int:
    if a.list_roles:
        for r in all_roles():
            p = persona(r)
            mark = "on by default" if p.get("default") else "optional"
            print(f"  {r:16} {p['name']:7} {p['title']:22} ({mark})")
        return 0

    if a.list_inputs:
        for kind in ("jd", "resume"):
            files = sorted(f.name for f in (INPUTS / kind).glob("*")
                           if f.is_file() and not f.name.startswith("."))
            print(f"  inputs/{kind}/ : {', '.join(files) if files else '(empty)'}")
        return 0

    config.require_agora()

    if a.stop_all:
        print(f"  stopped {await panel.stop_all()} agent(s)")
        return 0

    roles = set_panel([r.strip() for r in a.roles.split(",") if r.strip()]) \
        if a.roles else set_panel(active_roles(a.panel_size))

    print("\n  Panel:")
    for r in roles:
        p = persona(r)
        print(f"    {p['name']:<8} {p['title']}")

    base = _gateway(a.gateway)
    print(f"\n  Gateway: {base}")
    if not _preflight(base):
        return 1

    # --- 1. prepare the interview IN THE GATEWAY --------------------------
    jd_text = _read(a.jd, "jd")
    cv_text = _read(a.resume, "resume")
    print(f"  Job advert: {len(jd_text)} chars    CV: {len(cv_text)} chars")

    try:
        result = _setup(base, a.channel, {
            "job_title": a.title,
            "job_description": jd_text,
            "topics": [t.strip() for t in (a.topics or "").split(",") if t.strip()],
            "resume_text": cv_text,
            "candidate_name": a.name,
            "per_role": a.per_role,
            "roles": roles,
        })
    except Exception as exc:
        print(f"\n  \033[31mSetup failed: {str(exc)[:200]}\033[0m")
        return 1

    print()
    if result.get("personalised"):
        print(f"  \033[32mPrepared {result['planned']} questions from the advert "
              f"and the CV.\033[0m")
        for q in result.get("questions", [])[:5]:
            print(f"    [{persona(q['role'])['name']:<7} {q['difficulty']:6}] "
                  f"{q['text'][:76]}")
        if result["planned"] > 5:
            print(f"    … and {result['planned'] - 5} more")
    else:
        print(f"  \033[33mNOT personalised — {result.get('note')}\033[0m")
        print("  The panel will improvise generic questions.")
        if not a.force:
            print("\n  Stopping rather than spending minutes on a generic interview.")
            print("  Pass --force to run anyway.")
            return 1

    # --- 2. candidate joins first ----------------------------------------
    print()
    print("=" * 72)
    print("  STEP 1 — JOIN THE CHANNEL YOURSELF")
    print("=" * 72)
    print(f"  Open      : {WEBDEMO}")
    print(f"  App ID    : {config.AGORA_APP_ID}")
    print(f"  Channel   : {a.channel}")
    print(f"  Token     : {build_token(a.channel, 0) or '(App-ID-only mode)'}")
    print("=" * 72)
    print(f"\n  Then press Enter — {len(roles)} interviewers join only after you are in.\n")
    await _enter()

    who = await agora_channel.users_in(a.channel)
    if who.get("users"):
        print(f"  In the channel: {who['users']}\n")
    else:
        print("\n  \033[31mNobody is in that channel.\033[0m The browser did not join.")
        print("  Press Enter to continue anyway, or Ctrl-C to stop.\n")
        await _enter()

    # --- 3. the panel joins ----------------------------------------------
    agents: dict[str, str] = {}
    try:
        agents = await panel.start(a.channel, session_id=a.channel,
                                   roles=roles, idle_timeout=a.idle)
        print("\n" + "=" * 72)
        print(f"  THE PANEL IS IN: {', '.join(persona(r)['name'] for r in roles)}")
        print("=" * 72)
        print("  Only one speaks at a time. Give a strong technical answer that")
        print("  never mentions a customer — the floor should move.")
        print("  Interrupt any of them mid-sentence; they must stop.")
        print("=" * 72)
        print("\n  It ends on its own once the plan is covered — or press")
        print("  Enter to stop early.\n")

        # Whichever happens first: the panel finishes, or you stop it. Without
        # this the agents sit in the channel billing after the goodbye.
        async def wait_for_close() -> None:
            while True:
                await asyncio.sleep(3)
                if _state(base, a.channel).get("closed"):
                    print("\n  \033[32mThe panel has ended the interview.\033[0m")
                    return

        typed = asyncio.create_task(_enter())
        finished = asyncio.create_task(wait_for_close())
        done, pending = await asyncio.wait(
            {typed, finished}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()

        # Read the transcript from the GATEWAY, which is where it lives.
        s = _state(base, a.channel)
        print(f"\n  Turns          : {len(s.get('transcript', []))}")
        print(f"  Floor ended on : {s.get('floor_holder')}")
        print(f"  Difficulty     : {s.get('difficulty')}")
        asked = sum(1 for q in s.get("plan", []) if q.get("asked"))
        print(f"  Planned asked  : {asked}/{len(s.get('plan', []))}")
        for f in s.get("flags", []):
            print(f"  flag turn {f['turn_id']:>2}: {f['kind']} — {f['detail'][:48]}")
        print("\n  Transcript:")
        for t in s.get("transcript", []):
            who_said = ("candidate" if t["speaker"] == "candidate"
                        else persona(t["speaker"])["name"])
            print(f"    [{t['turn_id']:>2}] {who_said:<10} {t['text'][:68]}")
    finally:
        if agents:
            await panel.stop(agents)
        await asyncio.sleep(3)
        n = await panel.survivors()
        colour = "\033[32m" if n == 0 else "\033[31m"
        print(f"\n  {colour}Agents still running: {n}\033[0m  (must be 0)")
        if n:
            print("  Run:  make stop")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", default="interview")
    ap.add_argument("--jd", help="job advert: filename in inputs/jd/, or a path")
    ap.add_argument("--resume", help="CV: filename in inputs/resume/, or a path")
    ap.add_argument("--title", default="", help="job title")
    ap.add_argument("--name", default="", help="candidate name")
    ap.add_argument("--topics", default="", help="comma-separated topics")
    ap.add_argument("--per-role", type=int, default=3)
    ap.add_argument("--panel-size", type=int, default=config.PANEL_SIZE)
    ap.add_argument("--roles", default="",
                    help="explicit panel, e.g. technical,hiring_manager,customer")
    ap.add_argument("--gateway", default="",
                    help="gateway base URL (defaults to GATEWAY_PUBLIC_URL)")
    ap.add_argument("--idle", type=int, default=120)
    ap.add_argument("--force", action="store_true",
                    help="run even if personalisation failed")
    ap.add_argument("--list-roles", action="store_true")
    ap.add_argument("--list-inputs", action="store_true")
    ap.add_argument("--stop-all", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
