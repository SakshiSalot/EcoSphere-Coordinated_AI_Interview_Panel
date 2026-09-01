"""One interview, marked end to end.

    python -m src.analysis.demo

Runs a fixed transcript through the whole chain — rubric generation, judging
with evidence, allocation, final assessment — against a real SessionState,
exactly as the background worker will. Roughly eight model calls.

The transcript is fixed rather than generated so the demo shows the same thing
every time and each turn is there to exercise something specific:

    turns 1-4   a technical thread: a strong answer, then a follow-up. Two
                answers, ONE rubric, one question's marks split between them.
    turns 5-8   the problem statement's own scenario. The engineering is sound
                and nobody has mentioned a customer, so Arjun challenges it —
                and the candidate deflects, which is the vagueness the instant
                heuristic cannot see because the answer is full of numbers.
    turns 9-10  a behavioural thread, where "what did you actually say" is the
                whole marking scheme.

Difficulty rises after the strong opening and stays high, so the allocation has
bands to work with rather than a flat interview.
"""

import argparse
import logging
import sys
import time

from src.analysis import allocation, judge, pipeline
from src.state.models import JobSpec
from src.state.session import reset_session

JOB = JobSpec(
    title="Senior Backend Engineer",
    description=(
        "Own the billing and payments platform for a multi-tenant SaaS "
        "product. Design for correctness under retries, scale Postgres past a "
        "single primary, and keep p99 latency predictable as tenants grow."
    ),
    topics=["database scaling", "idempotency", "incident response"],
    must_haves=["Postgres at scale", "distributed systems", "on-call ownership"],
)

# (speaker, difficulty in force, text)
TRANSCRIPT = [
    ("technical", "medium",
     "Tell me about a time you had to scale a multi-tenant Postgres database "
     "past a single primary. What did you actually do?"),
    ("candidate", "medium",
     "We sharded Postgres by tenant id. That was the key precisely because it "
     "kept one customer's data on one node, so nothing on the hot path ever "
     "needed a cross-shard join. Consistent hash ring, routing table in Redis, "
     "a read replica per shard. That took p99 on the billing query from 400ms "
     "down to 60ms. The trade-off we accepted is that a single very large "
     "tenant can still overwhelm one shard, so we alerted on per-shard write "
     "latency and kept a runbook for splitting the biggest ones out before a "
     "customer noticed. The migration was the tricky part: we dual-wrote for "
     "two weeks and backfilled with a rate-limited job so we never saturated "
     "the primary's WAL."),

    ("technical", "hard",
     "You said a large tenant can still overwhelm a shard. What happened the "
     "first time one actually did?"),
    ("candidate", "hard",
     "It was our second biggest account, about four times the write volume of "
     "anyone else. The per-shard latency alert fired at around 300ms before "
     "support saw anything. We moved them onto a dedicated shard using the "
     "same dual-write path, which took about six hours, and after that we set "
     "a hard rule that any tenant over ten percent of a shard's writes gets "
     "isolated automatically."),

    ("product", "hard",
     "That is sound engineering, but nobody has said who it was for. Who felt "
     "that drop from 400 to 60 milliseconds, and what was it worth to them?"),
    ("candidate", "hard",
     "The billing query is on the invoice page, so it is every tenant admin "
     "who opens billing. Technically the improvement is a 6.6x reduction in "
     "p99, and it also cut our database CPU by about 40 percent, which meant "
     "we could defer an instance upgrade for two quarters."),

    ("product", "hard",
     "That is the infrastructure saving. What did the customer actually get "
     "out of it — can you put a number on that side?"),
    ("candidate", "hard",
     "It depends how you measure it, really. There are a number of factors "
     "there and we did look at various things. Generally speaking a faster "
     "page is better for everyone, and the feedback from the account team "
     "afterwards was positive, so we were comfortable with the direction."),

    ("behavioural", "medium",
     "Tell me about a time an engineer on your team pushed back hard on a "
     "decision you had already made. What did you actually say to them?"),
    ("candidate", "medium",
     "Our staff engineer thought sharding was premature and wanted to buy a "
     "bigger instance instead. He said it in standup, fairly bluntly. I said "
     "he might be right and asked him to spend two days costing out the "
     "vertical option properly, because I would rather be wrong before we "
     "started than halfway through. He came back and showed the bigger "
     "instance bought us about nine months. We went ahead with sharding but "
     "used his number to push the timeline out by a quarter, and he ran the "
     "migration. Looking back I should have asked him before I announced it, "
     "not after."),
]


