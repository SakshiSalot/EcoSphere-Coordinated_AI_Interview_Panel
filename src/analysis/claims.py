"""Checkable claims, and when two of them cannot both be true.

PS11 asks the panel to notice when a candidate contradicts themselves. There
was a tool for it from the start — `flag_contradiction` — and it almost never
fired, for a reason worth stating plainly: asking a small model to spot a
conflict with something said eleven turns ago, while also conducting an
interview, is asking it to do the one thing it is worst at. It has to hold the
whole transcript in mind, notice the collision unprompted, and volunteer a tool
call nobody asked for. In practice it just keeps interviewing.

So the work is moved to where it belongs. This module makes the comparison
NARROW and the trigger MECHANICAL:

  TIER 1 — arithmetic, inline, no model. Every answer is scanned for numbers
  with units, each filed under a topic. When a later answer states a different
  outcome for a topic the candidate already quantified, that is a conflict a
  regular expression can find, and it is found on the turn it happens — so the
  conductor can route the challenge into the very next question.

  TIER 2 — the judge, asynchronous. Contradictions with no numbers in them
  ("I led that migration" / "I wasn't really involved in the migration") need
  reading, not arithmetic. That call is made off the critical path, and only
  for answers whose topics overlap something already claimed, so it costs a
  request per relevant turn rather than one per turn.

WHY THE LEDGER FINALLY GETS USED. `ClaimLedger.by_topic()` was written on day
one with a docstring saying it is "what makes contradiction detection work on a
small model" — and nothing ever wrote to it. Everything below exists to fill
it, because the topic index is exactly what turns "compare this against the
whole interview" into "compare this against the two other things they said
about latency".

FALSE POSITIVES ARE THE EXPENSIVE FAILURE. A missed contradiction costs a
follow-up question. A wrong one has an interviewer telling a candidate they
said something they did not, on a recording, which is worse than useless — so
tier 1 requires the two claims to share subject wording as well as a topic, and
every flag carries both verbatim quotes.
"""

import logging
import re
from dataclasses import dataclass

from src.analysis.quick import _NAMED, _SENTENCE_START

log = logging.getLogger("analysis.claims")

# How far apart two stated outcomes must be before arithmetic alone calls it a
# contradiction. Two-to-one, because people round: "about 50ms" and "60ms" are
# the same claim told twice, and flagging that pair would train an operator to
# ignore the whole section.
STARK_RATIO = 2.0

# Topics exist to narrow the comparison, not to classify the interview. Each is
# a set of words that means "this sentence is quantifying THAT". A number with
# no topic word near it is not filed at all — an unattached "about 15" is not a
# claim anyone can be held to.
TOPICS: dict[str, tuple[str, ...]] = {
    # "took" is deliberately NOT here. It reads as latency in "it took 400ms"
    # and as duration in "it took three weeks", and guessing wrong files a
    # claim under a topic it will never be compared against.
    "latency": ("latency", "p99", "p95", "p50", "response time", "round trip",
                "ttfb", "millisecond", "ms ", "ms.", "ms,"),
    "throughput": ("throughput", "per second", "/sec", "per s", "qps", "rps",
                   "events", "requests", "traffic", "peak"),
    "team_size": ("team", "engineers", "developers", "people", "headcount",
                  "reports", "squad"),
    "duration": ("weeks", "months", "years", "days", "sprint", "quarter",
                 "took us", "spent"),
    "availability": ("uptime", "availability", "sla", "nines"),
    "error_rate": ("error rate", "errors", "failure rate", "failed", "dropped",
                   "timeouts"),
    "cost": ("cost", "spend", "budget", "bill", "savings", "saved", "$", "£"),
    "scale": ("users", "customers", "tenants", "rows", "records", "gb", "tb",
              "million", "billion"),
}

