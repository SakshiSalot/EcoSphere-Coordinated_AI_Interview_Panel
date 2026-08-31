"""Server-sent event framing in OpenAI's chat-completion-chunk shape.

Two details are non-negotiable and both cause failures that look like crashes:

  * A blank line after every frame. Without it the client buffers forever.
  * A final `data: [DONE]`. Omit it and the agent waits for more output until
    its idle timeout, which is indistinguishable from a hang.
"""

import json
import time
import uuid
from typing import Iterable, Iterator

MODEL_NAME = "echosphere-panel"


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chunk(cid: str, created: int, delta: dict, finish: str | None = None) -> dict:
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_NAME,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def sse(chunks: Iterable[str]) -> Iterator[str]:
    """Stream text chunks as a well-formed OpenAI streaming response."""
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    yield _frame(_chunk(cid, created, {"role": "assistant", "content": ""}))
    for c in chunks:
        if not c:
            continue
        yield _frame(_chunk(cid, created, {"content": c}))
    yield _frame(_chunk(cid, created, {}, "stop"))
    yield "data: [DONE]\n\n"


def sse_silent(mode: str = "empty_completion") -> Iterator[str]:
    """The silence path — a persona that does not hold the floor this turn.

    The entire turn-taking design rests on the assumption that an empty
    completion makes an Agora agent stay quiet. That assumption is cheap to
    test and expensive to get wrong, so both plausible encodings are available
    behind SILENCE_MODE rather than hardcoded.

    If neither silences an agent, the fallback is not in this file: join
    non-active agents with `remote_rtc_uids: []` so they cannot hear the
    candidate at all, and swap subscriptions via the `update` endpoint on
    each handoff.
    """
    if mode == "done_only":
        yield "data: [DONE]\n\n"
        return
    yield from sse([])
