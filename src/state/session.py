"""The one object every persona and every worker reads from.

A single shared state object is precisely what PS11 capability 3 asks for.
Because all three personas read the same one, cross-role memory is a property
of the architecture rather than a feature we had to build.
"""

import threading
import time
from dataclasses import dataclass, field

from src.state.ledger import (
    ClaimLedger,
    CodeLedger,
    EvidenceLedger,
    FlagLedger,
    IntegrityLedger,
    TurnLedger,
)
from src.state.models import AnswerScore, CandidateProfile, JobSpec, PlannedQuestion


@dataclass
class SessionState:
    session_id: str

    # --- floor control ---
    floor_holder: str = "technical"
    pending_handoff: tuple[str, str] | None = None  # (to_role, reason)
    consecutive_turns: int = 0
    active_scenario: str | None = None
    scenario_owner: str | None = None

    # --- difficulty ---
    difficulty: str = "medium"
    ewma: float = 0.5
    out_of_band: int = 0

    # --- what this interview is about ---
    job: JobSpec = field(default_factory=JobSpec)
    candidate: CandidateProfile = field(default_factory=CandidateProfile)
    plan: list[PlannedQuestion] = field(default_factory=list)
    rubrics: dict[int, dict] = field(default_factory=dict)  # turn_id -> rubric

    # --- ledgers ---
    turns: TurnLedger = field(default_factory=TurnLedger)
    claims: ClaimLedger = field(default_factory=ClaimLedger)
    evidence: EvidenceLedger = field(default_factory=EvidenceLedger)
    flags: FlagLedger = field(default_factory=FlagLedger)
    code: CodeLedger = field(default_factory=CodeLedger)
    integrity: IntegrityLedger = field(default_factory=IntegrityLedger)

    # --- scoring ---
    scores: list[AnswerScore] = field(default_factory=list)

    # --- coding round ---
    coding_round: bool = False
    last_code_comment_at: float = 0.0
    code_snapshots_at_last_comment: int = 0

    # --- observer directives, consumed by the next turn ---
    directive: str | None = None

    started_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------

    def holds_floor(self, role: str) -> bool:
        return self.floor_holder == role

    def next_question_for(self, role: str) -> PlannedQuestion | None:
        for q in self.plan:
            if q.role == role and not q.asked:
                return q
        return None

    def consecutive_vague(self) -> int:
        """Candidate turns in a row that were flagged vague.

        Genuinely consecutive — it stops at the first substantive answer.
        Two in a row is what makes an interviewer stop moving on and pin them
        down instead, because moving on rewards the deflection.
        """
        vague_turns = self.flags.turns_of_kind("vague")
        count = 0
        for t in reversed(self.turns.all()):
            if not t.is_candidate:
                continue
            if t.turn_id in vague_turns:
                count += 1
            else:
                break
        return count

    def shared_digest(self, last_n: int = 8) -> str:
        """A compact summary of what the candidate told the *other* personas.

        This is the mechanism behind PS11 capability 3, and it is what makes
        the panel feel like one interview rather than three separate ones.
        When Arjun references something the candidate told Priya, unprompted,
        that is the moment the concept lands for a judge.

        Capped hard: unbounded history grows every turn and hits Groq's
        tokens-per-minute ceiling partway through a long interview — failing
        exactly when the interview gets interesting.
        """
        lines: list[str] = []

        for t in self.turns.all()[-last_n * 2 :]:
            if t.is_candidate:
                lines.append(f"[{t.turn_id}] They said: {_clip(t.text, 220)}")
            else:
                lines.append(f"[{t.turn_id}] {t.speaker} asked: {_clip(t.text, 160)}")

        open_flags = []
        for f in self.flags.all()[-4:]:
            if f.kind == "vague":
                open_flags.append(f"vague on turn {f.turn_id}: {_clip(f.detail, 90)}")
            elif f.kind == "contradiction":
                open_flags.append(
                    f"contradiction between turns {f.ref_turn_id} and {f.turn_id}"
                )
            elif f.kind == "no_business_framing":
                # Whoever picks up the floor needs to know *why* they got it,
                # or the challenge lands vaguely instead of on the actual gap.
                open_flags.append(
                    f"turn {f.turn_id} was technically sound but named no user, "
                    "customer or cost"
                )
        if open_flags:
            lines.append("Open concerns: " + "; ".join(open_flags))

        if self.code.latest and self.coding_round:
            lines.append(
                "The candidate is writing code right now; the latest version is in "
                "your prompt below."
            )

        return "\n".join(lines)

    def record_score(self, score: AnswerScore) -> None:
        self.scores.append(score)

    @property
    def running_score(self) -> float:
        """0..1 across the whole interview so far. Normalised so an interview
        with fewer questions is not under-counted against a longer one."""
        if not self.scores:
            return 0.0
        return sum(s.normalised for s in self.scores) / len(self.scores)

    def snapshot(self) -> dict:
        """What the observer dashboard and the debug endpoints render."""
        return {
            "session_id": self.session_id,
            "floor_holder": self.floor_holder,
            "difficulty": self.difficulty,
            "ewma": round(self.ewma, 3),
            "running_score": round(self.running_score, 3),
            "turns": len(self.turns),
            "claims": len(self.claims),
            "flags": [
                {
                    "turn_id": f.turn_id,
                    "kind": f.kind,
                    "detail": f.detail,
                    "quote": f.quote,
                    "quote_b": f.quote_b,
                    "ref_turn_id": f.ref_turn_id,
                }
                for f in self.flags.all()
            ],
            "integrity": self.integrity.summary(),
            "coding_round": self.coding_round,
            "active_scenario": self.active_scenario,
            "job_title": self.job.title,
        }


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- registry -----------------------------------------------------------
# An interview is a few hundred rows that live for twenty minutes. A database
# is complexity for zero scoring benefit; sessions export to JSON on end.

_SESSIONS: dict[str, SessionState] = {}
_LOCK = threading.Lock()


def get_session(session_id: str) -> SessionState:
    with _LOCK:
        if session_id not in _SESSIONS:
            _SESSIONS[session_id] = SessionState(session_id=session_id)
        return _SESSIONS[session_id]


def reset_session(session_id: str) -> SessionState:
    with _LOCK:
        _SESSIONS[session_id] = SessionState(session_id=session_id)
        return _SESSIONS[session_id]


def all_sessions() -> dict[str, SessionState]:
    return dict(_SESSIONS)
