# EchoSphere — Coordinated AI Interview Panel

**Team Lumina · EchoSphere Hackathon 2026 · Problem Statement 11**

A candidate joins a voice call and is interviewed by several AI interviewers
with different voices, different concerns, and a shared memory of everything
the candidate has said. A conductor decides who holds the floor, so exactly one
persona speaks and can hand off to another *with a reason*. Questions are drawn
from a real job advert and the candidate's own CV. The panel adapts its
difficulty, catches vague answers, and ends with a written assessment where
every judgement cites a timestamped quote from the transcript.

| Persona | Role | Cares about |
|---|---|---|
| **Priya** | Technical | Correctness, depth, trade-offs. Accepts a sound implementation without probing commercial impact — which is what triggers the handoff. |
| **Arjun** | Product | Users, prioritisation, the cost of being wrong. Challenges technically correct answers that never mention a customer. |
| **Meera** | Behavioural | Collaboration, conflict, ownership. Runs role-play scenarios and pins down generalities. |
| **Kavya** | Hiring manager | Scope, ownership, ambiguity — whether you operate at the level the role needs. *(optional)* |
| **Dev** | Customer | Outcomes in plain language. Says so when you use jargon he does not understand. *(optional)* |

---

## How Agora Conversational AI is integrated

Agora lets you point an agent's `llm.url` at **any** endpoint that speaks
OpenAI's `/chat/completions` protocol. So rather than writing an app that calls
a model, we wrote a service that *pretends to be* a model, and Agora drives it.

```
Candidate browser ──audio──▶ AGORA RTC CHANNEL ◀──audio── Priya · Arjun · Meera
                                    │                     (one agent per persona,
                                    │                      one voice each)
             Agora handles VAD, barge-in, speech recognition,
             turn detection, LLM invocation and text-to-speech
                                    │
        when the candidate stops speaking, EVERY agent POSTs an
        OpenAI-shaped request to OUR gateway, at the same moment
                                    ▼
        ┌──────────────── GATEWAY (FastAPI) ────────────────┐
        │  POST /v1/{session}/{role}/chat/completions        │
        │                                                    │
        │  CONDUCTOR: does {role} hold the floor this turn?   │
        │     no  → empty completion: silent, and no model    │
        │           is called at all, so it costs nothing     │
        │     yes → stream a reply, with six tools available  │
        └────────────────────────────────────────────────────┘
                                    │ fire-and-forget, never blocking
                                    ▼
        ANALYSIS: score the answer against its rubric with quotes ·
        detect vagueness · extract claims · check contradictions
                          → feeds the NEXT turn
```

That one fact gives us everything: several agents share one brain, the
conductor silences a persona simply by returning an empty response, and all the
interview logic is ordinary Python we can test with no voice stack running.

**Agora endpoints used:** `join`, `leave`, `interrupt`, `update`, `history`,
`turns` (per-turn latency metrics), `agents` (leak detection), plus the RTC
channel-user API to confirm the candidate is present before spending minutes.

**Agora-managed TTS** (`credential_mode: managed`), so voices are included in
the agent-minute price with no third-party TTS account.

---

## Getting started on a new machine

### 1. What you need first

| | |
|---|---|
| **Python 3.11+** | `python3.11 --version` |
| **cloudflared** | `brew install cloudflared` (macOS) — gives your laptop a public HTTPS address Agora can reach |
| **git** | to clone |

macOS only: WeasyPrint (used for the PDF assessment) needs `brew install pango`
if `import weasyprint` fails. Nothing else in the project depends on it.

### 2. Clone and install

```bash
git clone <repo-url>
cd EcoSphere-Coordinated_AI_Interview_Panel

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Prove it works before touching any credentials

```bash
make check
```

35 regression tests. **No API keys, no network, no cost, under a second.** They
cover floor control, the Priya→Arjun handoff, the difficulty ladder in both
directions, repeat handling, and the interview ending. If this is green, the
interview brain on your machine is intact.

```bash
make sim      # a full interview against an AI candidate — needs keys, no Agora
```

### 4. Credentials

```bash
cp .env.example .env
```

Then fill it in — every line is explained in the file.

| What | Where |
|---|---|
| `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE` | Agora Console → your project |
| `AGORA_CUSTOMER_ID`, `AGORA_CUSTOMER_SECRET` | same console, **RESTful API** section |
| `GROQ_API_KEY` | console.groq.com — free |
| `GEMINI_API_KEY` | aistudio.google.com — free |
| `GATEWAY_SHARED_SECRET` | you invent it; any random string |
| `GATEWAY_PUBLIC_URL` | filled in at step 5 |

Two things that cost people an hour each:

- **App Certificate and Customer Secret are different credentials.** The
  certificate signs RTC tokens; the Customer pair authenticates REST calls.
- **Conversational AI Engine must be enabled** on the Agora project, or every
  join fails with a confusing error.

### 5. Run it

Three terminals. The first two stay open.

```bash
# terminal 1 — the gateway
make gateway          # development: reloads when you edit code
make gateway-live     # live voice tests: no reload (see the warning below)

# terminal 2 — a public address Agora can reach
make tunnel
# copy the https:// URL it prints into .env as GATEWAY_PUBLIC_URL

# terminal 3 — check the gateway is reachable from outside
scripts/smoke_gateway.sh https://your-tunnel-url.trycloudflare.com
```

You should see a health check, streamed text ending in `[DONE]`, a *silent*
reply for the second persona, and `401` for a bad token. Ideally run that last
command from your phone's hotspot: if it answers from a network you do not
control, Agora's servers can reach it too.

Then the interview:

```bash
make gate         # one interviewer — prove you can interrupt it mid-sentence

