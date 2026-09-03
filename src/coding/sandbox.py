"""Running the candidate's code — somewhere that is not our server.

Judge0's public Community Edition endpoint. It needs no key, returns a verdict
with runtime and memory, and — the part that matters — executes the code on
somebody else's isolated worker.

WHY NOT RUN IT LOCALLY. The gateway is on a public URL, because Agora has to
reach it. A local `subprocess` executor would be remote code execution by
design: anyone who found the address could run whatever they liked as the
gateway process, next to the .env holding every key we own. No amount of
resource limiting makes that an acceptable thing to ship.

Piston was the obvious alternative and its public API went whitelist-only in
February 2026 — checked, not assumed.

Everything here is failure-tolerant. A sandbox that is down must not end an
interview: the candidate keeps writing, the interviewer keeps talking about
what they wrote, and only the "Run" button stops working.
"""

import logging
import re

import httpx

log = logging.getLogger("sandbox")

BASE = "https://ce.judge0.com"
TIMEOUT = httpx.Timeout(45.0, connect=10.0)

# Judge0 language ids. Kept small on purpose: every extra language is another
# set of starter code and another way for a demo to go wrong.
LANGUAGES = {
    "python": {"id": 71, "label": "Python 3", "comment": "#"},
    "javascript": {"id": 63, "label": "JavaScript (Node)", "comment": "//"},
    "java": {"id": 62, "label": "Java", "comment": "//"},
    "cpp": {"id": 54, "label": "C++", "comment": "//"},
}

MAX_SOURCE = 20_000  # a screen of code, not a payload


class SandboxError(RuntimeError):
    pass


def languages() -> list[dict]:
    return [{"key": k, "label": v["label"]} for k, v in LANGUAGES.items()]


def run(source: str, language: str = "python", stdin: str = "") -> dict:
    """Execute once and return a plain result.

    Returns: {ok, verdict, stdout, stderr, time, memory}
    `ok` is True only when the program ran and exited cleanly — a compile error
    or a crash is a legitimate outcome to show the candidate, not an exception.
    """
    lang = LANGUAGES.get(language)
    if lang is None:
        raise SandboxError(f"unsupported language {language!r}")
    if not source.strip():
        raise SandboxError("there is no code to run")
    if len(source) > MAX_SOURCE:
        raise SandboxError("that is more code than this round expects")

    try:
        r = httpx.post(
            f"{BASE}/submissions?base64_encoded=false&wait=true",
            timeout=TIMEOUT,
            json={
                "language_id": lang["id"],
                "source_code": source,
                "stdin": stdin,
                # Judge0's own limits, so a runaway loop is its problem and not
                # a request of ours that never returns.
                "cpu_time_limit": 5,
                "wall_time_limit": 10,
                "memory_limit": 128000,
            },
        )
    except Exception as exc:
        log.warning("sandbox unreachable: %s", str(exc)[:160])
        raise SandboxError("the code sandbox is not responding — try again shortly")

    if r.status_code >= 400:
        log.warning("sandbox %s: %s", r.status_code, r.text[:200])
        raise SandboxError(f"the sandbox refused the request ({r.status_code})")

    d = r.json()
    verdict = (d.get("status") or {}).get("description", "Unknown")
    stdout = (d.get("stdout") or "").rstrip()
    stderr = (d.get("stderr") or "").rstrip()
    compile_output = (d.get("compile_output") or "").rstrip()

    return {
        "ok": verdict == "Accepted",
        "verdict": verdict,
        "stdout": stdout[:4000],
        "stderr": (stderr or compile_output)[:4000],
        "time": d.get("time"),
        "memory": d.get("memory"),
    }


def check(source: str, language: str, tests: list[dict]) -> dict:
    """Run the visible tests and report which passed.

    One submission per test rather than a generated harness: a harness has to
    be written per language, and a bug in ours would fail a candidate for our
    mistake.
    """
    results = []
    passed = 0
    for i, t in enumerate(tests, 1):
        expected = str(t.get("expected", "")).strip()
        try:
            out = run(source, language, str(t.get("stdin", "")))
        except SandboxError as exc:
            results.append({"n": i, "passed": False, "error": str(exc),
                            "input": t.get("stdin", ""), "expected": expected})
            continue
        got = out["stdout"].strip()
        ok = out["ok"] and _equivalent(got, expected)
        passed += ok
        results.append({
            "n": i, "passed": ok, "input": t.get("stdin", ""),
            "expected": expected, "got": got or out["stderr"][:200],
            "verdict": out["verdict"], "time": out["time"],
        })
    return {"passed": passed, "total": len(tests), "results": results}


def _equivalent(got: str, expected: str) -> bool:
    """Compare outputs the way a person would.

    Whitespace and quote style are not what is being examined — a candidate
    who prints `[0, 1]` where the test says `[0,1]` has solved the problem.
    """
    def norm(s: str) -> str:
        s = s.strip().replace("'", '"')
        return re.sub(r"\s+", "", s)

    return norm(got) == norm(expected)