# Unit families. Two claims only compare when their units are commensurable —
# "three weeks" and "three engineers" are both a 3 and have nothing to do with
# each other.
UNITS: dict[str, str] = {
    "ms": "time", "millisecond": "time", "milliseconds": "time",
    "s": "time", "sec": "time", "secs": "time", "second": "time",
    "seconds": "time", "m": "time", "min": "time", "mins": "time",
    "minute": "time", "minutes": "time", "hour": "time", "hours": "time",
    "day": "time", "days": "time", "week": "time", "weeks": "time",
    "month": "time", "months": "time", "year": "time", "years": "time",
    "%": "ratio", "percent": "ratio", "pc": "ratio",
    "qps": "rate", "rps": "rate", "eps": "rate",
}

# Everything time-like reduced to seconds, so "400ms" and "0.4 seconds" are the
# same quantity rather than a 1000x contradiction.
TIME_SCALE: dict[str, float] = {
    "ms": 0.001, "millisecond": 0.001, "milliseconds": 0.001,
    "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
    "hour": 3600.0, "hours": 3600.0,
    "day": 86400.0, "days": 86400.0,
    "week": 604800.0, "weeks": 604800.0,
    "month": 2629800.0, "months": 2629800.0,
    "year": 31557600.0, "years": 31557600.0,
}

_MAGNITUDE = {"k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9}

# A number, an optional magnitude suffix, and an optional unit. The unit is
# taken only when it is glued to the number or immediately after it, so "60ms"
# and "60 ms" both parse and "60 engineers who used ms" does not claim ms.
#
# The magnitude carries a negative lookahead for a reason that cost a debugging
# pass: without it, "60ms" matches `m` as the MAGNITUDE and `s` as the unit, and
# sixty milliseconds is recorded as sixty million seconds. The suffix only
# counts when nothing wordlike follows it.
_MEASURE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*"
    r"(?:(k|m|bn|b)(?![a-z]))?\s*"
    r"(ms|milliseconds?|s|secs?|seconds?|mins?|minutes?|hours?|days?|weeks?|"
    r"months?|years?|%|percent|qps|rps|eps)?"
    r"(?![\w])",
    re.I,
)

# Words that carry no subject information. Without this the overlap check
# passes on "the" and "we", and every pair of sentences looks related.
_STOP = {
    "the", "a", "an", "and", "or", "but", "we", "i", "our", "us", "it", "its",
    "that", "this", "was", "were", "is", "are", "be", "been", "to", "from",
    "at", "on", "in", "of", "for", "with", "by", "about", "after", "before",
    "then", "than", "so", "as", "up", "down", "out", "over", "under", "into",
    "had", "has", "have", "did", "do", "does", "got", "get", "went", "took",
    "really", "actually", "basically", "just", "very", "quite", "some", "any",
    "not", "no", "never", "always", "would", "could", "should", "can", "will",
    "when", "where", "which", "what", "who", "how", "there", "their", "they",
    "you", "your", "me", "my", "his", "her", "them", "one", "two", "per",
    "around", "roughly", "approximately", "like", "sort", "kind", "thing",
    "things", "lot", "bit", "much", "many", "more", "less", "most", "least",
}

# Phrases that say "this is the number that stuck", used to pick which value in
# a sentence is the claim's outcome. "From 400ms to 60ms" claims 60.
_OUTCOME_BEFORE = ("to", "down to", "up to", "under", "below", "above", "over",
                   "closer to", "around", "about", "at", "reached", "hit",
                   "ended up", "settled", "now")


@dataclass(frozen=True)
class Measurement:
    """One quantified claim, reduced far enough to compare with another."""

    topic: str
    family: str          # time | ratio | rate | count
    value: float         # normalised: seconds for time, raw otherwise
    raw: str             # what they actually said, for the quote
    subject: frozenset   # content words near it, to stop unrelated collisions
    sentence: str


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _topic_of(sentence: str) -> str | None:
    low = f" {sentence.lower()} "
    for topic, words in TOPICS.items():
        if any(w in low for w in words):
            return topic
    return None


def _subject_words(sentence: str) -> frozenset:
    """Content words that say what the sentence is ABOUT.

    Units and bare numbers are excluded deliberately: two sentences both
    containing "sec" are not thereby about the same thing, and letting the unit
    satisfy the overlap check is precisely how "12k events per second" gets
    matched against "the retry queue does 3k/sec".
    """
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", sentence.lower())
    return frozenset(
        w for w in words
        if w not in _STOP and w not in UNITS and not w.isdigit()
    )


