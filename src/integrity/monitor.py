"""Integrity signals raised by the candidate's own browser.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE: **no video ever leaves the
browser.** The camera frames are read by the candidate's own machine, the face
model runs there, and what reaches this server is a list of typed events —
"nobody in frame for 6 seconds", "the tab went to the background". There is no
upload, no recording, no frame buffer, and nothing here could produce one. That
is a privacy decision and also a liability one: a hiring product that ships
webcam footage of applicants to a hackathon server is a data breach waiting for
someone to notice it exists.

THE SECOND RULE: **advisory, never a verdict.** Nothing in this file can fail a
candidate, and integrity events are excluded from `scorer.PENALISED_KINDS` on
purpose — they cost zero marks. What they produce is a timestamped log the
operator reads next to the transcript. The reason is not squeamishness, it is
accuracy: every signal here has an innocent explanation that looks identical to
the guilty one. Someone looking away from the camera is thinking. Someone whose
tab lost focus got a calendar notification. Someone with a second face in frame
has a flatmate. A system that scores those as cheating is not detecting
dishonesty, it is detecting small rooms and busy households — and it will be
wrong most often about the people who can least afford it.

So each signal below carries a `but` field saying what it cannot distinguish,
and that text goes on the operator's screen next to the count. Making the
caveat as visible as the number is the whole design.

WHAT THIS CANNOT DO, said plainly because a demo invites the opposite claim: a
candidate who disables JavaScript, blocks the camera, or edits the page sends
no events at all, and an empty log is indistinguishable from a clean one.
Client-side monitoring cannot be made tamper-proof — anything running on a
machine the subject controls is advisory by construction. The heartbeat below
is the honest partial answer: the browser reports that it is still watching
every few seconds, so a period with no heartbeats shows up as *unmonitored*
rather than as *clean*. That turns "they turned it off" from an invisible
success into a visible gap.
"""

import json
import logging
import time
from dataclasses import dataclass

from src.state import db

log = logging.getLogger("integrity")


@dataclass(frozen=True)
class Signal:
    """One kind of thing the browser can notice.

    `weight` orders the log for the operator; it is NOT a score, and nothing
    multiplies it into a mark. `but` is the innocent reading, and it is
    mandatory — a signal nobody could write a `but` for is a signal we do not
    understand well enough to show anyone.
    """

    label: str
    weight: int
    means: str
    but: str


SIGNALS: dict[str, Signal] = {
    "tab_hidden": Signal(
        label="Left the interview tab",
        weight=3,
        means="The interview tab went to the background — another tab or "
              "window came to the front, or the screen locked.",
        but="A notification stealing focus looks exactly like reading an "
            "answer off a second tab. The duration is the useful part: two "
            "seconds is a popup, ninety is not.",
    ),
    "window_blur": Signal(
        label="Focus left the browser",
        weight=2,
        means="The browser window lost focus while the interview tab stayed "
              "visible — typically another application on top of it.",
        but="Clicking a second monitor, or an OS dialog appearing, does this "
            "too. On a two-screen desk it fires constantly and means nothing.",
    ),
    "multiple_faces": Signal(
        label="More than one person in frame",
        weight=4,
        means="The face model saw two or more faces in the camera image.",
        but="A flatmate crossing the room, a poster, or a face on a second "
            "screen behind them all count. It says someone was in shot, not "
            "that anyone was helping.",
    ),
    "no_face": Signal(
        label="Nobody in frame",
        weight=2,
        means="No face was found in the camera image for a sustained period.",
        but="Leaning out of shot to think, poor lighting, and a laptop lid at "
            "the wrong angle produce this. So does standing up to close a "
            "door.",
    ),
    "gaze_away": Signal(
        label="Looking away from the screen",
        weight=1,
        means="The irises sat well off centre for several seconds together.",
        but="This is the weakest signal here and it is kept last for that "
            "reason. People look away to think — it is one of the most common "
            "things a person does while recalling something. Read it only "
            "alongside the transcript.",
    ),
    "camera_off": Signal(
        label="Camera stopped",
        weight=2,
        means="The camera track ended mid-interview — permission revoked, "
              "device unplugged, or another application took it.",
        but="A laptop going to sleep or a video call starting elsewhere does "
            "this without anybody deciding anything.",
    ),
    "paste": Signal(
        label="Pasted into the editor",
        weight=3,
        means="Text was pasted into the coding round's editor.",
        but="Candidates paste their own earlier work, and the starter code "
            "itself is often copied around. The size of the paste is what "
            "distinguishes a variable name from a solution.",
    ),
    "fullscreen_exit": Signal(
        label="Left full screen",
        weight=1,
        means="The interview was taken out of full-screen mode.",
        but="Almost always someone adjusting their window. Logged for "
            "completeness rather than because it means much.",
    ),
    # Not a concern signal at all — the proof that monitoring was running.
    # Recorded like any other event so a gap in the record is visible.
    "heartbeat": Signal(
        label="Monitoring active",
        weight=0,
        means="The browser reported that camera and focus monitoring were "
              "still running.",
        but="Its absence is the interesting part, not its presence.",
    ),
}

