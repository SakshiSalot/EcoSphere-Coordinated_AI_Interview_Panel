"""Language model access, with a fallback chain.

Every entry speaks OpenAI's protocol, so failover is a base-URL, key and model
swap rather than a rewrite.

Two things learned the hard way on 31 Aug 2026, both verified against the live
/models endpoints:

  * Llama 3.3 70B is gone from Groq's free tier. The Round 2 deck's stack
    slide says otherwise and must be corrected before submission.
  * gpt-oss is a *reasoning* model. Without `reasoning_effort: "low"` it spends
    the entire token budget thinking and returns empty content — which looks
    exactly like a broken gateway.

Rate limits are per MODEL, not per account. That is why the chain has two
entries on Groq: switching model on the same provider genuinely doubles the
tokens-per-minute budget.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Iterator

from openai import OpenAI

from src import config

log = logging.getLogger("providers")


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model.split('/')[-1]}"


def _chain() -> list[Provider]:
    candidates = [
        Provider("groq", "https://api.groq.com/openai/v1", config.GROQ_API_KEY,
                 "openai/gpt-oss-120b", {"reasoning_effort": "low"}),
        Provider("groq", "https://api.groq.com/openai/v1", config.GROQ_API_KEY,
                 "qwen/qwen3.8-27b"),
        Provider("openrouter", "https://openrouter.ai/api/v1",
                 config.OPENROUTER_API_KEY, "google/gemma-4-31b-it:free"),
        Provider("cerebras", "https://api.cerebras.ai/v1",
                 config.CEREBRAS_API_KEY, "gpt-oss-120b"),
    ]
    return [p for p in candidates if p.api_key]


def available() -> list[str]:
    return [p.label for p in _chain()]


def _client(p: Provider) -> OpenAI:
    return OpenAI(base_url=p.base_url, api_key=p.api_key, timeout=40.0, max_retries=0)


_RETRY_AFTER = re.compile(r"try again in ([\d.]+)s")


def _short_wait(exc: Exception) -> float:
    """Seconds Groq asked us to wait, if it was a brief token-per-minute
    bounce rather than a real outage."""
    if "429" not in str(exc):
        return 0.0
    m = _RETRY_AFTER.search(str(exc))
    return float(m.group(1)) if m else 0.0


def _attempt(p: Provider, fn, allow_wait: bool):
    """Run one call against one provider, optionally absorbing a brief 429.

    `allow_wait` is False on the voice path — three seconds of silence
    mid-interview is worse than a slightly weaker model. It is True off the
    critical path, where waiting costs nothing and keeps every answer on the
    same model.
    """
    try:
        return fn(p)
    except Exception as exc:
        wait = _short_wait(exc)
        if allow_wait and 0 < wait <= 8:
            log.info("%s rate-limited, waiting %.1fs", p.label, wait)
            time.sleep(wait + 0.3)
            return fn(p)
        raise


def _run(fn, allow_wait: bool):
    chain = _chain()
    if not chain:
        raise RuntimeError("No LLM provider configured — set GROQ_API_KEY in .env")

    last: Exception | None = None
    for p in chain:
        try:
            return p, _attempt(p, fn, allow_wait)
        except Exception as exc:
            last = exc
            log.warning("%s failed (%s) — advancing chain", p.label, str(exc)[:160])
    raise RuntimeError(f"all providers failed; last error: {last}")


# --- the three call shapes ----------------------------------------------


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 300,
    temperature: float = 0.6,
    allow_wait: bool = True,
) -> tuple[str, list[tuple[str, str]], dict, str]:
    """One completion. Returns (text, tool_calls, assistant_message, provider).

    `assistant_message` is the raw message in OpenAI shape, ready to append to
    the conversation — needed to continue a tool loop, because a persona that
    calls a tool usually returns no spoken content on that same turn.
    """

    def call(p: Provider):
        kwargs = {
            "model": p.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **p.extra,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return _client(p).chat.completions.create(**kwargs)

    p, r = _run(call, allow_wait)
    m = r.choices[0].message
    calls = [(c.function.name, c.function.arguments or "{}") for c in (m.tool_calls or [])]

    assistant: dict = {"role": "assistant", "content": m.content or ""}
    if m.tool_calls:
        assistant["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in m.tool_calls
        ]

    return (m.content or "").strip(), calls, assistant, p.label


def complete(
    messages: list[dict],
    max_tokens: int = 400,
    temperature: float = 0.7,
    allow_wait: bool = True,
) -> str:
    """Plain text completion — the AI candidate, question planning, analysis."""
    text, _calls, _msg, _p = chat(
        messages, None, max_tokens, temperature, allow_wait
    )
    return text


def stream_chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 300,
    temperature: float = 0.6,
):
    """Streaming, for the live voice path — speech starts before the sentence
    is finished. Never waits on a 429: it advances the chain instead."""

    def call(p: Provider):
        kwargs = {
            "model": p.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **p.extra,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return _client(p).chat.completions.create(**kwargs)

    p, stream = _run(call, allow_wait=False)
    return p.label, stream


def text_chunks(stream) -> Iterator[str]:
    """Text deltas only.

    Tool-call deltas are deliberately dropped rather than forwarded: a leaked
    tool call is synthesised and read aloud to the candidate as gibberish.
    """
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if getattr(delta, "content", None):
            yield delta.content


# Kept for callers written against the earlier shape.
def complete_with_tools(
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 300,
    temperature: float = 0.6,
) -> tuple[str, list[tuple[str, str]]]:
    text, calls, _msg, _p = chat(messages, tools, max_tokens, temperature)
    return text, calls
