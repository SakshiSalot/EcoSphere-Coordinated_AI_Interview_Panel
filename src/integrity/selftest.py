"""Regression tests for monitoring and profile verification.

    python -m src.integrity.selftest

No keys and no network: the GitHub half tests the parsing, the matching and
the ownership-code derivation, all of which are pure. The one thing not
covered here is GitHub actually answering, which is not ours to test and would
make this suite fail whenever their status page went yellow.

Runs against a THROWAWAY DATABASE. Pointing `db.DB_PATH` at a temporary file
before the first connection means a test run cannot touch anybody's real
interviews — a suite that writes fixture events into the dev database would be
discovered the first time an operator opened a report and found "nobody in
frame" against an interview that never happened.
"""

import sys
import tempfile
import time
from pathlib import Path

from src.state import db

# BEFORE any import that connects. `connect()` reads this global on first use
# and caches the connection per thread, so redirecting it afterwards would
# silently do nothing.
_TMP = Path(tempfile.mkdtemp(prefix="echosphere-selftest-")) / "test.db"
db.DB_PATH = _TMP

from src.analysis import scorer                       # noqa: E402
from src.integrity import monitor                     # noqa: E402
from src.verify import github as gh                   # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  \033[32mPASS\033[0m  {name}")
    else:
        FAILED.append(name)
        print(f"  \033[31mFAIL\033[0m  {name}   {detail}")


def _interview(session_id: str) -> None:
    db.create_interview(session_id, None, None, "Test role")


# --- what the browser sends ---------------------------------------------


def test_recording() -> None:
    print("\n\033[1mevents from the browser are validated, not trusted\033[0m")
    session = "iv-record"
    _interview(session)

    result = monitor.record(session, [
        {"kind": "tab_hidden", "seconds": 12.0, "at": 1},
        {"kind": "gaze_away", "seconds": 4.0, "at": 2},
        # The candidate can reach this endpoint, so anything they invent must
        # not end up in their own report as if we had observed it.
        {"kind": "candidate_was_excellent", "seconds": 1.0},
        {"kind": "", "seconds": 1.0},
    ])
    check("valid events are stored", result["accepted"] == 2, str(result))
    check("invented event kinds are dropped", result["dropped"] == 2, str(result))

    monitor.record(session, [{"kind": "no_face", "seconds": 10_000_000}])
    events = monitor._load(session)
    clamped = [e for e in events if e["kind"] == "no_face"][0]
    check("a wild duration is clamped, not stored",
          clamped["seconds"] <= monitor.MAX_SECONDS, str(clamped["seconds"]))

    check("the server's clock is the authority",
          all(abs(e["at"] - time.time()) < 30 for e in events))
    check("the browser's own timestamp is kept separately",
          events[0]["client_at"] == 1)


def test_cap() -> None:
    print("\n\033[1ma runaway client cannot fill the database\033[0m")
    session = "iv-cap"
    _interview(session)

    for _ in range(4):
        monitor.record(session, [{"kind": "gaze_away", "seconds": 4.0}] * 300)

    events = monitor._load(session)
    check("the log stops at the cap", len(events) <= monitor.MAX_EVENTS,
          str(len(events)))
    check("and says so, so the browser can stop trying",
          monitor.record(session, [{"kind": "gaze_away"}])["full"] is True)


# --- what the operator reads --------------------------------------------


