# EchoSphere: Coordinated AI Interview Panel

**Team Lumina · EchoSphere Hackathon 2026 · Problem Statement 11**

### ▶ Live: **https://lumina-ckqc.onrender.com**

A candidate joins a voice call and is interviewed by **four AI interviewers**
with different voices, different concerns, and a shared memory of everything
said. A conductor decides who holds the floor, so exactly one persona speaks and
can hand off to another *with a reason*. Questions are drawn from a real job
advert and the candidate's own CV. The panel adapts its difficulty, catches
vague and contradictory answers, runs a role-play, and ends with a written
assessment where every judgement cites a timestamped quote from the transcript.

| Persona | Role | Cares about |
|---|---|---|
| **Priya** | Technical | Correctness, depth, trade-offs. Accepts a sound implementation without probing commercial impact, which is what triggers the handoff. |
| **Arjun** | Product | Users, prioritisation, the cost of being wrong. Challenges technically correct answers that never mention a customer. |
| **Meera** | Behavioural | Collaboration, conflict, ownership. Runs the role-play scenarios. |
| **Kavya** | Hiring manager | Scope, ownership, ambiguity: whether you operate at the level the role needs. |

---

## Try the live demo

**https://lumina-ckqc.onrender.com**

| | Username | Password |
|---|---|---|
| **Operator** | `operator` | `EchoSphere@2026` |
| **Candidate** | *create your own* | n/a |

**Candidates sign themselves up.** Click *Sign in* → create an account. That is
deliberate: candidates are the people being interviewed and there is nobody to
create accounts for them.

**Operators cannot self-register.** An operator reads assessments and records
hiring decisions, so self-service there would let anyone award themselves
visibility of every candidate. Creating one needs the shared key or
`scripts/seed_users`.

> **A demo instance holding test data.** The credentials above are public so
> that judges can get in. Please do not upload a real person's CV.

### The two-minute tour

1. Sign in as the **operator**. You will see openings with completed interviews.
   Open one, click a candidate, and read the assessment: marks per interviewer,
   each with the candidate's own words underneath.
2. **New opening** → paste an advert → **Add a candidate** → drop a CV. About
   ten seconds later you have an eight-character invite code.
3. Sign out, **create a candidate account**, and enter that code.
4. Take either round, the conversation or the coding exercise. You will need a
   **microphone** for the conversation.
5. Back as the operator: the leaderboard, the assessment, **Download report**,
   and record a hire decision.

> Render's free tier sleeps when idle, so the first request may take half a
> minute to wake. Everything after that is immediate.

---

## How Agora Conversational AI is integrated

Agora lets you point an agent's `llm.url` at **any** endpoint that speaks
OpenAI's `/chat/completions` protocol. So rather than writing an app that calls
a model, we wrote a service that *pretends to be* a model, and Agora drives it.

```
                     ┌────────────────────────────────────────┐
  CANDIDATE BROWSER  │  React · Agora RTC Web SDK             │
  ─────────────────  │  MediaPipe face model. Frames never    │
                     │  leave this box                        │
                     └───────────────┬────────────────────────┘
                               audio │ ▲ audio
                                     ▼ │
     ┌───────────── AGORA RTC CHANNEL ──────────────┐
     │  transport only: carries audio between the   │
     │  candidate (uid 2001) and the four agents    │
     └───────────────────┬──────────────────────────┘
                   audio │ ▲ audio
                         ▼ │
 ╔═══════════ AGORA CONVERSATIONAL AI ENGINE ═══════════════════╗
 ║                                                              ║
 ║   FOUR AGENTS, one per persona, each with its own voice:     ║
 ║     Priya 1001 · Arjun 1002 · Meera 1003 · Kavya 1004        ║
 ║                                                              ║
 ║   The Engine owns the whole conversational loop:             ║
 ║     · VAD and barge-in        · speech recognition           ║
 ║     · turn detection          · Agora-managed TTS            ║
 ║     · and it INVOKES THE LLM on every turn                   ║
 ║                                                              ║
 ║   `llm.url` ─────────────────────────────┐                   ║
 ║      points at OUR gateway, not OpenAI   │                   ║
 ╚══════════════════════════════════════════╪═══════════════════╝
                                            │
     when the candidate stops speaking, ALL FOUR agents POST an
     OpenAI-shaped request to that URL, at the same moment
                                            ▼
  ┌───────────────────── GATEWAY · FastAPI ─────────────────────┐
  │  POST /v1/{session}/{role}/chat/completions                 │
  │                                                             │
  │  CONDUCTOR: does {role} hold the floor this turn?           │
  │     no  → empty completion. Silent, and NO model is called  │
  │           at all, so three of the four cost nothing.        │
  │     yes → stream a reply from Groq, with seven tools.       │
  │                                                             │
  │  ONE SessionState per interview: turns · claims ·           │
  │  evidence · flags · integrity, read by every persona.       │
  │  Cross-role memory is a property of the architecture.       │
  └──────────┬──────────────────────────────────┬───────────────┘
             │ fire-and-forget, never blocking  │
             ▼                                  ▼
  ┌──────────────────────────────┐  ┌───────────────────────────┐
  │ MARKING · Gemini             │  │ PERSISTENCE · Turso        │
  │ a rubric is written when a   │  │ accounts · openings ·      │
  │ question is ASKED, before    │  │ transcripts · assessments ·│
  │ the answer exists            │  │ question plans · integrity │
  │ every mark cites a quote     │  │ (a local SQLite file when  │
  │ VERIFIED in the transcript   │  │  TURSO_URL is unset)       │
  └──────────────────────────────┘  └───────────────────────────┘
             │
             ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ CODING ROUND: problem generated from the candidate's CV,    │
  │ executed on JUDGE0, never on our own gateway                │
  └─────────────────────────────────────────────────────────────┘
```

