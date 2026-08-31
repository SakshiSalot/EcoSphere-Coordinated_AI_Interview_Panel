"""Deterministic regression tests — no API keys, no network, no cost.

The AI candidate in `candidate.py` explores; this file *asserts*. To prove
that the contradiction detector catches a contradiction, the input has to be
identical on every run, so these fixtures are fixed and small.

    python -m src.mock.offline

Runs in well under a second, which is what makes it a habit rather than a
chore. Anything that breaks floor control, the handoff policy, the vagueness
detector or the difficulty ladder fails here first.
"""

import sys

from src.analysis import quick
from src.conductor import difficulty
from src.conductor.conductor import decide_floor
from src.conductor.personas import active_roles
from src.contract import next_utterance
from src.gateway import providers
from src.state.session import reset_session

# --- the fixtures -------------------------------------------------------
# Four deliberate baits. Each exists to trigger exactly one capability, so
# every run exercises the parts that are hardest to get right and easiest to
# break silently.

STRONG_NO_BUSINESS = (
    "We sharded Postgres by tenant id using a consistent hash ring, with the "
    "routing table in Redis and a read replica per shard. That took p99 on the "
    "billing query from 400ms down to 60ms. The tricky part was the migration: "
    "we dual-wrote for two weeks and backfilled with a rate-limited job so we "
    "never saturated the primary's WAL."
)

VAGUE = (
    "It depends, really. There are a number of things you have to weigh up, and "
    "generally speaking you align the stakeholders early and then iterate on it "
    "until everyone is comfortable with the direction."
)

CLAIM = (
    "We cut the checkout latency from 400ms to 60ms after the sharding work "
    "landed, measured at p99 over a full week."
)

CONTRADICTION = (
    "The latency after sharding was actually closer to 200ms at p99, we never "
    "really got it under that."
)

STRONG_WITH_BUSINESS = (
    "We sharded Postgres by tenant id, which took p99 from 400ms to 60ms. That "
    "mattered because our three largest customers were timing out at checkout "
    "and we were losing roughly 2% of transactions during peak hours."
)


def _stub_providers() -> None:
    """Replace the model with a scripted panel, so these tests need no keys."""

    questions = {
        "technical": "Tell me about a system you scaled. What actually broke first?",
        "product": "That is sound engineering — but who was it for, and what was it worth?",
        "behavioural": "Tell me about a time that work put you in conflict with someone.",
    }

    from src.conductor.personas import persona

    def _question_for(messages) -> str:
        system = messages[0]["content"] if messages else ""
        for role, q in questions.items():
            if persona(role)["name"] in system:
                return q
        return "Go on."

    def chat(messages, tools=None, max_tokens=300, temperature=0.6, allow_wait=True):
        text = _question_for(messages)
        return text, [], {"role": "assistant", "content": text}, "stub"

    def complete(messages, **kw):
        return _question_for(messages)

    providers.chat = chat
    providers.complete = complete
    providers.complete_with_tools = lambda m, t, **kw: (_question_for(m), [])


