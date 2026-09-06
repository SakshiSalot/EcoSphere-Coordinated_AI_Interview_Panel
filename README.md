

<!-- The block above is for Hugging Face Spaces, which reads its configuration
     from README front-matter and ignores the rest. Without `sdk: docker` the
     Space never builds the Dockerfile, and without `app_port: 7860` it looks
     for the app on the wrong port and shows a permanent "starting" spinner.
     GitHub renders it as a small table and is otherwise unbothered. -->

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
        MARKING (Gemini, background thread, never blocking):
        a rubric is generated the moment a question is ASKED — before the
        answer exists, so it cannot be shaped by it — then the answer is
        judged against it concept by concept, each with a verbatim quote
                          → feeds the NEXT turn, and the final assessment
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

**283 regression tests. No API keys, no network, no cost.** Three suites: 122
for the conductor (floor control, the Priya→Arjun handoff, contradiction
detection, role-play scenarios, the difficulty ladder in both directions, the
interview ending), 84 for the marking engine (rubric shape, quote verification,
allocation arithmetic), and 77 for monitoring, profile verification and the
written report. If this is green, the whole brain on your machine is intact.

They pass with **no `.env` at all** — that is checked, in a container with no
credentials in it, because "run this before touching any credentials" is only
useful advice if it is true.

Once you have model keys, three more that spend no Agora minutes:

```bash
make sim        # a full interview against an AI candidate
make demo       # one interview marked end to end — the clearest thing to look at
make score      # 35 more marking checks that judge with a real model (~2 min)
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

### 6. Or use it the way an employer would

`make serve` starts the tunnel, wires the addresses and serves the browser app
at **http://localhost:7860**. The whole flow lives there:

**As the operator** — *New opening*: paste the advert, choose whether there is
a coding round and how the two rounds are weighted. Then *Add a candidate* for
each applicant: drop their CV as a PDF, and their interview is written on the
spot — spoken questions and a coding problem, both grounded in that CV against
that advert. You get an eight-character code per candidate to send them.

**As the candidate** — sign in, enter the code, take the conversation. The
coding round is separate and can be taken later, from a different machine;
the code is saved as they type.

**Back as the operator** — the opening's leaderboard ranks whoever has
finished, with the basis beside every score: how many questions they answered,
how hard it got, and a warning when an interview is too thin to compare.
Opening a row shows the marks, the quote behind each one, the integrity log and
the candidate's GitHub. The hire decision is a button, recorded against your
name.

A round nobody has sat is never counted as zero — someone who has not taken the
coding exercise has not failed it.

---

## Every command

```
make help
```

| | |
|---|---|
| `make check` | 99 tests — free, no keys, no network |
| `make sim` | full interview vs an AI candidate — no Agora minutes |
| `make demo` | one interview marked end to end, with the final assessment |
| `make score` | 35 marking checks that judge with a real model (~2 min) |
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
src/analysis/          the marking engine
   quick.py              instant heuristics, inline, no model call
   rubrics.py            a marking scheme per question, built when it is ASKED
   scorer.py             coverage + evidence quotes; the model observes,
                         Python does the arithmetic
   allocation.py         how many marks each question was worth (pure maths)
   judge.py              Gemini access for marking — one model, or no score
   pipeline.py           the only file that knows about SessionState
   selftest.py           99 checks; --offline skips the ones that cost
   claims.py             quantified claims, and when two cannot both be true
src/conductor/scenarios.py  role-play content, and the cap that ends one
src/integrity/         focus and camera signals — advisory, never a mark
   monitor.py            validates what the browser sends; builds the log
   selftest.py           67 checks for monitoring, verification and the report
src/verify/            checking a candidate's public GitHub
src/report/            the assessment as a filed PDF (WeasyPrint)
src/coding/            the coding round: generated question, Judge0 sandbox
src/mock/              AI candidate · full-interview harness · 100 offline tests
scripts/               panel.py, the interruption gate, TTS probe
frontend/src/integrity.js   the browser half — the camera never leaves it
inputs/                job adverts and CVs
```

Adding a sixth interviewer is an entry in `src/conductor/personas.yaml` and
nothing else — role routing, handoff targets and the question planner all read
that file at runtime.

---

## Contradiction detection, and why it needed moving

There was a `flag_contradiction` tool from the start, and it almost never
fired. The reason is worth stating: asking a small model to notice a conflict
with something said eleven turns ago, unprompted, while also conducting an
interview, is asking it to do the thing it is worst at. It just keeps
interviewing.