That one fact gives us everything: four agents share one brain, the conductor
silences a persona simply by returning an empty response, and all the interview
logic is ordinary Python we can test with no voice stack running.

**Agora endpoints used:** `join`, `leave`, `interrupt`, `update`, `history`,
`turns` (per-turn latency metrics), `agents` (leak detection), plus the RTC
channel-user API to confirm the candidate is present before spending minutes.

**Agora-managed TTS** (`credential_mode: managed`), so voices are included in
the agent-minute price with no third-party TTS account.

---

## Running it locally after cloning

### 1. What you need

| | macOS / Linux | Windows |
|---|---|---|
| **Python 3.11+** | `python3.11 --version` | `py -3.11 --version` |
| **Node 18+** | `brew install node` | `winget install OpenJS.NodeJS` |
| **cloudflared** <br><span class="small">a public HTTPS address Agora can reach</span> | `brew install cloudflared` | `winget install Cloudflare.cloudflared` |
| **Pango/Cairo** <br><span class="small">only for the PDF report</span> | `brew install pango` | [GTK3 runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer). Install, then **reopen the terminal** |
| **make** | built in | not available. Use the Windows column throughout |

Everything except *Download report* works without Pango/Cairo.

### 2. Clone and install

**macOS / Linux**

```bash
git clone <repo-url>
cd EcoSphere-Coordinated_AI_Interview_Panel

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

npm --prefix frontend install
npm --prefix frontend run build
```

**Windows (PowerShell)**

```powershell
git clone <repo-url>
cd EcoSphere-Coordinated_AI_Interview_Panel

py -3.11 -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt

npm --prefix frontend install
npm --prefix frontend run build
```

> **The frontend build is not optional.** `frontend/dist/` is gitignored, so the
> built JavaScript never travels with the code, and `make serve` only builds
> it when `dist/` is *missing* (on Windows nothing builds it for you at all). After any `git pull` that touches the frontend,
> rebuild it, or you will serve yesterday's page against today's server. This is
> the single most common "it works for you but not for me".

### 3. Prove it works before touching any credentials

**macOS / Linux**

```bash
make check
```

**Windows**, the same three suites run directly:

```powershell
.venv\Scripts\python.exe -m src.mock.offline
.venv\Scripts\python.exe -m src.analysis.selftest --offline
.venv\Scripts\python.exe -m src.integrity.selftest
```

**313 regression tests. No API keys, no network, no cost.** Three suites: 152
for the conductor (floor control, the Priya→Arjun handoff, contradiction
detection, role-play launch, the difficulty ladder in both directions,
transcript handling), 84 for the marking engine (rubric shape, quote
verification, allocation arithmetic), and 77 for monitoring, GitHub verification
and the written report.

They pass with **no `.env` at all**, verified inside a container with no
credentials in it, because "run this before touching any credentials" is only
useful advice if it is true.

### 4. Credentials

```bash
cp .env.example .env          # macOS / Linux
copy .env.example .env        # Windows
```