# A hostile or simply broken client can post in a loop. The cap is per
# interview and generous enough that an honest twenty-minute session never
# approaches it — heartbeats every fifteen seconds are eighty events.
MAX_EVENTS = 600

# Clamped so one bad clock or a crafted payload cannot claim a candidate was
# out of frame for a year and drown the log.
MAX_SECONDS = 3600.0

# How often the browser is asked to say it is still watching. Used to turn
# missing heartbeats into monitored-versus-unmonitored time.
HEARTBEAT_SECONDS = 15.0

# A gap wider than this many heartbeats is reported as unmonitored time
# rather than silently smoothed over.
GAP_TOLERANCE = 2.5


def _load(session_id: str) -> list[dict]:
    row = db.one("SELECT integrity_json FROM interviews WHERE session_id = ?",
                 (session_id,))
    if not row or not row["integrity_json"]:
        return []
    try:
        return json.loads(row["integrity_json"])
    except json.JSONDecodeError:
        # A corrupt log is worse than no log if it makes the report unreadable.
        log.warning("integrity log for %s did not parse — starting fresh", session_id)
        return []


def _save(session_id: str, events: list[dict]) -> None:
    db.write("UPDATE interviews SET integrity_json = ? WHERE session_id = ?",
             (json.dumps(events), session_id))


def record(session_id: str, events: list[dict], stage: str = "voice") -> dict:
    """Accept a batch from the browser and append it to the interview's log.

    Everything is validated here rather than trusted, because this endpoint is
    reachable by the person being assessed. An unknown `kind` is dropped rather
    than stored: otherwise a candidate could post whatever labels they liked
    into their own report, and an operator reading "kind: excellent_candidate"
    in the evidence log would be reading something the candidate wrote.

    Timestamps are taken from the SERVER, not the payload. A browser clock is
    the candidate's to set, and the ordering of this log against the transcript
    is the only thing that makes it readable.
    """
    existing = _load(session_id)
    now = time.time()
    accepted = 0
    dropped = 0

    for raw in events[:MAX_EVENTS]:
        if len(existing) >= MAX_EVENTS:
            break
        kind = str((raw or {}).get("kind", "")).strip()
        if kind not in SIGNALS:
            dropped += 1
            continue

        seconds = raw.get("seconds", 0.0)
        try:
            seconds = max(0.0, min(MAX_SECONDS, float(seconds)))
        except (TypeError, ValueError):
            seconds = 0.0

        existing.append({
            "at": now,
            "kind": kind,
            # The browser's own idea of when it happened, kept only to order
            # events inside one batch. Never used as the authority.
            "client_at": raw.get("at"),
            "seconds": round(seconds, 1),
            "detail": str(raw.get("detail", ""))[:200],
            "stage": "coding" if stage == "coding" else "voice",
        })
        accepted += 1

    if accepted:
        _save(session_id, existing)

    if dropped:
        log.warning("integrity %s: dropped %d events with unknown kinds",
                    session_id, dropped)

    return {"accepted": accepted, "dropped": dropped, "total": len(existing),
            "full": len(existing) >= MAX_EVENTS}


def _coverage(events: list[dict]) -> dict:
    """How much of the session the browser was actually watching.

    This is the answer to "what if they just turned it off". Monitoring reports
    itself alive on a fixed interval, so the span between the first and last
    event is known, and any stretch inside it with no heartbeat is time nobody
    was watching. Reported as its own number rather than folded into the
    concern level, because unmonitored time is not evidence of anything — it is
    the absence of evidence, and the two must not look alike on a report.
    """
    beats = sorted(e["at"] for e in events if e["kind"] == "heartbeat")
    if len(beats) < 2:
        return {"monitored_seconds": 0.0, "gap_seconds": 0.0,
                "span_seconds": 0.0, "complete": False}

    span = beats[-1] - beats[0]
    gaps = 0.0
    for previous, current in zip(beats, beats[1:]):
        delta = current - previous
        if delta > HEARTBEAT_SECONDS * GAP_TOLERANCE:
            gaps += delta

    return {
        "monitored_seconds": round(max(0.0, span - gaps), 1),
        "gap_seconds": round(gaps, 1),
        "span_seconds": round(span, 1),
        # A little slack: one dropped batch on a flaky connection is not a
        # candidate disabling the monitor.
        "complete": gaps < HEARTBEAT_SECONDS * GAP_TOLERANCE,
    }


