# EchoSphere — Coordinated AI Interview Panel

**Team Lumina · EchoSphere Hackathon 2026 · Problem Statement 11**

A candidate joins a voice call and is interviewed by three AI interviewers with
different voices, different concerns, and a shared memory of everything the
candidate has said. A conductor decides who holds the floor, so exactly one
persona speaks and can hand off to another *with a reason*. The panel adapts its
difficulty, catches vague and contradictory answers, and produces a written
assessment where every judgement cites a timestamped quote from the transcript.

| Persona | Role | Cares about |
|---|---|---|
| **Priya** | Technical | Correctness, depth, trade-offs. Accepts a sound implementation without probing commercial impact — which is what triggers the handoff. |
| **Arjun** | Product | Users, prioritisation, the cost of being wrong. Challenges technically correct answers that never mention a customer. |
| **Meera** | Behavioural | Collaboration, conflict, ownership. Runs role-play scenarios and pins down generalities. |

## How Agora Conversational AI is integrated

Agora lets you point an agent's `llm.url` at **any** endpoint that speaks
OpenAI's `/chat/completions` protocol. So rather than writing an app that calls
a model, we wrote a service that *pretends to be* a model, and Agora drives it.

```
Candidate browser ──audio──▶ AGORA RTC CHANNEL ◀──audio── Priya · Arjun · Meera
                                    │                     (3 agents, 3 voices)
                     Agora does VAD, barge-in, speech recognition,
                     turn detection, LLM invocation, and TTS
                                    │
              when the candidate stops speaking, EACH agent POSTs
              an OpenAI-shaped request to OUR gateway
                                    ▼
        ┌──────────────── GATEWAY (FastAPI) ────────────────┐
        │  POST /v1/{session}/{role}/chat/completions       │
        │                                                   │
        │  CONDUCTOR: does {role} hold the floor this turn?  │
        │     no  → empty completion, no LLM call, silence  │
        │     yes → stream a reply from Groq, with tools    │
        └───────────────────────────────────────────────────┘
                                    │ fire-and-forget, never blocking
                                    ▼
        ANALYSIS (Gemini): score the answer against its rubric with
        quotes · detect vagueness · extract claims · check contradictions
                          → feeds the NEXT turn
```

That one fact gives us everything: three agents share one brain, the conductor
silences a persona simply by returning an empty response, and all interview
logic is ordinary Python testable with no voice infrastructure running.

**Agora endpoints used:** `join`, `leave`, `interrupt`, `update`, `history`,
`turns` (per-turn latency metrics), plus the Agora Web SDK in the browser.

## Stack

Agora Conversational AI Engine · Groq (Llama 3.3 70B) with Cerebras/OpenRouter
fallback · Google Gemini for off-critical-path analysis · FastAPI · vanilla
HTML/JS. Everything except Agora runs on a free tier.

## Setup

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill it in
```

Agora needs **two different credential pairs**: the App Certificate signs RTC
tokens, while Customer ID / Customer Secret authenticate REST calls.

## Running

```bash
# 1. the gateway
.venv/bin/uvicorn src.gateway.app:app --port 7860

# 2. make it publicly reachable — Agora calls it over the internet,
#    so localhost is unreachable by definition
cloudflared tunnel --url http://localhost:7860
#    put the https:// URL in .env as GATEWAY_PUBLIC_URL

# 3. prove the framing works, first locally then against the tunnel
scripts/smoke_gateway.sh
scripts/smoke_gateway.sh https://your-tunnel-url.trycloudflare.com

# 4. the Day 1 gate — join one agent and interrupt it
.venv/bin/python -m scripts.day1_interrupt_test
```

## Cost discipline

Agora bills per **agent**-minute, not per session-minute. Three personas in a
ten-minute interview consume thirty agent-minutes. Develop and test at
`PANEL_SIZE=2`; switch to 3 only for integration runs and the demo video.
Teardown is wrapped in `finally` everywhere — an agent left running after a
crashed test keeps billing until its idle timeout.