| What | Where |
|---|---|
| `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE` | Agora Console → your project |
| `AGORA_CUSTOMER_ID`, `AGORA_CUSTOMER_SECRET` | same console, **RESTful API** section |
| `GROQ_API_KEY` | console.groq.com, free |
| `GEMINI_API_KEY` | aistudio.google.com, free |
| `GATEWAY_SHARED_SECRET` | you invent it; any random string |
| `GATEWAY_PUBLIC_URL` | written automatically by `make serve`; on Windows paste the tunnel's URL yourself (§5) |
| `TURSO_URL`, `TURSO_AUTH_TOKEN` | *optional*. A database the whole team shares. Unset, it uses a local SQLite file |
| `GITHUB_TOKEN` | *optional*. Raises the GitHub API limit from 60/hour to 5000 |

Two things that cost people an hour each:

- **App Certificate and Customer Secret are different credentials.** The
  certificate signs RTC tokens; the Customer pair authenticates REST calls.
- **Conversational AI Engine must be enabled** on the Agora project, or every
  join fails with a confusing error.

### 5. Run it

**macOS / Linux: one command**

```bash
make serve
```

It stops anything stale, starts the tunnel, waits for DNS to actually resolve,
writes the address into `.env`, **then** starts the gateway, in that order,
because the gateway reads the address once at startup. Getting the order wrong
is what produces agents that join and sit in silence.

**Windows: two terminals, in this order**

```powershell
# Terminal 1: the tunnel. Leave it running.
cloudflared tunnel --url http://localhost:7860
```

Copy the `https://….trycloudflare.com` line it prints into `.env` as
`GATEWAY_PUBLIC_URL=`. **Yours, not a teammate's**. Two people cannot share a
tunnel.

```powershell
# Terminal 2: the gateway. Start it AFTER saving that URL.
.venv\Scripts\python.exe -m src.gateway.app
```

Then open **http://localhost:7860** and create the first accounts:

```bash
.venv/bin/python -m scripts.seed_users            # macOS / Linux
.venv\Scripts\python.exe -m scripts.seed_users    # Windows
```

Passwords are generated and printed **once**. Write them down, because only a hash is
stored, so a forgotten password means making another account.

### 6. Stopping

**macOS / Linux**

```bash
make kill      # gateway and tunnel
make status    # what is running, and any Agora agents still billing
make stop      # stop every Agora agent. Do this if unsure
```

**Windows**: close both terminals, then check nothing is still billing:

```powershell
.venv\Scripts\python.exe -m scripts.panel --stop-all
```

> Agora bills per **agent-minute**. Four interviewers in a ten-minute interview
> costs forty agent-minutes, not ten. Always check for stray agents after a
> live test: `make status` on macOS, `scripts.panel --stop-all` on Windows.

---

## Every command

`make` is a convenience wrapper, not a dependency. Every target is a one-line
Python or npm command underneath, so Windows loses nothing but the shorthand.

| What it does | macOS / Linux | Windows |
|---|---|---|
| 313 regression tests, no keys | `make check` | the three `selftest` commands in §3 |
| Tunnel + gateway + UI | `make serve` | the two terminals in §5 |
| Rebuild the browser app | `make ui` | `npm --prefix frontend run build` |
| Full interview vs an AI candidate | `make sim` | `.venv\Scripts\python.exe -m src.mock.replay --turns 8` |
| One interview marked end to end | `make demo` | `.venv\Scripts\python.exe -m src.analysis.demo` |
| 35 marking checks with a real model | `make score` | `.venv\Scripts\python.exe -m src.analysis.selftest` |
| One interviewer, to prove interruption works | `make gate` | `.venv\Scripts\python.exe -m scripts.day1_interrupt_test` |
| Who can sit on the panel | `make roles` | `.venv\Scripts\python.exe -m scripts.panel --list-roles` |
| Stop every Agora agent | `make stop` | `.venv\Scripts\python.exe -m scripts.panel --stop-all` |
| Seed the first accounts | `.venv/bin/python -m scripts.seed_users` | `.venv\Scripts\python.exe -m scripts.seed_users` |

> **The only difference that matters** is the virtualenv path:
> `.venv/bin/python` on macOS and Linux, `.venv\Scripts\python.exe` on Windows.
> Everything else is the same code.

---

## Deployment

Live on **Render**, built from the `Dockerfile` at the repo root: a multi-stage
build (Node compiles the browser app, Python serves it), non-root, one port.

**Why deploying mattered.** A cloudflared quick tunnel invents a new hostname
every time it starts, and can silently stop resolving while the process stays
alive, and the agents then call an address that no longer exists and sit in the
channel in silence, billing. That happened four times during development.
Deploying removes the tunnel rather than working around it, and
`GATEWAY_PUBLIC_URL` no longer needs setting at all: the gateway works out its
own address from the incoming request and refuses to join agents unless it is
HTTPS.