def test_summary() -> None:
    print("\n\033[1mthe summary carries its caveats with it\033[0m")
    session = "iv-summary"
    _interview(session)

    monitor.record(session, [
        {"kind": "gaze_away", "seconds": 5.0},
        {"kind": "gaze_away", "seconds": 6.0},
        {"kind": "multiple_faces", "seconds": 3.0},
        {"kind": "heartbeat", "seconds": 15.0},
    ])
    s = monitor.summary(session)

    check("signals are grouped by kind", len(s["signals"]) == 2, str(s["signals"]))
    check("counts and durations are totalled",
          any(x["kind"] == "gaze_away" and x["count"] == 2 and x["seconds"] == 11.0
              for x in s["signals"]), str(s["signals"]))
    check("the strongest signal is read first",
          s["signals"][0]["kind"] == "multiple_faces", s["signals"][0]["kind"])
    check("heartbeats are proof of monitoring, not a signal",
          all(x["kind"] != "heartbeat" for x in s["signals"]))

    # The whole design of the module: a count on a hiring screen is read as an
    # accusation unless the reason it is usually nothing sits beside it.
    check("every signal shown carries what it cannot distinguish",
          all(x["but"] and x["means"] for x in s["signals"]))
    check("every signal defined carries one too",
          all(sig.but and sig.means for sig in monitor.SIGNALS.values()))
    check("the report says the score is untouched",
          "cannot change" in s["note"] or "no marks" in s["note"] or
          "carry no marks" in s["note"], s["note"])


def test_empty_is_not_clean() -> None:
    print("\n\033[1man empty log is not a clean one\033[0m")
    session = "iv-empty"
    _interview(session)

    s = monitor.summary(session)
    check("nothing recorded reports as not monitored", s["monitored"] is False)
    check("and as unknown rather than none", s["concern"] == "unknown", s["concern"])
    # The single most likely way to misread this screen.
    check("and says so in words the operator will read",
          "not a clean one" in s["note"], s["note"])


def test_coverage() -> None:
    print("\n\033[1mswitching monitoring off shows up as a gap\033[0m")
    session = "iv-gap"
    _interview(session)

    # Heartbeats with a hole in the middle: the browser stopped reporting for
    # two minutes and came back.
    now = time.time()
    events = [
        {"at": now + t, "kind": "heartbeat", "client_at": None,
         "seconds": 15.0, "detail": "", "stage": "voice"}
        for t in (0, 15, 30, 150, 165)
    ]
    monitor._save(session, events)

    coverage = monitor._coverage(events)
    check("the gap is measured", coverage["gap_seconds"] >= 100,
          str(coverage))
    check("and coverage is reported incomplete", coverage["complete"] is False)
    check("monitored time excludes the gap",
          coverage["monitored_seconds"] < coverage["span_seconds"], str(coverage))

    s = monitor.summary(session)
    check("no flags plus a gap is still 'unknown', not 'none'",
          s["concern"] == "unknown", s["concern"])

    # And the ordinary case, where nothing was missed.
    clean = [
        {"at": now + t, "kind": "heartbeat", "client_at": None,
         "seconds": 15.0, "detail": "", "stage": "voice"}
        for t in (0, 15, 30, 45)
    ]
    check("continuous heartbeats read as complete",
          monitor._coverage(clean)["complete"] is True)


def test_never_costs_marks() -> None:
    print("\n\033[1mmonitoring is advisory and cannot move a score\033[0m")
    check("integrity is not a penalised flag kind",
          "integrity" not in scorer.PENALISED_KINDS, str(scorer.PENALISED_KINDS))
    check("no signal carries anything a score could multiply",
          all(not hasattr(sig, "penalty") for sig in monitor.SIGNALS.values()))

    session = "iv-clear"
    _interview(session)
    monitor.record(session, [{"kind": "no_face", "seconds": 5.0}])
    monitor.clear(session)
    check("a rerun does not inherit the last run's flags",
          monitor.summary(session)["monitored"] is False)


# --- GitHub --------------------------------------------------------------


def test_username_parsing() -> None:
    print("\n\033[1mGitHub usernames: accept what people paste\033[0m")
    for raw, want in [
        ("https://github.com/torvalds", "torvalds"),
        ("https://www.github.com/torvalds/", "torvalds"),
        ("github.com/torvalds/linux", "torvalds"),
        ("@octocat", "octocat"),
        ("  hrshraj  ", "hrshraj"),
    ]:
        check(f"{raw!r} -> {want}", gh.normalise(raw) == want, gh.normalise(raw))

    # This value is interpolated into an API path.
    check("a path cannot be smuggled through", not gh.is_username("a/../b"))
    check("nor a leading hyphen", not gh.is_username("-nope"))
    check("nor forty characters", not gh.is_username("x" * 40))
    check("an ordinary username passes", gh.is_username("hrsh-raj"))


