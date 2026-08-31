"""Producing one persona's turn, and reacting to one candidate turn.

Both the live gateway and the mock harness go through here, which is what
makes the harness a real test rather than a parallel implementation.
"""

import json
import logging

from src.analysis import quick
from src.conductor import difficulty
from src.conductor.personas import persona, persona_prompt
from src.conductor.tools import TOOL_SCHEMAS, handle_tool_call
from src.gateway import providers
from src.state.session import SessionState

log = logging.getLogger("turn")

# Verbatim history is the single biggest consumer of the tokens-per-minute
# budget, and almost all of its value is already in the shared digest. Four
# exchanges, each clipped, is enough for the model to follow the thread.
HISTORY_TURNS = 4
HISTORY_CLIP = 480

# Spoken replies, not written ones. This cap does more for realism than any
# prompt wording: given room to monologue, a model will take it, and a
# monologuing interviewer is the fastest way to make a demo feel fake.
SPEECH_TOKENS = 130


def as_messages(session: SessionState, limit: int = HISTORY_TURNS) -> list[dict]:
    """The recent transcript in OpenAI shape.

    The candidate is the user; every persona is the assistant, because from
    one persona's point of view the panel speaks with one voice. What the
    *other* personas asked arrives through the shared digest in the system
    prompt instead, which is cheaper and reads better.
    """
    out: list[dict] = []
    for t in session.turns.all()[-limit * 2 :]:
        role = "user" if t.is_candidate else "assistant"
        text = t.text if len(t.text) <= HISTORY_CLIP else t.text[:HISTORY_CLIP] + "…"
        out.append({"role": role, "content": text})
    return out


# Used only when no model key is configured. This keeps the Agora path — the
# interruption gate, distinct voices, floor control — testable before anyone
# has a Groq key, so the two dependencies fail independently instead of
# together.
CANNED = {
    "technical": [
        "Tell me about a system you have built that had to handle real load. "
        "What broke first?",
        "Walk me through how you would shard that. What breaks when a tenant "
        "grows ten times?",
        "Where does that fall over, and how would you know before a customer did?",
    ],
    "product": [
        "That is sound engineering — but who was it for, and what was it worth "
        "to them?",
        "What did you drop to make room for that, and who felt it?",
        "How would you know if you had chosen wrong?",
    ],
    "behavioural": [
        "Tell me about a time that work put you in conflict with someone. What "
        "did you actually say?",
        "Let us role-play. I am the lead who thinks your rewrite is a waste of "
        "a quarter. Convince me.",
        "What would that person say about you if I asked them?",
    ],
}


def speak(session: SessionState, role: str) -> tuple[str, list[tuple[str, str]]]:
    """Generate what `role` says this turn, and apply any tools it called."""
    if not providers.available():
        asked = session.turns.count_asked_by(role)
        bank = CANNED[role]
        log.warning("no model configured — using canned %s question %d", role, asked + 1)
        return bank[min(asked, len(bank) - 1)], []

    system = persona_prompt(
        role,
        difficulty=session.difficulty,
        shared_context=session.shared_digest(),
    )

    extra: list[str] = []

    planned = session.next_question_for(role)
    if planned and not session.turns.all():
        extra.append(f"Open the interview with this question: {planned.text}")
    elif planned:
        extra.append(f"If it is time for a new question, ask this one: {planned.text}")

    if session.directive:
        extra.append(f"The recruiter watching has asked you to: {session.directive}")
        session.directive = None

    if session.coding_round and session.code.latest:
        extra.append(
            "The candidate is writing code right now. Comment on what they have "
            "actually written — be specific about a line or a choice. Here is "
            "their editor:\n\n" + session.code.latest.source[:1800]
        )

    if extra:
        system = system + "\n\n" + "\n\n".join(extra)

    messages = [{"role": "system", "content": system}, *as_messages(session)]
    if not session.turns.all():
        messages.append({"role": "user", "content": "(the candidate has just joined)"})

    text, calls, assistant, provider = providers.chat(
        messages, TOOL_SCHEMAS, max_tokens=SPEECH_TOKENS, temperature=0.6
    )

    results = [handle_tool_call(session, name, args) for name, args in calls]

    # A persona that calls a tool usually returns no spoken content on that
    # same turn. Left alone, Priya would hand the floor to Arjun in complete
    # silence — so we continue the tool loop to get her line. The problem
    # statement says the technical interviewer *accepts* the answer; accepting
    # it without saying so is not the same thing.
    if calls and not text:
        tool_msgs = [
            {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(res)}
            for tc, res in zip(assistant.get("tool_calls", []), results)
        ]
        text = providers.complete(
            [
                *messages,
                assistant,
                *tool_msgs,
                {
                    "role": "system",
                    "content": "Now say your line out loud to the candidate — "
                    "two sentences at most. Do not mention tools, handoffs or "
                    "anything the candidate cannot hear.",
                },
            ],
            max_tokens=160,
            temperature=0.6,
        )

    log.info("%s spoke via %s (%d tool calls)", role, provider, len(calls))

    if planned and text:
        planned.asked_turn_id = len(session.turns) + 1

    return text, calls


def observe(session: SessionState, turn_id: int, text: str) -> float:
    """React to a candidate answer: raise flags, move the difficulty ladder.

    Runs inline because it is pure string work with no model call. The
    model-based scoring runs separately and off the critical path, and its
    results land in time for the turn after next.
    """
    vague, why, quote = quick.is_vague(text)
    if vague:
        session.flags.add(turn_id, "vague", why, quote)
        log.info("flag vague on turn %d: %s", turn_id, why)

    no_biz, biz_quote = quick.is_technically_sound_but_no_business(text)
    if no_biz:
        session.flags.add(
            turn_id,
            "no_business_framing",
            "technically sound, but no user, customer or cost mentioned",
            biz_quote,
        )
        log.info("flag no_business_framing on turn %d", turn_id)

    # Placeholder score until the rubric-based judge runs. Specificity is a
    # decent proxy and keeps the ladder moving in tests; the real scorer
    # replaces this value when it lands.
    score = 0.0 if vague else max(0.25, quick.specificity(text))
    difficulty.update(session, score)
    return score


def opening_greeting(role: str) -> str:
    from src.conductor.personas import greeting

    return greeting(role)


def persona_name(role: str) -> str:
    return persona(role)["name"]
