"""The data everything else hangs off.

Every record carries a `turn_id`. That is deliberate: PS11 requires feedback
"linked to the interview transcript", and if the identifier is present from the
first line of code, citation is free later. Retrofitting it on report day means
rewriting the scorer, the ledgers and the report together.
"""

from dataclasses import dataclass, field

# --- the transcript -----------------------------------------------------


@dataclass
class Turn:
    turn_id: int
    speaker: str  # "candidate" | "technical" | "product" | "behavioural"
    text: str
    started_at: float
    ended_at: float
    difficulty: str = "medium"

    @property
    def is_candidate(self) -> bool:
        return self.speaker == "candidate"


@dataclass
class Claim:
    """One factual assertion the candidate made.

    `topic` is what makes contradiction detection tractable on a small model —
    we only ever compare a new claim against prior claims on the same topic.
    """

    claim_id: int
    turn_id: int
    text: str
    topic: str
    quote: str


@dataclass
class Evidence:
    """A scored observation, tied to the exact words that justify it."""

    turn_id: int
    competency: str
    concept: str
    quote: str
    polarity: str = "supports"  # "supports" | "undermines"
    weight: float = 1.0


@dataclass
class Flag:
    """Something worth surfacing: a dodge, a conflict, an integrity signal."""

    turn_id: int
    kind: str  # "vague" | "contradiction" | "integrity"
    detail: str
    quote: str = ""
    quote_b: str = ""
    ref_turn_id: int | None = None
    at: float = 0.0
    routed: bool = False  # the conductor acts on a given flag only once
    # Which tier raised it: "heuristic" (instant, inline, so the conductor has
    # a signal on the very next turn) or "judge" (the async worker, slower and
    # far better). One flag store either way — the conductor and the scorer
    # must never disagree about whether an answer was a dodge.
    source: str = ""


# --- what the interview is about ---------------------------------------


@dataclass
class JobSpec:
    """The role being interviewed for. Empty is valid — we fall back to the
    generic question bank."""

    title: str = ""
    description: str = ""
    topics: list[str] = field(default_factory=list)
    must_haves: list[str] = field(default_factory=list)

    @property
    def is_set(self) -> bool:
        return bool(self.description.strip() or self.topics or self.title.strip())

    def brief(self, limit: int = 900) -> str:
        parts = []
        if self.title:
            parts.append(f"Role: {self.title}")
        if self.must_haves:
            parts.append("Requirements: " + "; ".join(self.must_haves))
        if self.topics:
            parts.append("Topics to cover: " + ", ".join(self.topics))
        if self.description:
            parts.append(self.description.strip()[:limit])
        return "\n".join(parts)


@dataclass
class CandidateProfile:
    name: str = ""
    resume_text: str = ""
    highlights: list[str] = field(default_factory=list)

    @property
    def is_set(self) -> bool:
        return bool(self.resume_text.strip() or self.highlights)

    def brief(self, limit: int = 700) -> str:
        parts = []
        if self.name:
            parts.append(f"Candidate: {self.name}")
        if self.highlights:
            parts.append("From their resume: " + "; ".join(self.highlights))
        elif self.resume_text:
            parts.append(self.resume_text.strip()[:limit])
        return "\n".join(parts)


@dataclass
class PlannedQuestion:
    """One question in a persona's plan, generated from the job and resume."""

    role: str
    text: str
    difficulty: str = "medium"
    topic: str = ""
    asked_turn_id: int | None = None

    @property
    def asked(self) -> bool:
        return self.asked_turn_id is not None


# --- scoring ------------------------------------------------------------


@dataclass
class ConceptHit:
    concept: str
    covered: bool
    quote: str = ""
    weight: float = 1.0


@dataclass
class AnswerScore:
    """The result of judging one candidate answer against its rubric."""

    turn_id: int
    role: str
    score: float = 0.0
    max_score: float = 10.0
    concepts: list[ConceptHit] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    depth: str = "moderate"  # "shallow" | "moderate" | "deep"
    penalty: float = 0.0

    @property
    def normalised(self) -> float:
        """0..1, so results stay comparable across interviews of different
        lengths and questions of different weights."""
        if self.max_score <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.score - self.penalty) / self.max_score))

    @property
    def covered(self) -> list[str]:
        return [c.concept for c in self.concepts if c.covered]


# --- the coding round ---------------------------------------------------


@dataclass
class CodeSnapshot:
    at: float
    language: str
    source: str
    turn_id: int | None = None


# --- integrity ----------------------------------------------------------


@dataclass
class IntegrityEvent:
    """Raised in the browser, never accompanied by video.

    Advisory only. These appear in the report as a timestamped log, never as
    an automatic rejection.
    """

    at: float
    kind: str  # no_face | multiple_faces | gaze_away | tab_blur | paste
    detail: str = ""
    seconds: float = 0.0
    turn_id: int | None = None