There are **three** things a candidate can contradict, and only the third is
what most people picture:

1. **Their own CV.** The resume is a claim they made in writing. "Your CV says
   you built the retry layer in Redis" / "I've never really used Redis" is a
   contradiction, and it is the one an interviewer most wants raised while the
   candidate is still in the room. The CV's distinctive claims are indexed by
   shape at setup; a disclaimer next to one is caught inline, and the flag
   quotes the line of the CV that makes the claim.
2. **A different interviewer.** Telling Priya the team was three and Arjun it
   was twelve only catches anyone out because this panel shares one memory —
   so the flag says so: *"They told Priya one thing and Arjun another."*
3. **Themselves, earlier.** The same measure, restated differently.

Honest uncertainty is deliberately **not** any of them. "I don't know the exact
number", "I don't know if that was the right call", "I don't remember how much
memory we used" — all pass. A candidate punished for saying they cannot
remember a figure learns to bluff, which is the opposite of the point.

So the comparison was made narrow and the trigger mechanical, in two tiers:

**Arithmetic, inline, no model.** Every answer is scanned for numbers with
units, each filed under a topic — latency, throughput, team size, duration.
When a later answer states a different outcome for a topic already quantified,
that is a conflict a regular expression finds, on the turn it happens, so the
conductor routes the challenge into the very next question. `ClaimLedger.by_topic()`
was written on day one to make exactly this tractable, and until now nothing
had ever written a claim into it.

**The judge, asynchronously**, for the contradictions with no numbers in them —
"I led that migration" against "I wasn't really involved in it". Both quotes
are verified against the transcript before anything is recorded; a model asked
to quote will paraphrase, and a flag citing words the candidate never said
would be worse than missing the contradiction.

False positives are the expensive failure here, so tier one requires the two
claims to share subject wording as well as a topic and unit. Four cases it
deliberately does **not** flag, each with a regression test: rounding
("about 50ms" then "60ms"), the same quantity in different units, the same
number under different units ("3 weeks" / "3 engineers"), and describing an
improvement ("from 400ms to 60ms" is one claim, not two that disagree).

## Monitoring, and what it deliberately does not do

The candidate's browser watches two things: **focus** (the tab going to the
background, the window losing focus, pasting into the editor) and **the
camera** — nobody in frame, more than one face, sustained gaze off screen, via
MediaPipe's face landmarker running on their own machine.

Three design decisions, each of which could have gone the other way:

**No video ever leaves the browser.** Frames go to a `<video>` element and into
a model on the candidate's own computer. What reaches the server is a list of
typed events — `no_face, 6.2s`. There is no upload, no recording, no frame
buffer. A hiring product that ships webcam footage of applicants to a hackathon
server is a breach waiting to be noticed; this one has nothing to breach.

**Nothing here can fail a candidate.** Integrity events are excluded from
`scorer.PENALISED_KINDS` and cost exactly zero marks — the offline suite
asserts it. Every signal has an innocent explanation identical to the guilty
one: looking away is thinking, a lost focus is a calendar popup, a second face
is a flatmate. So each signal is stored with a `but` field naming what it
cannot distinguish, and that text renders **next to the count**, not in a
footnote. A number alone on a hiring screen is read as an accusation.

**An empty log is reported as empty, not as clean.** Anything running on a
machine the candidate controls can be switched off by the candidate — that is
not fixable, and claiming otherwise would be the dishonest part. So the browser
reports that it is still watching every fifteen seconds, and a stretch with no
heartbeat is shown as *unmonitored time*, which is missing data rather than a
clean record.

## GitHub verification, and the two questions people conflate

*Does this account exist and what is in it* is one API call. *Is it theirs* is
not answerable from the API at all — anyone can type `torvalds` into a form.

So ownership is proven the only way it can be: the candidate publishes a code
we derive (`echosphere-verify-…`, an HMAC over their account id and the
username, so it is unguessable and cannot be reused for a second account) in
their GitHub bio or a public gist. Until they do, the badge says **claimed, not
proven** — on their screen and the operator's. A tick that means "they typed
something" is worse than no tick, because someone will trust it.

The resume cross-check reports languages backed by public code and languages
claimed with none — and ships with the reason the second list is usually
innocent: most professional code is in a private repository belonging to an
employer. Ten years of Java at a bank leaves no public Java.

**LinkedIn gets no tick under any circumstances.** There is no public API and
the terms prohibit scraping, so it is stored as a link for a human to open and
labelled exactly that.

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