def summary(session_id: str) -> dict:
    """The integrity section of the report.

    Ordered by weight so the things worth reading come first, each with its
    count, its total duration, and the caveat that says what it cannot
    distinguish. The caveat travels WITH the number rather than sitting in a
    footnote, because a count with no caveat next to it is read as an
    accusation.
    """
    events = _load(session_id)
    if not events:
        return {
            "monitored": False,
            "signals": [],
            "events": [],
            "coverage": {"complete": False},
            "concern": "unknown",
            "headline": "No monitoring data was recorded for this interview.",
            "note": "Either the candidate declined the camera, their browser "
                    "blocked it, or the interview predates monitoring. An "
                    "empty log is not a clean one — it says nothing was "
                    "watching, not that nothing happened.",
        }

    grouped: dict[str, dict] = {}
    for e in events:
        if e["kind"] == "heartbeat":
            continue
        bucket = grouped.setdefault(e["kind"], {"count": 0, "seconds": 0.0})
        bucket["count"] += 1
        bucket["seconds"] += e.get("seconds", 0.0)

    signals = []
    for kind, bucket in grouped.items():
        signal = SIGNALS[kind]
        signals.append({
            "kind": kind,
            "label": signal.label,
            "count": bucket["count"],
            "seconds": round(bucket["seconds"], 1),
            "weight": signal.weight,
            "means": signal.means,
            "but": signal.but,
        })
    signals.sort(key=lambda s: (s["weight"], s["count"]), reverse=True)

    coverage = _coverage(events)
    concern, headline = _read(signals, coverage)

    return {
        "monitored": True,
        "signals": signals,
        # The raw log, newest last, so the operator can line it up against the
        # transcript rather than take the summary's word for it.
        "events": [
            {"at": e["at"], "kind": e["kind"], "label": SIGNALS[e["kind"]].label,
             "seconds": e.get("seconds", 0.0), "detail": e.get("detail", ""),
             "stage": e.get("stage", "voice")}
            for e in events if e["kind"] != "heartbeat"
        ],
        "coverage": coverage,
        "concern": concern,
        "headline": headline,
        "note": "None of this affects the candidate's score, by design — "
                "integrity events carry no marks. It is here to be read "
                "alongside the transcript by a person, who is the only one "
                "who can tell a flatmate walking past from someone being fed "
                "answers.",
    }


def _read(signals: list[dict], coverage: dict) -> tuple[str, str]:
    """Turn the log into one line, without turning it into an accusation.

    Bands rather than a percentage. A number like "82% integrity" invites
    exactly the comparison this module refuses to support — it would rank two
    candidates on how tidy their rooms are. The band says how much reading the
    operator should do, and nothing else.
    """
    if not signals:
        if not coverage["complete"]:
            return "unknown", (
                "Nothing was flagged, but monitoring was not running for the "
                "whole interview — treat this as no data rather than a clean "
                "record."
            )
        return "none", "Nothing was flagged. Monitoring ran throughout."

    serious = sum(s["count"] for s in signals if s["weight"] >= 3)
    minor = sum(s["count"] for s in signals if s["weight"] < 3)
    longest = max((s["seconds"] for s in signals if s["weight"] >= 3), default=0.0)

    top = signals[0]
    detail = f"{top['label'].lower()} ×{top['count']}"

    if serious >= 3 or longest >= 60:
        return "review", (
            f"Worth reading the transcript against the log — {detail}. "
            "Look at when these happened, not just how often."
        )
    if serious or minor >= 6:
        return "minor", (
            f"A few signals, none of them strong on its own — {detail}. "
            "Common in an ordinary interview taken at home."
        )
    return "none", (
        f"Only weak signals ({detail}), which people produce constantly "
        "while thinking."
    )


def clear(session_id: str) -> None:
    """Drop the log. Used when an interview is set up again on the same id, so
    a rerun does not inherit the previous run's events."""
    db.write("UPDATE interviews SET integrity_json = NULL WHERE session_id = ?",
             (session_id,))
