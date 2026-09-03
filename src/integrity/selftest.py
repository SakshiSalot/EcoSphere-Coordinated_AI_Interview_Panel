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


# --- the written report --------------------------------------------------


def _report(**over) -> dict:
    base = {
        "session_id": "iv-test", "job_title": "Backend Engineer",
        "candidate": "Test Person", "status": "ended", "decision": None,
        "transcript": [
            {"turn_id": 1, "speaker": "technical", "text": "Tell me about it."},
            {"turn_id": 2, "speaker": "candidate", "text": "We cut p99 to 60ms."},
        ],
        "marked": True,
        "assessment": {
            "fraction": 0.62, "earned": 61.5, "total": 100, "available": 112.0,
            "answers": 9, "penalties": 0.0,
            "by_role": {"technical": {"earned": 40.0, "available": 60.0,
                                      "fraction": 0.67}},
            "evidence": [{"turn_id": 2, "role": "technical",
                          "concept": "Names a measurement", "quote": "p99 to 60ms"}],
            "flags": [],
        },
        "integrity": {"monitored": False, "signals": [], "coverage": {},
                      "concern": "unknown", "headline": "No data.",
                      "note": "An empty log is not a clean one."},
        "candidate_profile": {"full_name": "Test Person"},
        "note": None,
    }
    base.update(over)
    return base


def test_report_document() -> None:
    print("\n\033[1mthe assessment renders as a document\033[0m")
    from src.report import document

    page = document.build_html(_report())
    check("the candidate is named", "Test Person" in page)
    check("the score is on it", "62%" in page, page[:0])
    check("marks are broken down per interviewer", "Technical" in page)
    check("evidence carries its quote", "p99 to 60ms" in page)
    check("the transcript is appended", "Tell me about it." in page)
    check("and the AI panel is disclosed on the document itself",
          "every interviewer on this panel was an ai" in page.lower())
    check("an unrecorded decision says so rather than implying one",
          "No hiring decision has been recorded" in page)

    # The bug this check exists for: `str(value or "")` prints a real zero as
    # an empty cell, which reads as missing data rather than as none.
    zeroed = _report(candidate_profile={
        "full_name": "Zero Person",
        "github": {
            "username": "nobody", "url": "https://github.com/nobody",
            "ownership": {"proven": False},
            "profile": {"age_years": 0.2, "public_repos": 0},
            "activity": {"original": 0, "forks": 0, "pushed_last_year": 0},
            "resume_match": {"available": False},
        },
    })
    page = document.build_html(zeroed)
    check("a zero prints as a zero, not a blank", "0 / 0" in page,
          [l for l in page.splitlines() if "Own /" in l][:1])

    # Candidate answers are attacker-controlled text going into HTML.
    nasty = _report(transcript=[
        {"turn_id": 1, "speaker": "candidate",
         "text": "<script>alert('x')</script> & \"quoted\""},
    ])
    page = document.build_html(nasty)
    check("candidate text cannot inject markup",
          "<script>" not in page and "&lt;script&gt;" in page)
    check("and is still readable once escaped", "&amp;" in page)

    thin = _report(assessment={**_report()["assessment"], "answers": 2})
    check("a thin interview is warned about, in the document",
          "small sample" in document.build_html(thin))

    unmarked = _report(marked=False, assessment=None,
                       note="Not totalled yet.")
    page = document.build_html(unmarked)
    check("an unmarked interview still produces a document",
          "was not marked" in page and "Not totalled yet." in page)

    check("the filename is safe to write to disk",
          document.filename(_report(candidate_profile={"full_name": "A/B: C"}))
          == "AB-C-assessment.pdf",
          document.filename(_report(candidate_profile={"full_name": "A/B: C"})))

    # WeasyPrint needs system libraries. Where they are missing this is skipped
    # rather than failed — the endpoint returns a 503 saying so, and the suite
    # must still be runnable on a bare machine.
    try:
        pdf = document.build_pdf(_report())
    except (ImportError, OSError) as exc:
        print(f"  \033[33mSKIP\033[0m  PDF rendering unavailable here ({exc})")
        return
    check("it renders to a real PDF", pdf[:5] == b"%PDF-", str(pdf[:8]))
    check("with content in it", len(pdf) > 4000, str(len(pdf)))


def test_plan_survives_restart() -> None:
    """A curated interview is taken LATER. Later is after a restart.

    The whole point of writing five interviews on Monday is that five people
    sit them whenever suits them. The plan lived only in process memory, and
    `get_session()` on an unknown id returns a BLANK session rather than an
    error — so a redeploy between curating and joining silently swapped a
    personalised interview for a generic one, with nothing anywhere saying so.
    """
    print("\n\033[1ma curated interview survives the gateway restarting\033[0m")
    import src.state.session as sessions
    from src.gateway.app import _hydrate
    from src.intake import plan as intake_plan
    from src.state.models import CandidateProfile, JobSpec, PlannedQuestion

    session = sessions.reset_session("iv-restart")
    session.roles = ["technical", "product"]
    session.job = JobSpec(title="Backend Engineer", description="payments",
                          must_haves=["idempotency"])
    session.candidate = CandidateProfile(name="Harsh",
                                         resume_text="Built a retry layer in Redis",
                                         highlights=["retry layer"])
    session.plan = [
        PlannedQuestion(role="technical", topic="retries",
                        text="Walk me through the retry layer.", difficulty="hard"),
        PlannedQuestion(role="product", topic="impact",
                        text="Who felt the difference?", difficulty="medium"),
    ]
    db.save_plan("iv-restart", intake_plan.snapshot(session))

    sessions._SESSIONS.clear()          # the gateway restarts
    restored = _hydrate("iv-restart")
    back = sessions.get_session("iv-restart")

    check("the questions come back", restored == 2 and len(back.plan) == 2,
          str(restored))
    check("with their wording intact",
          back.plan[0].text == "Walk me through the retry layer.", back.plan[0].text)
    check("and their difficulty", back.plan[0].difficulty == "hard")
    check("the CV comes back", "Redis" in back.candidate.resume_text)
    check("the advert comes back", back.job.title == "Backend Engineer")
    check("the panel comes back", back.roles == ["technical", "product"])
    check("and the floor starts on the first of them",
          back.floor_holder == "technical", back.floor_holder)
    # A snapshot is taken before anybody answers, so every question in it is
    # unasked. Carrying a stale flag would skip questions nobody heard.
    check("nothing is marked as already asked",
          not any(q.asked for q in back.plan))

    back.turns.add("candidate", "I built it with Redis and idempotency keys.")
    turns_before = len(back.turns)
    check("hydrating a LIVE interview is a no-op",
          _hydrate("iv-restart") == 0 and len(back.turns) == turns_before)

    check("an interview with no saved plan hydrates to nothing, not an error",
          _hydrate("iv-never-existed") == 0)


def main() -> int:
    print("\n\033[1mINTEGRITY, VERIFICATION & REPORT SUITE\033[0m  ·  no keys, no network")
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
    test_report_document()
    test_plan_survives_restart()

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