def _normalise(value: float, magnitude: str | None, unit: str | None) -> tuple[str, float]:
    if magnitude:
        value *= _MAGNITUDE.get(magnitude.lower(), 1.0)
    if not unit:
        return "count", value
    unit = unit.lower()
    family = UNITS.get(unit, "count")
    if family == "time":
        return "time", value * TIME_SCALE.get(unit, 1.0)
    return family, value


def _outcome(found: list[tuple[float, str, str]], sentence: str) -> tuple[float, str] | None:
    """Which of the numbers in this sentence is the claim.

    "We took p99 from 400ms to 60ms" contains two numbers and asserts one
    result. Comparing every number against every other number across the
    interview would flag the before-value against the after-value and call the
    candidate a liar for describing an improvement.
    """
    if not found:
        return None
    if len(found) == 1:
        return found[0][0], found[0][1]

    low = sentence.lower()
    best = None
    for value, raw, _unit in found:
        where = low.find(raw.lower())
        if where <= 0:
            continue
        before = low[max(0, where - 14):where].strip()
        if any(before.endswith(marker) for marker in _OUTCOME_BEFORE):
            best = (value, raw)
    # Falling back to the LAST number rather than the smallest: English puts
    # the outcome at the end of "from X to Y" regardless of direction, so this
    # works for both "cut latency to 60ms" and "grew throughput to 12k".
    return best or (found[-1][0], found[-1][1])


def measurements(text: str) -> list[Measurement]:
    """Every quantified claim in one answer.

    Deliberately conservative. A number with no unit and no topic word beside
    it is discarded rather than guessed at, because a claim nobody could be
    held to is not worth the risk of holding them to it.
    """
    out: list[Measurement] = []
    for sentence in _sentences(text):
        topic = _topic_of(sentence)
        if topic is None:
            continue

        found: list[tuple[float, str, str]] = []
        for match in _MEASURE.finditer(sentence):
            number, magnitude, unit = match.groups()
            try:
                value = float(number.replace(",", "."))
            except ValueError:
                continue
            family, value = _normalise(value, magnitude, unit)
            found.append((value, match.group(0).strip(), family))

        if not found:
            continue

        picked = _outcome(found, sentence)
        if picked is None:
            continue
        value, raw = picked
        family = next((f for v, r, f in found if r == raw), "count")

        out.append(Measurement(
            topic=topic,
            family=family,
            value=value,
            raw=raw,
            subject=_subject_words(sentence),
            sentence=sentence[:300],
        ))
    return out


@dataclass(frozen=True)
class Conflict:
    topic: str
    earlier_turn: int
    earlier_quote: str
    later_quote: str
    detail: str


def conflict_between(
    earlier: Measurement, later: Measurement, earlier_turn: int
) -> Conflict | None:
    """Do these two claims disagree, by arithmetic alone?

    Four conditions, and all of them have to hold. Each one exists because
    dropping it produced a false positive in testing:

      same topic     — otherwise "4 engineers" contradicts "4 weeks"
      same family    — otherwise "3 seconds" contradicts "3 percent"
      shared subject — otherwise "12k events/sec" contradicts "3k/sec" from a
                       different system entirely
      far apart      — otherwise "about 50ms" contradicts "60ms", and rounding
                       becomes dishonesty
    """
    if earlier.topic != later.topic or earlier.family != later.family:
        return None

    shared = earlier.subject & later.subject
    if not shared:
        return None

    low, high = sorted((earlier.value, later.value))
    if low <= 0:
        return None
    if high / low < STARK_RATIO:
        return None

    return Conflict(
        topic=later.topic,
        earlier_turn=earlier_turn,
        earlier_quote=earlier.sentence,
        later_quote=later.sentence,
        detail=(
            f"On {later.topic.replace('_', ' ')} they said {earlier.raw} "
            f"earlier and {later.raw} now — the two cannot both be right."
        ),
    )


