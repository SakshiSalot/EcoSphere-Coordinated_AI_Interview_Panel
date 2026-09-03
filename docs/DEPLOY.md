# Deploying — and getting a URL that stops changing

## Why the tunnel keeps breaking

`cloudflared tunnel --url` is a **quick tunnel**. It invents a new random
hostname every time it starts, by design. Agora is handed that hostname when an
agent joins the channel, so a restarted tunnel leaves the agents calling an
address that no longer resolves — they sit in the call, silent, billing until
their idle timeout.

That failed four separate times during development, each time looking like a
different bug. It is not a bug to work around. **Deploy, and there is no tunnel.**

## Hugging Face Spaces

Free, gives a permanent HTTPS URL, and does not force CPU Spaces to sleep — a
judge's first click will not time out on a cold start.

### 1. Create the Space

At <https://huggingface.co/new-space>:

- **SDK:** Docker → *Blank*
- **Visibility:** Public

Your URL is then permanent:

```
https://<your-username>-<space-name>.hf.space
```

### 1b. Build it locally first

Pushing to Spaces and reading a build log is a slow way to find a typo. The
image builds and runs on any machine with Docker:

```bash
docker build -t echosphere .
docker run --rm -p 7860:7860 --env-file .env echosphere
```

Then `curl localhost:7860/health` and open the same address in a browser.

This has been done, not assumed — and it caught two things worth knowing:

- **The suites must pass inside the image, with no keys.** Run them there:
  ```bash
  docker run --rm echosphere python -m src.mock.offline
  docker run --rm echosphere python -m src.analysis.selftest --offline
  docker run --rm echosphere python -m src.integrity.selftest
  ```
  All three pass with no `.env` at all. They did not, at first: two tests
  branched on whether model keys happened to be present, so they passed on a
  developer's laptop and failed on a fresh clone — which is the machine the
  README tells people to run them on. That is fixed, and the container is how
  it was found.

- **The PDF report needs system libraries**, not just pip packages. WeasyPrint
  renders through Pango and Cairo, which is why the Dockerfile installs
  `libpango`, `libcairo2` and friends. Verified by fetching
  `/interviews/<id>/report.pdf` from the running container and checking the
  bytes really start with `%PDF-`.

### 2. Push this repository to it

```bash
git remote add space https://huggingface.co/spaces/<user>/<space-name>
git push space main
```

The `Dockerfile` at the repo root builds the browser app and the gateway in one
image, on port 7860 — which is what Spaces expects.

### 3. Set the secrets

Space → **Settings → Variables and secrets**. Add each as a **Secret**, not a
variable, so they are not visible in the build log:

```
AGORA_APP_ID            AGORA_APP_CERTIFICATE
AGORA_CUSTOMER_ID       AGORA_CUSTOMER_SECRET
GROQ_API_KEY            GEMINI_API_KEY
GATEWAY_SHARED_SECRET
```

Optional: `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, `PANEL_ROLES`,
`INTERVIEW_MAX_TURNS`.

**You do not need to set `GATEWAY_PUBLIC_URL`.** The gateway now works out its
own address from the incoming request when the panel starts, and refuses to
join agents unless that address is HTTPS. One less thing to get wrong, and the
exact thing that kept going wrong.

### 4. Create the first accounts

Once the Space is running, from a terminal with the repo checked out:

```bash
GATEWAY=https://<user>-<space>.hf.space \
.venv/bin/python -m scripts.seed_users --remote "$GATEWAY"
```

Or open a terminal in the Space and run `python -m scripts.seed_users`.

Passwords are generated and printed **once**. Write them down — an operator
account is needed to read assessments in the demo.

> The accounts database lives at `/app/data` inside the container. Without a
> persistent volume, a rebuild starts empty and you seed again. That is fine
> for the hackathon; note it before the finale so nobody is surprised.

## Local development, still on a tunnel

```bash
make serve
```

Starts the tunnel, waits for DNS to actually resolve, writes the address into
`.env`, and only then starts the gateway — in that order, because the gateway
reads the address once at startup. Getting the order wrong is what produced
silent agents.

Logs: `/tmp/echosphere-gateway.log`, `/tmp/echosphere-tunnel.log`.

## Checking a deployment

```bash
curl https://<user>-<space>.hf.space/health
```

Then open the same URL in a browser: the sign-in page should render. If
`/health` answers but the page is blank, the frontend build stage did not run —
check the Space build log for the `npm run build` step.