make interview RESUME=yourcv.pdf JD=ml-engineer.txt \
     ROLES=technical,hiring_manager,customer \
     TITLE="ML Engineer" NAME="Your Name" CHANNEL=yourname-1
```

It prints a channel and token, waits for you to join in the browser, confirms
you are actually in the channel, *then* brings the panel in. It ends by itself
once the question plan is covered, and tears the agents down.

> **Use `make gateway-live` for voice tests.** Session state lives in memory,
> so `--reload` firing mid-interview wipes the transcript and the question plan.
> Use `make gateway` while writing code — a stale gateway silently serving old
> code cost us an evening.

---

## Every command

```
make help
```

| | |
|---|---|
| `make check` | 35 tests — free, no keys, under a second |
| `make sim` | full interview vs an AI candidate — no Agora minutes |
| `make roles` | who can sit on the panel |
| `make inputs` | what CVs and job adverts are available |
| `make gateway` / `gateway-live` | the gateway, with / without auto-reload |
| `make tunnel` | public HTTPS |
| `make gate` | one interviewer, interruption test |
| `make interview` | the real thing |
| `make status` | what is running, **and whether anything is still billing** |
| `make stop` | stop every Agora agent |
| `make kill` | stop the gateway and the tunnel |

---

## Two of us working at once

Everything is per-person except the Agora account.

- **Each person runs their own gateway and their own tunnel**, and puts their
  own `GATEWAY_PUBLIC_URL` in their own `.env`. Never share a tunnel URL.
- **Use different channel names.** `make interview CHANNEL=sakshi-1`. Two
  people on the same channel will hear each other's interviewers.
- **The 300 free agent-minutes are shared across the account.** Say in the
  group when you are about to run a live test. Run `make status` afterwards —
  it reports any agent still billing.
- **Develop at `PANEL_SIZE=2`.** Three interviewers in a ten-minute interview
  costs thirty agent-minutes, not ten. Save three for integration runs and the
  demo video.

Nearly all day-to-day work needs no Agora at all: `make check` and `make sim`
exercise the whole interview brain for free.

---

## CVs and job adverts

```
inputs/
  jd/       job adverts   (.txt, .md, .pdf)   — committed
  resume/   CVs           (.pdf, .txt)        — GITIGNORED
```

Refer to them by bare filename: `--jd ml-engineer.txt --resume harsh.pdf`.
PDFs are text-extracted automatically.

`inputs/resume/` is gitignored on purpose. Real CVs are personal data and this
repository is public at submission — keep them out of the commit history.

---

## Where things live

```
src/contract.py        the one function Agora ultimately calls
src/gateway/           the fake-model endpoint, SSE framing, model providers
src/agora/             join · leave · interrupt · tokens · channel · panel
src/conductor/         personas.yaml · floor control · six tools · difficulty
src/intake/            job advert + CV  ->  a per-persona question plan
src/state/             ledgers, shared session memory, the digest
src/analysis/          instant heuristics (scoring and reports land here)
src/mock/              AI candidate · full-interview harness · 35 offline tests
scripts/               panel.py, the interruption gate, TTS probe
inputs/                job adverts and CVs
```

Adding a sixth interviewer is an entry in `src/conductor/personas.yaml` and
nothing else — role routing, handoff targets and the question planner all read
that file at runtime.

---

## Stack

Agora Conversational AI Engine (voice, barge-in, ASR, managed TTS) ·
**Groq** `openai/gpt-oss-120b` for every spoken reply, with a Groq
second-model → OpenRouter → Cerebras fallback chain ·
**Google Gemini** for question planning and analysis, deliberately a separate
quota pool so heavy analysis can never starve the conversation ·
FastAPI · vanilla HTML/JS. Everything except Agora runs on a free tier.

> Rate limits are **per model**, not per account, which is why the fallback
> chain lists two different Groq models. Model availability also changes: both
> Groq and Gemini advertise models via their APIs that then refuse real
> requests, so the working set is probed and dated in the source rather than
> assumed.

---

## When something goes wrong

| What you see | What it means |
|---|---|
| Interviewer joins but never speaks | Gateway unreachable. Is the tunnel still open, and does `GATEWAY_PUBLIC_URL` match what it printed? |
| `401 unauthorized` in the gateway log | `GATEWAY_SHARED_SECRET` mismatch. The same value is used in both places. |
| `all providers failed` | Every model account is rate-limited at once. Wait a minute. If it is constant, replies are too long — that is a bug, not a quota problem. |
| Interviewer says nothing at all | Usually a reasoning model spending its whole budget thinking. Check `reasoning_effort` in `src/gateway/providers.py`. |
| `model_not_found` | The provider retired that model. Ask their API what it serves now — do not guess. |
| `vendor ... not available for the current SKU` | TTS vendor not offered on this Agora account. Run `python -m scripts.probe_tts` — it joins and leaves in seconds and reports what is accepted. |
| Two interviewers speak at once | The harness raises `OneSpeakerViolation`. Run `make check`; it will name the broken rule. |
| Free minutes disappearing | An agent left running. `make status`, then `make stop`. |
| An interviewer sounds wrong | A wording problem, not a code problem — `src/conductor/personas.yaml`, no redeploy needed. |

---

## Build disclosure

Built from scratch between **31 August and 6 September 2026** for the
EchoSphere Hackathon. See the commit history. Team Lumina — Harsh Raj (lead),
Sakshi Salot.