# --- the CV is a claim too -----------------------------------------------
#
# The most useful contradiction in a hiring interview is not between two spoken
# answers at all: it is between the CV and the person. A resume saying "built a
# retry layer in Python" is a claim the candidate made in writing, and "I've
# never really worked with retries" contradicts it as squarely as any pair of
# numbers. Only comparing answers against each other misses the entire class.

# Saying you do not know something. Deliberately about KNOWLEDGE and
# EXPERIENCE, not about certainty — the difference between the two is the
# difference between a contradiction and an honest hedge.
_DISCLAIMERS = (
    "i don't know", "i dont know", "i do not know",
    "i have no idea", "no idea",
    "i'm not familiar", "im not familiar", "i am not familiar",
    "not familiar with", "never used", "never worked with",
    "never worked on", "never really used", "never really worked",
    "haven't used", "havent used", "have not used",
    "haven't worked", "havent worked", "have not worked",
    "didn't work on", "didnt work on", "did not work on",
    "wasn't me", "wasnt me", "that wasn't mine", "not my area",
    "no experience with", "i've not used", "ive not used",
    "i wouldn't know", "i wouldnt know", "never touched",
    "i haven't really", "i havent really",
)

# "I don't know the exact number" is a person being precise about their memory,
# not disowning their CV. Every one of these follows a disclaimer and turns it
# into a hedge, and without them a careful candidate is flagged for honesty.
_HEDGE_AFTER = (
    "if", "whether", "how many", "how much", "the exact", "exactly",
    "the number", "for sure", "off the top", "the specifics", "the details",
    "what the", "which", "when", "why", "who", "that well", "much about it",
    "for certain", "the figure", "precisely",
)

# Where the disclaimer has to sit relative to the thing being disclaimed.
# Wider than a clause, narrower than a paragraph: "I've never used Kafka, but
# I did build the retry layer" must flag Kafka and not the retry layer.
_NEAR = 140


# Words that pass the named-tool shape test on a CV and claim nothing: job
# titles, section headings, and the verbs every bullet point starts with. A CV
# is not prose — "Built a retry layer" opens a LINE, so the capital survives
# sentence-based stripping and "built" becomes a technology the candidate is
# then accused of disowning.
_RESUME_GENERIC = {
    "engineer", "engineering", "developer", "development", "manager",
    "management", "senior", "junior", "lead", "principal", "staff", "intern",
    "backend", "frontend", "fullstack", "full", "stack", "software",
    "experience", "education", "skills", "projects", "summary", "profile",
    "objective", "achievements", "responsibilities", "certifications",
    "university", "college", "school", "bachelor", "master", "degree",
    "company", "client", "team", "teams", "project", "role", "work", "worked",
    "built", "designed", "developed", "created", "implemented", "delivered",
    "managed", "improved", "reduced", "increased", "migrated", "sharded",
    "instrumented", "owned", "shipped", "wrote", "present", "current",
    # Months and ordinals, which litter a dated CV.
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "first", "second",
    "third", "fourth", "year", "years", "summer", "winter", "ongoing",
    # Generic nouns that pass the capitalised-word test and mean nothing on
    # their own. These are the dangerous ones: they appear in ordinary speech,
    # so without them "I've never worked with that kind of data" is read as
    # disowning the CV.
    "data", "code", "time", "scale", "system", "systems", "model", "models",
    "tools", "tool", "control", "training", "science", "learning", "machine",
    "information", "computer", "languages", "library", "libraries",
    "frameworks", "framework", "concepts", "algorithms", "structures",
    "database", "databases", "office", "national", "central", "government",
    "ministry", "region", "remote", "freelance", "hackathon", "prize",
    "final", "detection", "generation", "evaluation", "optimization",
    "automation", "classification", "validation", "operations", "workflow",
    "leadership", "competencies", "artificial", "intelligence", "deep",
    "feature", "cycle", "signal", "branch", "capital", "centre", "center",
    "corporation", "transport", "transportation", "logistics", "writing",
    "hybrid", "cross", "supervised", "enthusiast", "trainer", "auditor",
}

