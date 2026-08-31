"""The difficulty ladder.

PS11 capability 7. This is the capability most often faked with a fixed
question ladder, and the one a judge can test in thirty seconds by
deliberately answering badly.
"""

import logging

from src.state.session import SessionState

log = logging.getLogger("difficulty")

ALPHA = 0.4          # weight on the newest answer
EASY_BELOW = 0.4
HARD_ABOVE = 0.7
HYSTERESIS = 2       # consecutive out-of-band turns before the level moves

ORDER = ["easy", "medium", "hard"]


def band_for(ewma: float) -> str:
    if ewma < EASY_BELOW:
        return "easy"
    if ewma > HARD_ABOVE:
        return "hard"
    return "medium"


def update(session: SessionState, normalised_score: float) -> str:
    """Fold one answer into the running average and maybe move the level.

    The exponentially weighted average smooths out a single unlucky answer,
    and the two-turn hysteresis stops the level oscillating between easy and
    hard on alternating turns — which looks broken rather than adaptive.
    """
    session.ewma = ALPHA * normalised_score + (1 - ALPHA) * session.ewma
    target = band_for(session.ewma)

    if target == session.difficulty:
        session.out_of_band = 0
        return session.difficulty

    session.out_of_band += 1
    if session.out_of_band < HYSTERESIS:
        log.info(
            "difficulty: %s -> %s pending (%d/%d), ewma=%.3f",
            session.difficulty, target, session.out_of_band, HYSTERESIS, session.ewma,
        )
        return session.difficulty

    # Move one step at a time, so a single disastrous answer cannot drop a
    # candidate from hard to easy in one turn.
    old = session.difficulty
    step = 1 if ORDER.index(target) > ORDER.index(old) else -1
    session.difficulty = ORDER[ORDER.index(old) + step]
    session.out_of_band = 0
    log.info("difficulty: %s -> %s (ewma=%.3f)", old, session.difficulty, session.ewma)
    return session.difficulty


def nudge(session: SessionState, direction: str) -> str:
    """A persona or a recruiter asking for a step directly, bypassing the
    average. Used by the adjust_difficulty tool and the observer's
    'push harder' button."""
    i = ORDER.index(session.difficulty)
    i = min(len(ORDER) - 1, i + 1) if direction == "up" else max(0, i - 1)
    session.difficulty = ORDER[i]
    session.out_of_band = 0
    return session.difficulty
