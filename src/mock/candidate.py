"""An AI that plays the person being interviewed.

Why this exists instead of hand-written test scripts:

A hand-written script only ever tests what we already thought of. If our
contradiction detector catches only contradictions we planted ourselves, we
have built a puppet show rather than a product.

So the candidate is **adversarial and self-labelling**. It is told to exhibit
certain behaviours at a moment of its own choosing, and it reports what it did
in a hidden field the panel never sees. That gives us ground truth generated
alongside the test, which means we can measure precision and recall on the
detectors instead of asserting "it worked" — and the numbers go in the deck.
"""

import re
import logging
import random
from dataclasses import dataclass, field

from src.gateway import providers

log = logging.getLogger("candidate")

# Behaviours the candidate can be asked to plant. These are exactly the
# capabilities that are hardest to demonstrate and easiest to break silently.
BEHAVIOURS = {
    "contradiction": (
        "At some point, state a specific fact (a number, a technology, a "
        "decision). Several turns LATER, contradict it — give a different "
        "number or claim you did the opposite. Do it casually, as a real "
        "person misremembering, not as an obvious flip."
    ),
    "vague": (
        "At some point, answer a direct question with confident-sounding "
        "generalities and no specifics at all. No numbers, no tool names, no "
        "concrete example."
    ),
    "no_business_framing": (
        "At some point, give a genuinely EXCELLENT technical answer — real "
        "depth, real trade-offs, correct engineering — that never once "
        "mentions a user, a customer, a cost, or a business outcome. The "
        "engineering must be good enough that a staff engineer would accept "
        "it without complaint."
    ),
    "dodge_followup": (
        "When pressed for a specific detail you did not give, deflect once "
        "and answer a slightly different question instead."
    ),
}

PERSONAS = {
    "strong": (
        "You are a strong candidate. You are specific: you use real numbers, "
        "name actual tools, and explain the trade-off behind each decision. "
        "You are calm and you do not ramble."
    ),
    "hesitant": (
        "You are a competent but nervous candidate. You know your material but "
        "you undersell it. Your answers are short and you often stop before "
        "the interesting part. You need to be drawn out with follow-ups."
    ),
    "waffler": (
        "You sound confident and senior but say remarkably little. You favour "
        "process language over substance — alignment, stakeholders, "
        "iterating, it depends. You rarely commit to a specific number or a "
        "named technology."
    ),
}

DEFAULT_RESUME = """\
4 years backend engineering. Python and Go. Most recently on a multi-tenant
SaaS billing platform: Postgres, Redis, Kafka, deployed on AWS with Terraform.
Led a migration from a single shared database to per-tenant sharding. Built the
retry and idempotency layer for payment webhooks. Previously at a smaller
startup building internal tooling."""


@dataclass
class CandidateTurn:
    """One answer, plus the ground truth about what it deliberately did."""

    text: str
    labels: list[str] = field(default_factory=list)
    turn_index: int = 0


