"""Gemini access for the marking engine.

Everything in `src/analysis` runs off the critical path — nothing here is ever
between the candidate and hearing a reply. That buys two things the voice path
cannot afford: a slower, more careful model, and the freedom to wait out a rate
limit rather than degrade to a weaker one.

Deliberately separate from `gateway/providers.py`. That chain exists to keep a
voice call alive, and will answer from a weaker model rather than pause —
correct, because silence mid-call is worse than a slightly worse question.
Marking wants the opposite. Scores produced by four different models are not
comparable to each other, so a scale that silently changes provider mid-interview
is worse than one that stops. One model, every time, or no score at all.

Two facts, both verified against the live API :

  * `gemini-2.5-flash` returns 404 for this key — "no longer available to new
    users". The error names its own replacement, `gemini-3.6-flash`. Every
    tutorial still says 2.5, so check what the API serves before trusting a
    model name.
  * `response_schema` with a Pydantic model makes `response.parsed` a typed
    object. That is the whole reason this engine can trust its own input; the
    alternative is asking politely for JSON and hoping.
"""

import logging
import os
import re
import time
from functools import lru_cache
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from src import config

log = logging.getLogger("analysis.judge")

# Chosen on QUOTA, not on capability. `gemini-3.6-flash` is the better model and
# is unusable here: its free tier allows twenty requests per DAY, and a single
# twenty-turn interview needs roughly forty calls. The lite tier is built for
# exactly this shape of work — high volume, low difficulty, a yes/no judgement
# against a fixed checklist rather than open reasoning.
#
# Pinned to a version rather than the `-latest` alias on purpose: an alias that
# silently changes model underneath you means two candidates interviewed a week
# apart were marked by different judges, and a disputed score cannot be
# reproduced. Overridable so a retirement is a config change, not a code one.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()

T = TypeVar("T", bound=BaseModel)


class JudgeUnavailable(RuntimeError):
    """The judge was asked and did not usefully answer.

    A named type rather than a bare Exception so callers can catch *this* and
    nothing else. `except Exception` around a model call swallows your own
    typos too, and reports them as "the model was unavailable" — which sends
    you debugging the network instead of your code.

    Raised rather than papered over. A marking engine that substitutes a
    plausible number when the model fails produces scores that look perfectly
    reasonable and mean nothing, and nobody notices until someone asks how
    scoring works. Callers should record *no* score instead: a missing score
    is visible, a wrong one is not.
    """


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    """Built on first use, not at import.

    Constructing this at module scope would make `import scorer` fail without a
    key — which would break the offline suite, whose entire value is that it
    needs no keys and no network.
    """
    if not config.GEMINI_API_KEY:
        raise JudgeUnavailable("GEMINI_API_KEY is not set")
    return genai.Client(api_key=config.GEMINI_API_KEY)


# A per-DAY quota is not a bounce, and treating it as one is expensive: the
# backoff below would sleep a minute, retry, fail, and then do it again for the
# next answer — twenty minutes of worker threads rediscovering the same fact,
# with no marks at the end of it either way. So the first one trips a breaker.
#
# Not permanent, because the daily allowance does reset. Re-probing every
# quarter of an hour costs one wasted call instead of one per answer.
_DAILY_QUOTA = re.compile(r"PerDay", re.IGNORECASE)
QUOTA_COOLDOWN = 900.0

_quota_exhausted_until = 0.0


def available() -> bool:
    """Whether marking can run at all, without constructing anything or
    raising. Lets a caller skip cleanly rather than catch once per answer."""
    if not config.GEMINI_API_KEY:
        return False
    return time.monotonic() >= _quota_exhausted_until


_TRANSIENT = ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE")

# Flipped off for the process the first time a model rejects the parameter, so
# swapping GEMINI_MODEL does not also require editing this file.
_thinking_supported = True


def _is_transient(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _TRANSIENT)


# Gemini's free tier is 5 requests per minute per model, and a 429 states how
# long to wait — either as "Please retry in 52.1s" or a structured retryDelay.
_RETRY_HINT = re.compile(r"retry in ([\d.]+)s|retryDelay['\"]?:\s*['\"]?(\d+)s")

# Long, on purpose. `providers.py` caps its wait at eight seconds because it
# sits on the voice path, where three seconds of silence is worse than a weaker
# model. Nothing waits on marking, so honouring a full minute costs only time
# and buys the score for that answer instead of losing it.
MAX_BACKOFF = 65.0
DEFAULT_BACKOFF = 2.0


def _retry_after(exc: Exception) -> float:
    """How long the API asked us to wait, clamped to something sane.

    Guessing is what makes a rate limit look like an outage: the first attempt
    here waited two seconds against a stated fifty-two, failed again, and
    reported the quota error as if the model were down.
    """
    match = _RETRY_HINT.search(str(exc))
    if not match:
        return DEFAULT_BACKOFF
    stated = float(match.group(1) or match.group(2) or 0.0)
    return min(max(stated, DEFAULT_BACKOFF), MAX_BACKOFF)