def test_ownership_code() -> None:
    print("\n\033[1mthe ownership code is per person AND per account\033[0m")
    a = gh.challenge_for(1, "octocat")
    check("stable across calls", a == gh.challenge_for(1, "octocat"))
    check("case does not change it", a == gh.challenge_for(1, "OctoCat"))
    # If one code verified any account, publishing it once would let a
    # candidate claim somebody else's profile.
    check("a different account gets a different code",
          a != gh.challenge_for(1, "torvalds"))
    check("a different person gets a different code",
          a != gh.challenge_for(2, "octocat"))
    check("it is recognisable in a bio", a.startswith(gh.CHALLENGE_PREFIX))


def test_resume_cross_check() -> None:
    print("\n\033[1mmatching a resume against public code\033[0m")

    languages = {"Go": 3, "Python": 5, "C++": 1}
    match = gh._resume_cross_check(
        "Backend work in Python. I also used Django and Rails on a side "
        "project, and wrote a parser in C++.",
        languages,
    )
    got = {c["language"] for c in match["corroborated"]}

    # The three substring traps that would make this look unserious the first
    # time anybody read the output.
    check("'Go' is not found inside 'Django'", "Go" not in got, str(got))
    check("'C++' is matched properly", "C++" in got, str(got))
    check("Python is corroborated", "Python" in got, str(got))
    check("Go lands in only-on-GitHub",
          "Go" in {c["language"] for c in match["only_on_github"]})

    check("'Golang' counts as Go",
          "Go" in {c["language"] for c in
                   gh._resume_cross_check("Five years of Golang.", {"Go": 2})
                   ["corroborated"]})

    claimed = gh._resume_cross_check("Ten years of Java at a bank.", {"Python": 1})
    check("a resume claim with no public repo is reported",
          "java" in claimed["claimed_not_seen"], str(claimed["claimed_not_seen"]))
    # The single most important sentence in the payload: without it an operator
    # reads "claimed, not seen" as "lied".
    check("and reported with the reason it is usually innocent",
          "private" in claimed["note"], claimed["note"])

    check("no resume means no comparison, not an empty one",
          gh._resume_cross_check("", {"Go": 1})["available"] is False)


def test_linkedin_claims_nothing() -> None:
    print("\n\033[1mLinkedIn is a link, and says so\033[0m")
    result = gh.linkedin("linkedin.com/in/someone")
    check("a bare host is made a URL", result["url"].startswith("https://"))
    check("it is never marked verified", result["status"] == "unverified",
          result["status"])
    check("and the payload says why nothing was checked",
          "no public API" in result["note"])

    try:
        gh.linkedin("https://definitely-not-linkedin.example/in/me")
    except gh.VerifyError:
        check("a non-LinkedIn URL is refused", True)
    else:
        check("a non-LinkedIn URL is refused", False, "it was accepted")

    check("empty stays empty", gh.linkedin("")["status"] == "none")


def main() -> int:
    print("\n\033[1mINTEGRITY & VERIFICATION SUITE\033[0m  ·  no keys, no network")
    print("─" * 70)

    test_recording()
    test_cap()
    test_summary()
    test_empty_is_not_clean()
    test_coverage()
    test_never_costs_marks()
    test_username_parsing()
    test_ownership_code()
    test_resume_cross_check()
    test_linkedin_claims_nothing()

    print("\n" + "─" * 70)
    if FAILED:
        print(f"\033[31m{len(FAILED)} of {len(PASSED) + len(FAILED)} checks failed\033[0m")
        for name in FAILED:
            print(f"  · {name}")
        return 1
    print(f"\033[32mall {len(PASSED)} checks passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