def run(pace: float = 7.0, total: float = 100.0) -> int:
    session = reset_session("demo")
    session.job = JOB

    print(f"\n\033[1mMARKING ONE INTERVIEW END TO END\033[0m   ·   "
          f"judge: {judge.MODEL}   ·   {JOB.title}")
    print("─" * 78)

    first = True
    for speaker, band, text in TRANSCRIPT:
        session.difficulty = band
        turn_id = session.turns.add(speaker, text, difficulty=band)

        if speaker != "candidate":
            print(f"\n\033[36m\033[1mturn {turn_id:>2}  {speaker} ({band})\033[0m")
            _wrap(text)
            continue

        print(f"\n\033[97m\033[1mturn {turn_id:>2}  candidate\033[0m")
        _wrap(text)

        if not first:
            time.sleep(pace)  # free-tier requests per minute
        first = False

        score = pipeline.mark_answer(session, turn_id)
        if score is None:
            print("        \033[33m(unscored — see the log)\033[0m")
            continue
        _show(score)

    return _assessment(session, total)


def run_live(turns: int, persona: str, pace: float, total: float) -> int:
    """Mark a real interview instead of the fixed transcript.

    The mock harness drives `contract.next_utterance` exactly as Agora does, so
    the marking hooks fire the same way they will in production: a rubric is
    built the moment a persona asks, and the answer is judged in the background
    while the interview carries on. Nothing here calls the marking engine
    directly — it happens because the interview happened.

    That is what makes this the only run that closes the loop. A flag the judge
    raises on turn 4 is in shared state before the conductor decides who speaks
    on turn 6, which is the feedback arrow the fixed transcript cannot show.

    It is also the only test that exercises rubric generation on questions
    nobody wrote in advance — which is every question in a real interview.
    """
    from src.mock import replay

    session = replay.run(
        turns=turns, persona=persona, session_id="demo-live", pace=pace
    )

    # The conversation never waits for marking; the report does.
    print("\n  \033[2mwaiting for background marking to finish…\033[0m")
    pipeline.drain()

    answers = [t for t in session.turns.all() if t.is_candidate]
    if not answers:
        print("\n  The interview produced no answers — nothing to mark.\n")
        return 1

    scored = {s.turn_id: s for s in session.scores}

    print("\n" + "═" * 78)
    print(f"\033[1mWHAT THE PANEL ASKED, AND WHAT IT WAS WORTH\033[0m   "
          f"·   {len(scored)}/{len(answers)} marked   ·   judge: {judge.MODEL}")
    print("═" * 78)

    for turn in answers:
        question_turn = pipeline.question_turn_for(session, turn.turn_id)
        question = session.turns.get(question_turn) if question_turn else None

        if question:
            print(f"\n\033[36m\033[1mturn {question.turn_id:>2}  "
                  f"{question.speaker} ({question.difficulty})\033[0m")
            _wrap(question.text)
        print(f"\n\033[97m\033[1mturn {turn.turn_id:>2}  candidate\033[0m")
        _wrap(turn.text)

        score = scored.get(turn.turn_id)
        if score is None:
            print("        \033[33m(unscored — run with --verbose to see why)\033[0m")
            continue
        _show(score)

    return _assessment(session, total)


def _show(score) -> None:
    covered = len(score.covered)
    print(f"\n        \033[1m{score.score:.1f}/{score.max_score:.1f}\033[0m"
          f"   {covered}/{len(score.concepts)} concepts"
          f"   depth {score.depth}"
          + (f"   \033[31mpenalty {score.penalty:.1f}\033[0m"
             if score.penalty else ""))
    for hit in score.concepts:
        if hit.covered:
            print(f"          \033[32m+{hit.weight:>4.1f}\033[0m  {hit.concept}")
            print(f"                  \033[2m\"{hit.quote[:66]}\"\033[0m")
    for missed in score.missing:
        print(f"          \033[31m  --\033[0m  {missed}")


