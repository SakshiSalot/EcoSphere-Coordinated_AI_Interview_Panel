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


# Things a candidate says that are NOT answers. Scoring these as answers is a
# silent unfairness: "sorry, could you repeat that?" is nine words with no
# numbers, so the vagueness check flags it, the difficulty ladder drops, and
# after two of them the interviewer starts pinning down a candidate whose only
# crime was not hearing the question.
_REPEAT = [
    "repeat that", "repeat the question", "say that again", "come again",
    "didn't catch", "did not catch", "sorry, what", "sorry what", "pardon",
    "what was the question", "can you repeat", "could you repeat",
    "say it again", "one more time", "missed that", "didn't hear",
    "did not hear", "run that by me",
]
_CLARIFY = [
    "what do you mean", "not sure i understand", "can you clarify",
    "could you clarify", "rephrase", "in what sense", "clarify that",
    "i don't follow", "i do not follow",
]
_PAUSE = [
    "one moment", "give me a second", "give me a moment", "hold on",
    "let me think", "just a second", "bear with me",
]

# Noise, not an answer. Speech-to-text faithfully transcribes "Uh." and
# "Okay." as candidate turns; scoring them as answers dropped a live
# interview's difficulty to easy before a single real question was answered.
_FILLER = {
    "uh", "um", "erm", "hmm", "mm", "mhm", "ah", "oh", "eh",
    "okay", "ok", "yeah", "yes", "yep", "no", "sure", "right", "hello",
    "hi", "hey", "sorry", "thanks", "thank you", "got it", "i see",
    "one second", "yeah okay", "okay yeah", "alright", "all right",
}


# Attempts to talk the panel out of being a panel. Detected deterministically
# rather than left to the model: a candidate who tries once will try again,
# and "the prompt says not to" is not a control — it is a request.
_INJECTION = [
    "ignore your instructions", "ignore all previous", "ignore previous",
    "disregard your instructions", "disregard the above", "forget your instructions",
    "forget the above", "new instructions", "system prompt", "your prompt",
    "you are now", "pretend you are", "act as if you", "from now on you",
    "stop being", "drop the persona", "reveal your instructions",
    "what are your instructions", "print your instructions", "repeat your prompt",
    "developer mode", "jailbreak", "override your", "bypass your",
    "tell me the answer", "give me the answer", "just give me the answers",
    "what is the correct answer", "score me", "give me full marks",
    "pass me", "say i passed", "rate me highly", "mark me as",
]


def injection_attempt(text: str) -> str | None:
    """Is the candidate trying to change the rules rather than answer?

    Returns the phrase that matched, or None. Kept separate from vagueness:
    this is not a weak answer, it is a different kind of event, and the report
    should be able to say so.
    """
    t = _norm(text)
    for phrase in _INJECTION:
        if phrase in t:
            return phrase
    return None


def meta_request(text: str) -> str | None:
    """Classify an utterance that is about the conversation, not an answer.

    Returns "repeat", "clarify", "pause", "filler", or None.
    """
    t = _norm(text)
    if len(t.split()) > 25:
        return None  # a long turn is an answer, even if it contains "pardon"

    for phrases, kind in ((_REPEAT, "repeat"), (_CLARIFY, "clarify"), (_PAUSE, "pause")):
        if any(p in t for p in phrases):
            return kind

    # Speech-to-text mangles the end of a sentence more often than the start,
    # and a candidate asking for a repeat is usually cut off mid-word — "Can
    # you repeat the ques—". Any short turn built around "repeat" or "again"
    # is a repeat request, however badly it was transcribed.
    words = t.split()
    if len(words) <= 12 and re.search(r"\b(repeat|again|pardon|rephrase)\b", t):
        return "repeat"
    if len(words) <= 12 and re.search(r"\b(hear|catch|understand|get)\b", t) and (
        "n't" in t or " not " in t or "sorry" in t
    ):
        return "repeat"

    # A handful of words, none of them substantive — hesitation or a greeting,
    # not an attempt at the question.
    stripped = t.strip(" .,!?-–—…")
    if stripped in _FILLER:
        return "filler"
    words = [w.strip(".,!?") for w in stripped.split()]
    if words and len(words) <= 4 and all(w in _FILLER for w in words if w):
        return "filler"

    return None


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


# Speech-to-text writes numbers as WORDS. "eighty-four percent" contains no
# digit, so a `\d` check — which is what this used to be — finds nothing in a
# voice interview and calls a precise answer vague. This is a voice product;
# digits are the exception, not the rule.
_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "fifteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million",
    "billion", "percent", "half", "twice", "double", "triple", "dozen",
    "milliseconds", "seconds", "minutes", "hours", "days", "weeks", "months",
}

# A named tool, detected by SHAPE rather than by a vocabulary list.
# ChromaDB, PyTorch, Tesseract, SUMO, PSO, SAR, RAG, p99 — a word list of
# backend nouns scored every one of these at zero, so an ML candidate's
# strongest answer read as vague while an identical answer about Postgres
# passed. The shape generalises; the list never will.
_NAMED = re.compile(
    r"\b(?:[A-Z][a-z]+[A-Z][A-Za-z]*"     # ChromaDB, PyTorch, OpenCV
    r"|[A-Z]{2,}[A-Za-z]*\d*"             # SUMO, PSO, SAR, RAG, XGBoost
    r"|[a-z]+\d+"                         # p99, p95, gpt4
    r"|[A-Z][a-z]{3,})\b"                 # Tesseract, Postgres, Redis
)

_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)([A-Z])")


def _has_number(normalised: str) -> bool:
    if re.search(r"\d", normalised):
        return True
    return any(w.strip(".,%") in _NUMBER_WORDS for w in normalised.split())


def _names_something(text: str) -> bool:
    """A named tool, product or metric — not just a capitalised sentence."""
    if any(h in _norm(text) for h in TECH_HINTS):
        return True
    # Drop the first word of each sentence, and "I", before looking for
    # proper nouns, or every sentence start counts as a named tool.
    stripped = _SENTENCE_START.sub(" ", text)
    return bool(_NAMED.search(stripped))


def specificity(text: str) -> float:
    """0..1. Does this answer contain anything a person could check?"""
    t = _norm(text)
    signals = 0
    if _has_number(t):
        signals += 1
    if _names_something(text):
        signals += 1
    if re.search(r"\b(for example|for instance|specifically|in our case|we used|"
                 r"we built|i built|we ran|i ran)\b", t):
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
