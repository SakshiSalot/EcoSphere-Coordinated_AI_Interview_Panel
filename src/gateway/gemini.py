"""Gemini, for structured work off the critical path.

Deliberately a different provider from the voice path. Two reasons:

  * Separate quota pool. Heavy analysis can never starve the conversation —
    which is the thing that actually gets scored.
  * `responseSchema` forces valid JSON instead of hoping for it. Today taught
    us that asking a small model to speak naturally *and* emit JSON gets you
    fluent prose and no JSON at all.

Called over REST rather than through the SDK: the pinned google-genai is old
enough that `models.list()` returns 501, and one httpx call has fewer moving
parts than fighting a version mismatch during a hackathon.
"""

import json
import logging
from typing import Any

import httpx

from src import config

log = logging.getLogger("gemini")

BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Probed with a real request on 31 Aug 2026, because the model LIST endpoint
# lies: it advertises gemini-2.5-flash and 2.5-flash-lite, both of which then
# return 404 "no longer available to new users". Only an actual call tells the
# truth.
#
# 3.7-flash and the `-latest` aliases were returning 503 "high demand" during
# testing, so they are not first. 3.6-flash is the strongest that answered
# reliably; the lites are two to three times faster and are enough for the
# smaller judgements.
MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
]

# For high-volume, low-stakes calls (per-answer scoring) where latency matters
# more than depth.
FAST_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"]


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


def structured(
    prompt: str,
    schema: dict,
    temperature: float = 0.4,
    max_tokens: int = 4096,
    models: list[str] | None = None,
) -> dict[str, Any]:
    """Return JSON matching `schema`, or raise.

    Never returns a silently-defaulted object: a quiet fallback here produces
    scores and question plans that look perfectly reasonable and mean nothing,
    and nobody notices until a judge asks how it works.
    """
    if not available():
        raise RuntimeError("GEMINI_API_KEY is not set")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    last: Exception | None = None
    for model in (models or MODELS):
        try:
            r = httpx.post(
                f"{BASE}/{model}:generateContent",
                params={"key": config.GEMINI_API_KEY},
                json=body,
                timeout=90.0,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            log.info("gemini %s answered (%d chars)", model, len(text))
            return json.loads(text)
        except Exception as exc:
            last = exc
            log.warning("gemini %s failed (%s) — trying next", model, str(exc)[:160])

    raise RuntimeError(f"all Gemini models failed; last error: {last}")