def _generation_config(schema: type[T], temperature: float, max_output_tokens: int):
    settings: dict = {
        # Belt and braces: the schema alone is enough on current models, but
        # the mime type makes a downgrade fail loudly instead of returning
        # prose that happens to mention JSON.
        "response_mime_type": "application/json",
        "response_schema": schema,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        # We never pass tools to the judge, and it must never acquire the
        # ability to act — it reads a transcript written by the person being
        # assessed. Disabling explicitly also silences a per-call INFO line.
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(
            disable=True
        ),
    }
    # Left unchecked a reasoning model spends its whole budget thinking and
    # returns empty content — indistinguishable from a broken key. Marking is a
    # judgement against a fixed checklist, not a puzzle; it does not need the
    # deliberation, and the budget is better spent on quotes.
    if _thinking_supported:
        settings["thinking_config"] = types.ThinkingConfig(thinking_level="low")
    return types.GenerateContentConfig(**settings)


def structured(
    prompt: str,
    schema: type[T],
    *,
    what: str,
    temperature: float = 0.15,
    max_output_tokens: int = 2000,
) -> T:
    """One judged call, returned as a typed object. The only public entry point.

    Args:
        prompt: the full instruction. Callers are responsible for capping any
            candidate-supplied text *before* it gets here — this function will
            not silently truncate, because a quietly shortened rubric is
            exactly the plausible-but-wrong failure the module exists to avoid.
        schema: a Pydantic model. Its field names and docstrings are sent to
            the API as the response schema, so they are part of the prompt in
            everything but name — worth wording carefully.
        what: names the caller in every log line, e.g. "rubric/technical".
            When a score looks wrong three days from now the first question is
            always "did the model answer, or did we fall back?" — so every
            failure has to say which call it was.
        temperature: low by default. Two candidates giving the same answer must
            receive the same mark, or the scale means nothing.

    Raises:
        JudgeUnavailable: the model could not be reached, or returned nothing
            parseable. Never returns a placeholder result.
    """
    global _thinking_supported, _quota_exhausted_until

    if not available():
        raise JudgeUnavailable(
            f"{what} skipped: {MODEL} daily quota exhausted, retrying in "
            f"{_quota_exhausted_until - time.monotonic():.0f}s"
        )

    last: Exception | None = None

    for attempt in (1, 2):
        try:
            response = _client().models.generate_content(
                model=MODEL,
                contents=prompt,
                config=_generation_config(schema, temperature, max_output_tokens),
            )
        except Exception as exc:  # noqa: BLE001 — the SDK surfaces many types
            last = exc

            # A model that does not take thinking_level: drop it and retry.
            if _thinking_supported and "thinking" in str(exc).lower():
                log.warning("%s does not accept thinking_level - disabling", MODEL)
                _thinking_supported = False
                continue

            # The day's allowance is gone. Stop, loudly and once, rather than
            # sleeping a minute per answer for the rest of the interview.
            if _DAILY_QUOTA.search(str(exc)):
                _quota_exhausted_until = time.monotonic() + QUOTA_COOLDOWN
                log.error(
                    "%s: %s DAILY quota exhausted - marking is off for %.0f "
                    "minutes. Answers will go unscored, not mis-scored.",
                    what, MODEL, QUOTA_COOLDOWN / 60,
                )
                break

            # A rate-limit bounce. Nobody is waiting on us, so absorb one — but
            # only one. A retry loop turns a real outage into a silent hang.
            if attempt == 1 and _is_transient(exc):
                wait = _retry_after(exc)
                log.warning(
                    "%s: %s unavailable - waiting %.0fs then retrying once (%s)",
                    what, MODEL, wait, str(exc)[:100],
                )
                time.sleep(wait)
                continue

            log.error("%s: %s failed - %s", what, MODEL, str(exc)[:240])
            break

        parsed = response.parsed
        if parsed is None:
            # Nearly always the output hitting max_output_tokens mid-object. A
            # truncated response and a refused one are indistinguishable to the
            # caller, so print what did arrive: seeing `{"concepts": [{"conce`
            # identifies a token limit instantly.
            log.error(
                "%s: %s returned no parseable JSON (truncated?): %r",
                what, MODEL, (response.text or "")[:300],
            )
            last = JudgeUnavailable("no parseable JSON in response")
            if attempt == 1:
                continue
            break

        _log_cost(what, response)
        return parsed

    raise JudgeUnavailable(f"{what} failed via {MODEL}: {last}") from last


def _log_cost(what: str, response) -> None:
    """Rate limits are the first thing that breaks at scale — not CPU, not
    memory. Per-call token counts are what make that visible before it bites."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return
    log.info(
        "%s via %s: %s prompt + %s output tokens",
        what, MODEL, usage.prompt_token_count, usage.candidates_token_count,
    )