@dataclass
class AICandidate:
    persona: str = "strong"
    resume: str = DEFAULT_RESUME
    job_title: str = "Senior Backend Engineer"
    plant: list[str] = field(default_factory=lambda: ["no_business_framing"])
    temperature: float = 0.85
    seed: int | None = None

    _history: list[dict] = field(default_factory=list, init=False)
    _done: list[str] = field(default_factory=list, init=False)
    _truth: list[tuple[int, str]] = field(default_factory=list, init=False)
    _turn: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.persona not in PERSONAS:
            raise ValueError(f"unknown candidate persona {self.persona!r}")
        bad = [b for b in self.plant if b not in BEHAVIOURS]
        if bad:
            raise ValueError(f"unknown behaviours {bad}")
        if self.seed is not None:
            random.seed(self.seed)

    # ------------------------------------------------------------------

    @property
    def ground_truth(self) -> list[tuple[int, str]]:
        """(turn_index, behaviour) for everything the candidate actually did.
        This is what the evaluation harness scores our detectors against."""
        return list(self._truth)

    def system_prompt(self) -> str:
        pending = [b for b in self.plant if b not in self._done]
        parts = [
            f"You are a candidate being interviewed for a {self.job_title} role "
            "on a live voice call. Answer as that person, out loud.",
            PERSONAS[self.persona],
            f"Your background:\n{self.resume}",
            "Answer in two to four sentences, under 80 words. This is speech "
            "on a phone call — no bullet points, no markdown, no headings. "
            "Real people do not deliver paragraphs out loud.",
        ]

        if pending:
            parts.append(
                "You are also a test fixture. Over the course of this "
                "interview you must exhibit the behaviours below, each once, "
                "at a moment of YOUR choosing — not necessarily now. Choose "
                "moments where they feel natural.\n"
                + "\n".join(f"- {b}: {BEHAVIOURS[b]}" for b in pending)
            )

        # A sentinel line rather than JSON. Asking a small model to speak
        # naturally AND emit valid JSON is a conflicting instruction, and
        # speech wins every time — we got fluent prose and zero labels. The
        # label goes FIRST so that truncation costs us the tail of an answer
        # rather than the ground truth we are measuring against.
        parts.append(
            "FORMAT — exactly two parts:\n"
            "Line 1 must be:  ###DID: <ids>\n"
            "  where <ids> is a comma-separated list of behaviour ids you are "
            "exhibiting on THIS turn, or the word none.\n"
            "  Valid ids: " + ", ".join(BEHAVIOURS) + "\n"
            "Line 2 onwards: what you say out loud, and nothing else.\n\n"
            "Never mention the behaviours, the ids or the test in what you "
            "say. Report a behaviour only on the turn you actually exhibit it."
        )
        return "\n\n".join(parts)

    def answer(self, question: str) -> CandidateTurn:
        """Answer one interviewer question."""
        self._turn += 1
        self._history.append({"role": "user", "content": question})

        messages = [{"role": "system", "content": self.system_prompt()}, *self._history]
        raw = providers.complete(
            messages, max_tokens=260, temperature=self.temperature
        )
        text, labels = _parse(raw)

        # A reasoning model that spends its whole budget thinking returns
        # nothing. One retry costs a second; an empty candidate turn silently
        # stalls the whole interview.
        if not text:
            log.warning("candidate returned nothing — retrying once")
            raw = providers.complete(
                messages, max_tokens=260, temperature=min(1.0, self.temperature + 0.1)
            )
            text, labels = _parse(raw)
        labels = [l for l in labels if l in BEHAVIOURS and l not in self._done]

        for l in labels:
            self._done.append(l)
            self._truth.append((self._turn, l))
            log.info("candidate planted %r on turn %d", l, self._turn)

        self._history.append({"role": "assistant", "content": text})
        return CandidateTurn(text=text, labels=labels, turn_index=self._turn)

    @property
    def unplanted(self) -> list[str]:
        """Behaviours we asked for that never happened — the interview may
        have been too short. The eval harness excludes these rather than
        counting them as detector misses."""
        return [b for b in self.plant if b not in self._done]


_SENTINEL = re.compile(r"^\s*#{0,3}\s*DID\s*:\s*(.*)$", re.IGNORECASE)


def _parse(raw: str) -> tuple[str, list[str]]:
    """Split the label line from the spoken answer.

    A missing label is logged loudly rather than swallowed: a silent fallback
    would quietly stop recording ground truth and make our detectors look
    perfect by measuring them against nothing.
    """
    lines = [l for l in raw.splitlines()]
    labels: list[str] = []
    body: list[str] = []

    for i, line in enumerate(lines):
        m = _SENTINEL.match(line)
        if m and not body:
            raw_ids = m.group(1).strip().lower()
            if raw_ids and raw_ids != "none":
                labels = [x.strip() for x in raw_ids.replace(";", ",").split(",")]
            continue
        if line.strip():
            body.append(line)

    text = " ".join(" ".join(body).split()).strip()

    if not labels and not any(_SENTINEL.match(l) for l in lines):
        log.warning("candidate omitted the ###DID line: %r", raw[:120])

    return text, labels
