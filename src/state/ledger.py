"""Append-and-query ledgers.

Built before any conductor logic, because three later capabilities read from
them and none can be retrofitted cheaply. `ClaimLedger.by_topic()` in
particular is what makes contradiction detection work on a small model.
"""

import time

from src.state.models import (
    Claim,
    CodeSnapshot,
    Evidence,
    Flag,
    IntegrityEvent,
    Turn,
)


class TurnLedger:
    def __init__(self) -> None:
        self._turns: list[Turn] = []

    def add(
        self,
        speaker: str,
        text: str,
        started_at: float | None = None,
        ended_at: float | None = None,
        difficulty: str = "medium",
    ) -> int:
        now = time.time()
        turn = Turn(
            turn_id=len(self._turns) + 1,
            speaker=speaker,
            text=text,
            started_at=started_at if started_at is not None else now,
            ended_at=ended_at if ended_at is not None else now,
            difficulty=difficulty,
        )
        self._turns.append(turn)
        return turn.turn_id

    def extend(self, turn_id: int, text: str = "") -> bool:
        """Replace a turn's text with a fuller version, and mark it still live.

        Agora sends a partial transcript the moment it thinks a turn ended,
        then sends it again as the candidate keeps talking. Recording only the
        first fragment leaves the transcript — and everything marked from it —
        holding half a sentence.

        `ended_at` is bumped on EVERY touch, including one that brings no new
        words. It is what "how long since we last heard about this utterance"
        is measured against, and measuring from `started_at` instead was a real
        bug: a thirty-second answer had its window expire while the candidate
        was still speaking, so the last agent to report was treated as a whole
        new answer and the reply was recorded twice.

        Called with no text to refresh the clock alone.
        """
        turn = self.get(turn_id)
        if turn is None:
            return False
        if text:
            turn.text = text
        turn.ended_at = time.time()
        return True

    def get(self, turn_id: int) -> Turn | None:
        if 1 <= turn_id <= len(self._turns):
            return self._turns[turn_id - 1]
        return None

    def all(self) -> list[Turn]:
        return list(self._turns)

    def last(self, n: int = 1) -> list[Turn]:
        return self._turns[-n:] if n > 0 else []

    def last_candidate(self) -> Turn | None:
        for t in reversed(self._turns):
            if t.is_candidate:
                return t
        return None

    def by_speaker(self, speaker: str) -> list[Turn]:
        return [t for t in self._turns if t.speaker == speaker]

    def count_asked_by(self, role: str) -> int:
        return len(self.by_speaker(role))

    def transcript(self, last_n: int | None = None) -> str:
        turns = self._turns[-last_n:] if last_n else self._turns
        return "\n".join(f"[{t.turn_id}] {t.speaker}: {t.text}" for t in turns)

    def __len__(self) -> int:
        return len(self._turns)


class ClaimLedger:
    def __init__(self) -> None:
        self._claims: list[Claim] = []

    def add(self, turn_id: int, text: str, topic: str, quote: str) -> int:
        claim = Claim(
            claim_id=len(self._claims) + 1,
            turn_id=turn_id,
            text=text,
            topic=(topic or "general").strip().lower(),
            quote=quote,
        )
        self._claims.append(claim)
        return claim.claim_id

    def by_topic(self, topic: str, exclude_turn: int | None = None) -> list[Claim]:
        """Prior claims on the same topic — the narrow comparison set that
        makes contradiction checking reliable instead of imaginative."""
        topic = (topic or "general").strip().lower()
        return [
            c
            for c in self._claims
            if c.topic == topic and (exclude_turn is None or c.turn_id != exclude_turn)
        ]

    def topics(self) -> list[str]:
        return sorted({c.topic for c in self._claims})

    def all(self) -> list[Claim]:
        return list(self._claims)

    def __len__(self) -> int:
        return len(self._claims)


class EvidenceLedger:
    def __init__(self) -> None:
        self._evidence: list[Evidence] = []

    def add(
        self,
        turn_id: int,
        competency: str,
        concept: str,
        quote: str,
        polarity: str = "supports",
        weight: float = 1.0,
    ) -> None:
        self._evidence.append(
            Evidence(
                turn_id=turn_id,
                competency=competency,
                concept=concept,
                quote=quote,
                polarity=polarity,
                weight=weight,
            )
        )

    def by_competency(self) -> dict[str, list[Evidence]]:
        out: dict[str, list[Evidence]] = {}
        for e in self._evidence:
            out.setdefault(e.competency, []).append(e)
        return out

    def all(self) -> list[Evidence]:
        return list(self._evidence)

    def __len__(self) -> int:
        return len(self._evidence)


class FlagLedger:
    def __init__(self) -> None:
        self._flags: list[Flag] = []

    def add(
        self,
        turn_id: int,
        kind: str,
        detail: str,
        quote: str = "",
        quote_b: str = "",
        ref_turn_id: int | None = None,
        source: str = "",
    ) -> None:
        self._flags.append(
            Flag(
                turn_id=turn_id,
                kind=kind,
                detail=detail,
                quote=quote,
                quote_b=quote_b,
                ref_turn_id=ref_turn_id,
                at=time.time(),
                source=source,
            )
        )

    def for_turn(self, turn_id: int) -> list[Flag]:
        """Everything raised against one answer.

        The scoring penalty is computed from these rather than from a private
        verdict of its own: one flag store, so the conductor's routing and the
        candidate's marks can never disagree about whether an answer dodged.
        """
        return [f for f in self._flags if f.turn_id == turn_id]

    def of_kind(self, kind: str) -> list[Flag]:
        return [f for f in self._flags if f.kind == kind]

    def recent(self, kind: str, n: int = 2) -> list[Flag]:
        return self.of_kind(kind)[-n:]

    def turns_of_kind(self, kind: str) -> set[int]:
        return {f.turn_id for f in self._flags if f.kind == kind}

    def all(self) -> list[Flag]:
        return list(self._flags)

    def __len__(self) -> int:
        return len(self._flags)


class CodeLedger:
    """Snapshots of the candidate's editor during the coding round."""

    def __init__(self) -> None:
        self._snaps: list[CodeSnapshot] = []

    def add(self, source: str, language: str = "python", turn_id: int | None = None) -> None:
        self._snaps.append(
            CodeSnapshot(at=time.time(), language=language, source=source, turn_id=turn_id)
        )

    @property
    def latest(self) -> CodeSnapshot | None:
        return self._snaps[-1] if self._snaps else None

    def changed_since_last_comment(self, marker: int) -> bool:
        return len(self._snaps) > marker

    def all(self) -> list[CodeSnapshot]:
        return list(self._snaps)

    def __len__(self) -> int:
        return len(self._snaps)


class IntegrityLedger:
    """Browser-raised signals. No video is ever received or stored."""

    def __init__(self) -> None:
        self._events: list[IntegrityEvent] = []

    def add(
        self,
        kind: str,
        detail: str = "",
        seconds: float = 0.0,
        turn_id: int | None = None,
    ) -> None:
        self._events.append(
            IntegrityEvent(
                at=time.time(),
                kind=kind,
                detail=detail,
                seconds=seconds,
                turn_id=turn_id,
            )
        )

    def of_kind(self, kind: str) -> list[IntegrityEvent]:
        return [e for e in self._events if e.kind == kind]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self._events:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    def all(self) -> list[IntegrityEvent]:
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)
