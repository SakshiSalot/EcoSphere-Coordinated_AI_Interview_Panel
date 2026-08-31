"""The mock harness — a full interview with Agora switched off.

This is the highest-leverage tool in the project. It impersonates Agora
exactly: every turn it calls *every* persona, not just the one holding the
floor, because that is what Agora does. The "exactly one speaks" assertion
then catches a broken conductor immediately, rather than during a live
session in front of a judge.

    python -m src.mock.replay                    # AI candidate, 8 turns
    python -m src.mock.replay --persona waffler
    python -m src.mock.replay --turns 12 --plant contradiction vague

Costs zero agent-minutes.
"""

import argparse
import logging
import sys
import time

from src.conductor.personas import active_roles
from src.contract import next_utterance
from src.mock.candidate import DEFAULT_RESUME, AICandidate
from src.state.models import CandidateProfile, JobSpec
from src.state.session import SessionState, reset_session

log = logging.getLogger("replay")

DEFAULT_JOB = JobSpec(
    title="Senior Backend Engineer",
    description=(
        "Own the billing and payments platform for a multi-tenant SaaS product. "
        "Design for correctness under retries, scale Postgres past a single "
        "primary, and keep p99 latency predictable as tenants grow."
    ),
    topics=["database scaling", "idempotency", "observability", "incident response"],
    must_haves=["Postgres at scale", "distributed systems", "on-call ownership"],
)

RESET = "\033[0m"
COLOURS = {
    "candidate": "\033[97m",
    "technical": "\033[36m",
    "product": "\033[33m",
    "behavioural": "\033[35m",
}


class OneSpeakerViolation(AssertionError):
    """More than one persona tried to speak on the same turn. This is the most
    embarrassing possible demo failure and the easiest to introduce by
    accident when editing handoff rules."""


def run(
    turns: int = 8,
    persona: str = "strong",
    plant: list[str] | None = None,
    session_id: str = "mock",
    quiet: bool = False,
    pace: float = 4.0,
) -> SessionState:
    session = reset_session(session_id)
    session.job = DEFAULT_JOB
    session.candidate = CandidateProfile(
        name="Test Candidate", resume_text=DEFAULT_RESUME
    )

    candidate = AICandidate(
        persona=persona,
        job_title=session.job.title,
        plant=plant or ["no_business_framing", "vague", "contradiction"],
    )

    roles = active_roles()
    history: list[dict] = []
    latencies: list[float] = []

    _banner(f"panel: {', '.join(roles)}  ·  candidate: {persona}  ·  "
            f"planting: {', '.join(candidate.plant)}")

    for i in range(turns):
        # The harness runs far faster than a real interview, where the
        # candidate spends thirty seconds thinking. Without pacing it burns
        # the free tokens-per-minute allowance in about ninety seconds and
        # every provider 429s — a limit we would never hit live.
        if i and pace:
            time.sleep(pace)

        # --- Agora calls EVERY agent, not just the one that should answer ---
        started = time.perf_counter()
        spoke: list[tuple[str, str]] = []

        try:
            for role in roles:
                stream = next_utterance(session_id, role, history)
                if stream is None:
                    continue
                spoke.append((role, "".join(stream)))
        except RuntimeError as exc:
            # Providers exhausted. A short interview still tells us something,
            # so report what we have rather than losing the whole run.
            print(f"\n\033[33mstopped after {len(session.turns)} turns: "
                  f"{str(exc)[:100]}\033[0m")
            break

        latencies.append((time.perf_counter() - started) * 1000)

        if len(spoke) != 1:
            raise OneSpeakerViolation(
                f"{len(spoke)} personas tried to speak on turn "
                f"{len(session.turns)}: {[r for r, _ in spoke]}"
            )

        role, said = spoke[0]
        if not quiet:
            _say(role, said, session)
        history.append({"role": "assistant", "content": said})

        try:
            answer = candidate.answer(said)
        except RuntimeError as exc:
            print(f"\n\033[33mcandidate stopped: {str(exc)[:100]}\033[0m")
            break

        if not quiet:
            _say("candidate", answer.text, session)
        history.append({"role": "user", "content": answer.text})

    _summary(session, candidate, latencies)
    return session


def _banner(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m")
    print("─" * 78)


def _say(speaker: str, text: str, session: SessionState) -> None:
    colour = COLOURS.get(speaker, "")
    name = speaker if speaker == "candidate" else f"{speaker} ({session.difficulty})"
    print(f"\n{colour}\033[1m{name}\033[0m{colour}")
    for line in _wrap(text, 74):
        print(f"  {line}")
    print(RESET, end="")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, buf = text.split(), [], ""
    for w in words:
        if buf and len(buf) + len(w) + 1 > width:
            lines.append(buf)
            buf = w
        else:
            buf = f"{buf} {w}".strip()
    if buf:
        lines.append(buf)
    return lines


def _summary(session: SessionState, candidate: AICandidate, latencies: list[float]) -> None:
    print("\n" + "─" * 78)
    print("\033[1mWHAT THE PANEL DID\033[0m\n")

    print(f"  turns            {len(session.turns)}")
    print(f"  difficulty       {session.difficulty}  (ewma {session.ewma:.2f})")
    print(f"  floor ended on   {session.floor_holder}")
    print(f"  median turn      {sorted(latencies)[len(latencies)//2]:.0f} ms")

    print("\n  flags raised:")
    if not session.flags.all():
        print("    (none)")
    for f in session.flags.all():
        print(f"    turn {f.turn_id:>2}  {f.kind:<20} {f.detail[:44]}")

    # --- ground truth vs what we detected ---
    print("\n\033[1mDETECTION — the candidate's own labels vs our flags\033[0m\n")
    truth = candidate.ground_truth
    detected = {f.kind for f in session.flags.all()}

    if not truth:
        print("  the candidate planted nothing in this run — try more turns")
    for turn_index, behaviour in truth:
        kind = {"no_business_framing": "no_business_framing",
                "vague": "vague",
                "contradiction": "contradiction",
                "dodge_followup": "vague"}.get(behaviour, behaviour)
        hit = kind in detected
        mark = "\033[32mcaught\033[0m" if hit else "\033[31mMISSED\033[0m"
        print(f"  candidate turn {turn_index:>2}  planted {behaviour:<22} {mark}")

    if candidate.unplanted:
        print(f"\n  never planted (interview too short): {', '.join(candidate.unplanted)}")

    print()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--persona", default="strong",
                    choices=["strong", "hesitant", "waffler"])
    ap.add_argument("--plant", nargs="*", default=None)
    ap.add_argument("--session", default="mock")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--pace", type=float, default=4.0,
                    help="seconds between turns; keeps free-tier TPM in budget")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    if a.verbose:
        logging.getLogger().setLevel(logging.INFO)

    try:
        run(turns=a.turns, persona=a.persona, plant=a.plant,
            session_id=a.session, quiet=a.quiet, pace=a.pace)
    except OneSpeakerViolation as exc:
        print(f"\n\033[31mFLOOR CONTROL BROKEN: {exc}\033[0m\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