# The named-tool regex has four branches, and they are not equally trustworthy.
# CamelCase, ALL-CAPS and word-plus-digit name a THING almost every time —
# ChromaDB, SUMO, XGBoost, p99. A plainly capitalised word is far weaker: it
# catches Postgres and Tesseract, but also Delhi, June and Machine. So the
# generic filter is applied only to that branch, and the precise ones are kept
# whatever they look like.
_PRECISE = re.compile(
    r"^(?:[a-z]+[A-Z][A-Za-z]*"        # ChromaDB, PyTorch  (pre-lowercasing)
    r"|[A-Z]{2,}[A-Za-z]*\d*"          # SUMO, PSO, RAG
    r"|[a-z]+\d+)$"                    # p99, gpt4
)


def resume_terms(resume_text: str) -> set[str]:
    """The distinctive things a CV claims, by SHAPE rather than a word list.

    Reuses the same regex the vagueness heuristic uses to spot a named tool,
    for the same reason it exists there: a vocabulary of backend nouns scored
    ChromaDB, PyTorch and SUMO at zero, and any list we write will be wrong for
    the next candidate.

    Two things are stripped before the shape test, and both were found by
    reading the output rather than by reasoning about it. Sentence-initial
    capitals, obviously — but a CV is bullet points, not prose, so LINE-initial
    capitals matter more: "Migrated ingestion to RabbitMQ" made `migrated` a
    claimed technology. The generic list above catches what is left.
    """
    if not resume_text:
        return set()
    # Line starts first: on a CV they are the common case, and nearly always a
    # verb or a heading rather than something claimed.
    stripped = re.sub(r"(?m)^\s*[-•*\d.\s]*([A-Z])", " ", resume_text)
    stripped = _SENTENCE_START.sub(" ", stripped)

    terms = set()
    for match in _NAMED.finditer(stripped):
        raw = match.group(0)
        term = raw.lower()
        if len(term) < 3 or term in _STOP:
            continue
        # A precisely-shaped name is kept whatever it is; a plainly
        # capitalised word has to survive the generic filter.
        if not _PRECISE.match(raw) and term in _RESUME_GENERIC:
            continue
        terms.add(term)
    return terms


def disclaimer_in(text: str) -> str | None:
    """The phrase where they disown knowledge, or None if they only hedged."""
    low = " ".join(text.lower().split())
    for phrase in _DISCLAIMERS:
        at = low.find(phrase)
        while at != -1:
            after = low[at + len(phrase):].lstrip(" ,")
            if not any(after.startswith(h) for h in _HEDGE_AFTER):
                return phrase
            at = low.find(phrase, at + 1)
    return None


# The disclaimer refers back to whatever the interviewer just named, rather
# than naming anything itself: "I never touched that", "no idea about it".
# Only in that case may the term be taken from the QUESTION — and the anaphor
# has to be bare. "I've never worked with that kind of data" also contains
# "that", but it names its own subject, and reading the question's subject into
# it flags the candidate for something they did not say.
_BARE_ANAPHOR = re.compile(
    r"\s*(?:with|on|about|in|of|to)?\s*"
    r"(?:any of |much of |all of )?(?:that|it|this|those|them)"
    r"\s*(?:[,.;!?]|$|\s+(?:to be honest|honestly|really|at all|much|"
    r"i'm afraid|im afraid|sorry|either|before|personally))"
)