`docs/DEPLOY.md` covers the same for Hugging Face Spaces, including building and
testing the image locally first.

---

## Where things live

```
src/contract.py        the one function Agora ultimately calls
src/gateway/           the fake-model endpoint, SSE framing, auth, 37 routes
src/agora/             join · leave · interrupt · tokens · channel · panel
src/conductor/         personas.yaml · floor control · seven tools · difficulty
   scenarios.py          six role-plays, and the cues that launch them
src/intake/plan.py     job advert + CV  ->  a per-persona question plan
src/state/             ledgers, shared session memory, SQLite/Turso
src/analysis/          the marking engine
   quick.py              instant heuristics, inline, no model call
   rubrics.py            a marking scheme per question, built when it is ASKED
   scorer.py             coverage + evidence quotes; the model observes,
                         Python does the arithmetic
   allocation.py         how many marks each question was worth (pure maths)
   claims.py             quantified claims and contradiction detection
   judge.py              Gemini access for marking: one model, or no score
   leaderboard.py        ranking the candidates for one opening
src/coding/            generated question · Judge0 sandbox · resumable round
src/integrity/         focus and camera signals: advisory, never a mark
src/verify/            checking a candidate's public GitHub
src/report/            the assessment as a filed PDF (WeasyPrint)
src/mock/              AI candidate · full-interview harness · 152 offline tests
frontend/src/          React app; integrity.js keeps the camera in the browser
```

Adding a sixth interviewer is an entry in `src/conductor/personas.yaml` and
nothing else. Role routing, handoff targets and the question planner all read
that file at runtime.

---

## Scoring, and what is normalised

```
marks earned = coverage × marks available − penalty
```

Two independent halves. `scorer.py` answers *"how much of a good answer was
that?"* It depends on the answer and needs a model. `allocation.py` answers
*"how many marks was that question worth?"* That depends only on the shape of
the interview, and is therefore **pure arithmetic with no model call, no key, no
cost and no non-determinism.**

**Against the instrument, done.** Two candidates sit different interviews.
Every mark is a proportion of one fixed nominal, so a three-question interview
and a nine-question one both report out of 100. Difficulty bands scale marks
×0.8 / ×1.0 / ×1.25 **without being renormalised**, so covering 80% of *hard*
questions reaches full marks while 80% of *easy* reaches 64%. Renormalising
would make difficulty weighting a no-op. The coding round uses the same
multipliers, because every candidate gets a *different* generated problem and
whoever drew the harder one would otherwise be punished for the draw.

**Against the cohort, refused.** No z-scores. A candidate's mark must not move
because a strong applicant applied the same week. Defensible for grading on a
curve; wrong for deciding whether to hire one person.

**What cannot be normalised** is rubric strictness: each question gets its own
generated marking scheme and some are harder to satisfy. There is no principled
fix without a calibration set, so the *basis*, how many answers and how hard it
got, sits next to every score, and an interview with fewer than four answers is
called out as a small sample rather than quietly ranked.

---

## Contradiction detection, in three kinds

Asking a small model to notice a conflict with something said eleven turns ago,
unprompted, while also conducting an interview, is asking it to do the thing it
is worst at. So the comparison was made narrow and the trigger mechanical, and
detection does not depend on the model choosing to volunteer anything.

There are **three** things a candidate can contradict:

1. **Their own CV.** The resume is a claim they made in writing. *"Your CV says
   you built the retry layer in Redis"* against *"I've never really used
   Redis"* is a contradiction, and the flag quotes the line of the CV that
   makes the claim.
2. **A different interviewer.** Telling Priya the team was three and Arjun that
   it was twelve only catches anyone out because this panel shares one memory,
   so the flag says *"They told Priya one thing and Arjun another."*
3. **Themselves, earlier.** The same measure, restated differently.

Honest uncertainty is deliberately **none of them**. *"I don't know the exact
number"*, *"I don't remember how much memory we used"*, all pass. A candidate
punished for admitting they cannot remember a figure learns to bluff, which is
the opposite of the point.

Two tiers. **Arithmetic, inline, no model**: numbers on the same topic, in the
same unit family, sharing subject wording, differing by two times or more, so
the conductor can route the challenge into the very next question; and **the
judge, asynchronously**, for contradictions with no numbers in them. Both quotes
are verified against the transcript before anything is recorded.

---

## Role-play, and why the conductor launches it

The same principle as contradiction detection. A tool a persona *may* call is
a request, not a mechanism. A small model conducting an interview will not
also volunteer an unrequested tool call, so role-play cannot depend on one.

