"""Instant heuristic detectors — no model call, no latency.

These run inline after every candidate turn. They are deliberately cheap and
deliberately conservative: they exist so the conductor has signals on the very
next turn, and so the system still behaves sensibly when the Gemini analysis
worker is rate-limited or slow.

The model-based scorer (rubric coverage with evidence quotes) and the
contradiction checker live elsewhere and run off the critical path.
"""

import re

PUNTING = [
    "it depends", "generally speaking", "various factors", "a number of things",
    "align the stakeholders", "align stakeholders", "at the end of the day",
    "best practices", "it varies", "case by case", "case-by-case",
    "there are many ways", "lots of factors", "we iterated", "kind of",
    "sort of", "you know", "stuff like that",
]

# Concrete-detail signals. An answer with none of these is saying very little
# however fluent it sounds.
TECH_HINTS = [
    "postgres", "mysql", "redis", "kafka", "rabbitmq", "s3", "kubernetes",
    "docker", "terraform", "nginx", "grpc", "graphql", "index", "shard",
    "cache", "queue", "lock", "transaction", "replica", "partition", "throughput",
    "latency", "p99", "p95", "timeout", "retry", "idempoten", "schema", "migration",
    "thread", "async", "goroutine", "memory", "cpu", "load balancer",
]

BUSINESS_HINTS = [
    "customer", "user", "client", "revenue", "business", "cost to the",
    "stakeholder", "adoption", "churn", "sla", "product", "market",
    "onboarding", "conversion", "support ticket", "complaint", "sales",
    "who it was for", "end user", "the team using",
]


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def specificity(text: str) -> float:
    """0..1. Does this answer contain anything a person could check?"""
    t = _norm(text)
    signals = 0
    if re.search(r"\d", t):
        signals += 1
    if any(h in t for h in TECH_HINTS):
        signals += 1
    if re.search(r"\b(for example|for instance|specifically|in our case|we used)\b", t):
        signals += 1
    if len(t.split()) > 45:
        signals += 1
    return min(1.0, signals / 3.0)


def punting_hits(text: str) -> list[str]:
    t = _norm(text)
    return [p for p in PUNTING if p in t]


def is_vague(text: str) -> tuple[bool, str, str]:
    """(vague?, why, quote).

    Two independent signals: a punting-phrase list catches deflection, and a
    specificity check catches fluent answers that say nothing — which is what
    smaller models reward if you only ask "was this a good answer?".
    """
    hits = punting_hits(text)
    spec = specificity(text)
    words = len(_norm(text).split())

    if hits and spec < 0.5:
        return True, f"deflecting phrase with no specifics: {hits[0]!r}", _quote_around(text, hits[0])
    if words >= 12 and spec == 0.0:
        return True, "no numbers, no named tools, no concrete example", _first_sentence(text)
    if words < 8:
        return True, "answer too short to contain any substance", text.strip()
    return False, "", ""


def has_technical_depth(text: str) -> bool:
    t = _norm(text)
    return bool(re.search(r"\d", t)) or sum(h in t for h in TECH_HINTS) >= 2


def mentions_business(text: str) -> bool:
    t = _norm(text)
    return any(h in t for h in BUSINESS_HINTS)


def is_technically_sound_but_no_business(text: str) -> tuple[bool, str]:
    """The problem statement's own example scenario, detected.

    A strong technical answer that never mentions a user, a customer or a
    cost. This is what hands the floor from Priya to Arjun, so it is the most
    load-bearing heuristic in the project.
    """
    if not has_technical_depth(text):
        return False, ""
    if mentions_business(text):
        return False, ""
    if len(_norm(text).split()) < 25:
        return False, ""  # too short to be a *strong* answer
    return True, _first_sentence(text)


def _first_sentence(text: str, limit: int = 180) -> str:
    t = " ".join(text.split())
    m = re.search(r"^(.{20,%d}?[.!?])\s" % limit, t)
    return (m.group(1) if m else t[:limit]).strip()


def _quote_around(text: str, phrase: str, window: int = 90) -> str:
    t = " ".join(text.split())
    i = t.lower().find(phrase)
    if i == -1:
        return _first_sentence(t)
    start = max(0, i - window // 2)
    return t[start : start + window].strip()