def _assessment(session, total: float) -> int:
    print("\n" + "═" * 78)
    print("\033[1mFINAL ASSESSMENT\033[0m")

    if not session.scores:
        print("\n  nothing was scored — the judge was unavailable for every answer.")
        print("  That is the intended failure: no marks rather than invented ones.\n")
        return 1

    result = pipeline.finalise(session, total=total)

    print(f"\n  {'interviewer':<16}{'earned':>9}{'available':>12}{'':>4}share")
    for role, bucket in result["by_role"].items():
        bar = "█" * round(bucket["fraction"] * 20)
        print(f"  {role:<16}{bucket['earned']:>9.1f}{bucket['available']:>12.1f}"
              f"    {bucket['fraction'] * 100:>3.0f}%  \033[36m{bar}\033[0m")

    print(f"\n  {'TOTAL':<16}\033[1m{result['earned']:>9.1f}\033[0m"
          f"{'/ ' + str(int(result['total'])):>12}"
          f"    \033[1m{result['fraction'] * 100:>3.0f}%\033[0m")
    print(f"  {'':<16}{'':>9}{'':>12}    "
          f"\033[2mon questions worth {result['available']:.0f}, "
          f"penalties {result['penalties']:.1f}\033[0m")

    # The rubric-per-thread saving, which is what keeps this affordable.
    threads = {r.get("question_turn") for r in session.rubrics.values()}
    print(f"\n  {len(session.rubrics)} questions marked against "
          f"{len(threads)} rubrics · {len(session.scores)} answers scored")

    # One flag store, two tiers writing to it. The heuristic runs inline so the
    # conductor has a signal on the very next turn; the judge arrives seconds
    # later and sees what a word list cannot. Both cost the same marks.
    flags = session.flags.all()
    if flags:
        print("\n\033[1mFLAGS — what the conductor routes on, and what it cost\033[0m")
        for flag in flags:
            tier = flag.source or "heuristic"
            colour = "\033[35m" if tier == "judge" else "\033[33m"
            print(f"  turn {flag.turn_id:>2}  {colour}{tier:<10}\033[0m "
                  f"{flag.kind:<20} {flag.detail[:44]}")
            if flag.quote:
                print(f"           \033[2m\"{flag.quote[:62]}\"\033[0m")

    print("\n\033[1mEVIDENCE — every mark, and the words that earned it\033[0m")
    print("\033[2mVerified as a real substring of the transcript at scoring "
          "time, so no line here can cite something unsaid.\033[0m\n")
    for turn_id, role, concept, quote in pipeline.evidence_for(session)[:8]:
        print(f"  \033[36mturn {turn_id:>2}\033[0m  {role:<12} {concept}")
        print(f"           \033[2m\"{quote[:64]}\"\033[0m")

    print()
    return 0


def _wrap(text: str, width: int = 70, indent: str = "        ") -> None:
    words, line = text.split(), ""
    for word in words:
        if line and len(line) + len(word) + 1 > width:
            print(indent + line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        print(indent + line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="run a real mock interview and mark that instead "
                             "of the fixed transcript")
    parser.add_argument("--turns", type=int, default=6,
                        help="--live only: interview length")
    parser.add_argument("--persona", default="strong",
                        choices=["strong", "hesitant", "waffler"],
                        help="--live only: which candidate to interview")
    parser.add_argument("--interview-pace", type=float, default=10.0,
                        help="--live only: seconds between turns. Groq allows "
                             "8000 tokens/min per model; going faster forces a "
                             "model swap mid-interview")
    parser.add_argument("--pace", type=float, default=7.0,
                        help="seconds between model calls; free-tier headroom")
    parser.add_argument("--total", type=float, default=allocation.DEFAULT_TOTAL)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not judge.available():
        print("\nGEMINI_API_KEY is not set — nothing to demonstrate.\n")
        return 1

    if args.live:
        return run_live(
            turns=args.turns,
            persona=args.persona,
            pace=args.interview_pace,
            total=args.total,
        )
    return run(pace=args.pace, total=args.total)


if __name__ == "__main__":
    sys.exit(main())