So the conductor launches it. Each scenario declares cue words; a heuristic
spots them in the candidate's own answer, the conductor hands the floor to the
persona who owns that role-play, and that persona arrives **already in
character** rather than being asked to decide to be. If nobody opens a door by
40% of the way through the interview, one is opened anyway.

The cues are deliberately narrow, whole words with stems marked explicitly,
because one false role-play is worse than several missed ones. An interviewer
stepping into character for no reason is the single most jarring thing this
panel could do.

---

## Monitoring, and what it deliberately does not do

The candidate's browser watches **focus** (the tab going to the background, the
window losing focus, pasting into the editor) and the **camera**: nobody in
frame, more than one face, sustained gaze off screen, via MediaPipe's face
landmarker running on their own machine.

**No video ever leaves the browser.** Frames go to a `<video>` element and into
a model on the candidate's own computer. What reaches the server is a list of
typed events such as `no_face, 6.2s`. There is no upload, no recording, no frame
buffer. A hiring product that ships webcam footage of applicants to a hackathon
server is a breach waiting to be noticed; this one has nothing to breach.

**Nothing here can fail a candidate.** Integrity events are excluded from
`scorer.PENALISED_KINDS` and cost exactly zero marks, and the offline suite asserts
it. Every signal has an innocent explanation identical to the guilty one, so
each is stored with a `but` field naming what it cannot distinguish, and that
text renders **next to the count**, not in a footnote. A number alone on a
hiring screen is read as an accusation.

**An empty log is reported as empty, not clean.** Anything running on a machine
the candidate controls can be switched off by the candidate. The browser reports
that it is still watching every fifteen seconds, so a stretch with no heartbeat
is shown as *unmonitored time*: missing data rather than a clean record.

---

## GitHub verification, and the two questions people conflate

*Does this account exist and what is in it* is one API call. *Is it theirs* is
not answerable from the API at all, because anyone can type `torvalds` into a form.

So ownership is proven the only way it can be: the candidate publishes a code we
derive (`echosphere-verify-…`, an HMAC over their account id **and** the
username, so a published code cannot be reused for a second account) in their
GitHub bio or a public gist. Until then the badge says **claimed, not proven**.

The resume cross-check reports languages backed by public code and languages
claimed with none, and ships with the reason the second list is usually
innocent: most professional code lives in a private repository belonging to an
employer.

**LinkedIn gets no tick under any circumstances.** There is no public API and
the terms prohibit scraping, so it is stored as a link for a human to open, and
labelled exactly that.

---

## Stack

Agora Conversational AI Engine (voice, barge-in, ASR, managed TTS) ·
**Groq** `openai/gpt-oss-120b` for every spoken reply, with a Groq
second-model → OpenRouter → Cerebras fallback chain ·
**Google Gemini** for question planning, rubrics, scoring and contradiction
judging, deliberately a separate quota pool so heavy analysis can never
starve the conversation ·
FastAPI · React + Vite · MediaPipe · Judge0 · Turso/libSQL · WeasyPrint ·
Docker. Everything except Agora runs on a free tier.

> Rate limits are **per model**, not per account, which is why the fallback
> chain lists two different Groq models. Model availability also changes: both
> Groq and Gemini advertise models via their APIs that then refuse real
> requests, so the working set is probed and dated in the source rather than
> assumed.

---

## When something goes wrong

| What you see | What it means |
|---|---|
| Interviewer joins but never speaks | The tunnel's hostname stopped resolving, which it does while the process stays alive. Restart the tunnel and gateway (§5). |
| One voice works, the others are silent | A TTS voice Agora **accepts at join** but cannot synthesise. See the warning in `personas.yaml`: changing a voice is a live test, not a config edit. |
| "The panel has gone quiet" | Agora removed the agents after `AGORA_IDLE_TIMEOUT` seconds of silence. Answer within three minutes. |
| New features do not appear in the browser | Stale `frontend/dist/`. Run `npm --prefix frontend run build`. |
| `Download report` fails | Pango/Cairo missing. Everything else works without it. |
| `ModuleNotFoundError: libsql` | `pip install -r requirements.txt`, needed once `TURSO_URL` is set. |
| `401 unauthorized` in the gateway log | `GATEWAY_SHARED_SECRET` mismatch. The same value is used in both places. |

---

## Build disclosure

Built from scratch for the
EchoSphere Hackathon. See the commit history. Team Lumina: Harsh Raj (lead),
Sakshi Salot.