# --- the checks ---------------------------------------------------------

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  \033[32mPASS\033[0m  {name}")
    else:
        FAILED.append(name)
        print(f"  \033[31mFAIL\033[0m  {name}   {detail}")


def _turn(session_id: str, history: list, answer: str | None) -> tuple[str, str]:
    """One full turn, calling every persona exactly as Agora does."""
    spoke = []
    for role in active_roles():
        stream = next_utterance(session_id, role, history)
        if stream is not None:
            spoke.append((role, "".join(stream)))

    if len(spoke) != 1:
        raise AssertionError(f"{len(spoke)} personas spoke: {[r for r, _ in spoke]}")

    role, said = spoke[0]
    history.append({"role": "assistant", "content": said})
    if answer is not None:
        history.append({"role": "user", "content": answer})
    return role, said


# --- 1. heuristics in isolation -----------------------------------------


def test_heuristics() -> None:
    print("\n\033[1mheuristics\033[0m")

    vague, why, quote = quick.is_vague(VAGUE)
    check("vague answer is flagged", vague, why)
    check("vague flag carries a quote", bool(quote))

    not_vague, _, _ = quick.is_vague(STRONG_NO_BUSINESS)
    check("strong answer is not flagged vague", not not_vague)

    no_biz, q = quick.is_technically_sound_but_no_business(STRONG_NO_BUSINESS)
    check("strong tech with no customer is flagged", no_biz, q)

    has_biz, _ = quick.is_technically_sound_but_no_business(STRONG_WITH_BUSINESS)
    check("same answer WITH business framing is not flagged", not has_biz)

    weak, _ = quick.is_technically_sound_but_no_business(VAGUE)
    check("vague answer does not count as technically sound", not weak)


# --- 2. floor control ---------------------------------------------------


def test_one_speaker() -> None:
    print("\n\033[1mfloor control\033[0m")
    session = reset_session("t-floor")
    history: list = []

    try:
        for _ in range(6):
            _turn("t-floor", history, "That is a fair question, let me think.")
        check("exactly one persona speaks on every turn", True)
    except AssertionError as exc:
        check("exactly one persona speaks on every turn", False, str(exc))

    check("turns were recorded", len(session.turns) >= 6, f"{len(session.turns)}")


def test_handoff() -> None:
    """The problem statement's own example, end to end."""
    print("\n\033[1mthe PS11 handoff — Priya accepts, Arjun challenges\033[0m")
    session = reset_session("t-handoff")
    history: list = []

    first_role, _ = _turn("t-handoff", history, STRONG_NO_BUSINESS)
    check("technical opens the interview", first_role == "technical", first_role)

    second_role, _ = _turn("t-handoff", history, "Sure, happy to expand on that.")
    check(
        "a technically sound answer with no customer hands to product",
        second_role == "product",
        f"floor went to {second_role}",
    )
    check(
        "the no_business_framing flag was raised",
        any(f.kind == "no_business_framing" for f in session.flags.all()),
    )


def test_vague_holds_floor() -> None:
    print("\n\033[1mtwo vague answers keep the floor\033[0m")
    session = reset_session("t-vague")
    history: list = []

    _turn("t-vague", history, VAGUE)
    role_a, _ = _turn("t-vague", history, VAGUE)
    role_b, _ = _turn("t-vague", history, "Right, so, broadly it varies a lot.")

    check(
        "the interviewer holds the floor to pin them down",
        role_a == role_b,
        f"{role_a} then {role_b}",
    )
    check("vagueness was flagged more than once", session.consecutive_vague() >= 2)


def test_contradiction_routing() -> None:
    print("\n\033[1mcontradictions go back to the original asker\033[0m")
    session = reset_session("t-contra")
    history: list = []

    _turn("t-contra", history, CLAIM)
    _turn("t-contra", history, "Yes, that is right.")

    # Simulate what the analysis worker produces when it finds a conflict.
    session.claims.add(1, CLAIM, "latency", "400ms to 60ms")
    session.flags.add(
        turn_id=len(session.turns),
        kind="contradiction",
        detail="p99 stated as 60ms and later as 200ms",
        quote="400ms down to 60ms",
        quote_b="actually closer to 200ms",
        ref_turn_id=1,
    )
    session.floor_holder = "behavioural"
    role, reason = decide_floor(session)

    check(
        "the floor returns to whoever asked the original question",
        role == "technical",
        f"went to {role} ({reason})",
    )
    check("a contradiction is only routed once", decide_floor(session)[0] != "technical"
          or session.flags.recent("contradiction", 1)[0].routed)


# --- 3. the difficulty ladder -------------------------------------------


def test_difficulty_both_directions() -> None:
    """Teams usually test only the downward path. A ladder that drops but
    never recovers punishes a candidate for one bad answer."""
    print("\n\033[1mdifficulty ladder\033[0m")
    session = reset_session("t-diff")

    for _ in range(6):
        difficulty.update(session, 0.05)
    check("two weak answers lower the difficulty", session.difficulty == "easy",
          f"{session.difficulty} (ewma {session.ewma:.2f})")

    for _ in range(10):
        difficulty.update(session, 0.98)
    check("strong answers raise it again", session.difficulty == "hard",
          f"{session.difficulty} (ewma {session.ewma:.2f})")

    session2 = reset_session("t-diff2")
    difficulty.update(session2, 0.02)
    check("one bad answer alone does not move the level (hysteresis)",
          session2.difficulty == "medium", session2.difficulty)


def test_shared_context() -> None:
    print("\n\033[1mshared memory across personas\033[0m")
    session = reset_session("t-shared")
    history: list = []

    _turn("t-shared", history, STRONG_NO_BUSINESS)
    _turn("t-shared", history, "Happy to go deeper.")

    digest = session.shared_digest()
    check("the digest carries what the candidate said", "sharded Postgres" in digest)
    check("the digest cites turn numbers", "[1]" in digest or "[2]" in digest)
    check("open concerns are surfaced to the next persona",
          "concern" in digest.lower() or len(session.flags.all()) == 0)


def main() -> int:
    _stub_providers()
    print("\n\033[1mOFFLINE REGRESSION SUITE\033[0m  ·  no keys, no network, no cost")
    print("─" * 70)

    test_heuristics()
    test_one_speaker()
    test_handoff()
    test_vague_holds_floor()
    test_contradiction_routing()
    test_difficulty_both_directions()
    test_shared_context()

    print("\n" + "─" * 70)
    if FAILED:
        print(f"\033[31m{len(FAILED)} failed\033[0m, {len(PASSED)} passed\n")
        for f in FAILED:
            print(f"    {f}")
        print()
        return 1
    print(f"\033[32mall {len(PASSED)} checks passed\033[0m\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