def resume_conflict(
    answer: str, question: str, terms: set[str]
) -> tuple[str, str] | None:
    """Did they just disown something their own CV claims?

    Returns (the disclaimer, the term) or None.

    TWO PASSES, and the order is what keeps this honest. First the ANSWER is
    searched near the disclaimer, because a candidate who names the thing they
    are disowning has told us exactly what they mean. Only when the answer
    names nothing at all does the question get consulted, and only when the
    disclaimer was bare — "I never touched that" — where the subject really is
    whatever was just asked about.

    Both halves were wrong in the first version, and on real input: searching
    the answer and the question in one pass reported "I've never used SUMO" as
    disowning *traffic*, because that word was in the question and happened to
    be longer; and reading the question into any answer flagged "I've never
    worked with that kind of data" as disowning a technology nobody mentioned.
    """
    if not terms:
        return None
    phrase = disclaimer_in(answer)
    if phrase is None:
        return None

    low = " ".join(answer.lower().split())
    at = low.find(phrase)
    after = low[at + len(phrase):]
    window = low[max(0, at - _NEAR): at + len(phrase) + _NEAR]

    def longest_in(haystack: str) -> str | None:
        hits = [t for t in terms if re.search(rf"\b{re.escape(t)}\b", haystack)]
        # Longest wins: "chromadb" is a better account of what they disowned
        # than "db" would be.
        return max(hits, key=len) if hits else None

    named = longest_in(window)
    if named:
        return phrase, named

    if _BARE_ANAPHOR.match(after):
        from_question = longest_in((question or "").lower())
        if from_question:
            return phrase, from_question
    return None


# --- tier 2: the judge ---------------------------------------------------

JUDGE_SCHEMA_FIELDS = {
    "contradicts": "true only if the two statements cannot both be true",
    "quote_earlier": "verbatim from the EARLIER statement",
    "quote_later": "verbatim from the LATER statement",
    "why": "one sentence a human would accept",
}

_JUDGE_PROMPT = """You are checking one candidate answer against something the
same candidate said EARLIER in this interview.

EARLIER (turn {earlier_turn}):
{earlier}

NOW (turn {later_turn}):
{later}

Does the later statement CONTRADICT the earlier one?

Say yes ONLY if both cannot be true at the same time. This is a job interview
and the cost of being wrong is an interviewer accusing someone of changing
their story when they did not — so the bar is high.

NOT contradictions, and these are the ones that trip people up:
  * more detail, or a correction the candidate makes themselves
  * a different part of the same system, or a different project
  * an approximation restated ("about fifty" then "sixty")
  * describing a change over time ("it was 400, we got it to 60")
  * hedging, uncertainty, or saying they do not remember

Quote VERBATIM from each statement. If you cannot quote it, it is not there,
and the answer is no."""


_RESUME_PROMPT = """You are checking what a candidate just SAID against what
their own CV claims in writing.

FROM THE CV:
{resume}

WHAT THEY JUST SAID (turn {turn}):
{answer}

Does the spoken answer contradict the CV?

Say yes ONLY when the CV claims something about THEM that the answer denies or
undercuts — they disown work the CV credits to them, deny knowing a technology
the CV lists, or describe a much smaller role than the CV claims.

NOT contradictions:
  * being modest, or crediting a team ("we built it", "my team did the hard part")
  * not remembering a figure, a date or a detail
  * being rusty on something they used years ago, and saying so
  * the CV summarising and the answer adding nuance
  * anything the CV does not actually claim

A candidate who says "I'd have to look it up now" about a tool they used three
years ago is being honest, not contradicting themselves. Quote VERBATIM from
each side. If you cannot quote it, the answer is no."""


def resume_judge_prompt(resume: str, answer: str, turn: int) -> str:
    return _RESUME_PROMPT.format(
        resume=resume.strip()[:1500], answer=answer.strip()[:900], turn=turn
    )


def judge_prompt(earlier: str, later: str, earlier_turn: int, later_turn: int) -> str:
    return _JUDGE_PROMPT.format(
        earlier=earlier.strip()[:900], later=later.strip()[:900],
        earlier_turn=earlier_turn, later_turn=later_turn,
    )


def verified(quote: str, source: str) -> bool:
    """Is this quote actually in the text it claims to come from?

    The same rule the evidence scorer uses. A contradiction is the most
    damaging thing the panel can assert, so a quote the model invented must
    never reach the report — and models paraphrase when asked to quote.
    """
    if not quote or not quote.strip():
        return False
    a = re.sub(r"\W+", " ", quote).strip().lower()
    b = re.sub(r"\W+", " ", source).strip().lower()
    if not a:
        return False
    if a in b:
        return True
    # Allow a trimmed tail: models often quote a clause and add a word.
    words = a.split()
    return len(words) >= 4 and " ".join(words[:-1]) in b
