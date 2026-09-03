# EchoSphere — one image, one port, one permanent URL.
#
# WHY THIS EXISTS AT ALL: a cloudflared quick tunnel invents a new hostname
# every time it starts. Agora is told that hostname when an agent joins, so a
# restarted tunnel leaves agents calling an address that no longer resolves —
# they sit in the channel in silence, billing. That failed four separate times
# during development. Deploying removes the tunnel rather than working around
# it.
#
# Port 7860 because that is what Hugging Face Spaces expects, and it is already
# the port everything else in this repo uses.

# --- stage 1: the browser app -------------------------------------------
FROM node:22-slim AS ui

WORKDIR /ui
# Copy the manifests alone first so `npm ci` is cached until dependencies
# actually change — a code edit should not re-download the tree.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# --- stage 2: the gateway -----------------------------------------------
FROM python:3.11-slim

# WeasyPrint renders the assessment PDF and needs the system Pango/Cairo
# stack; pip cannot supply it. Everything else here is pure Python.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        libffi8 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# A non-root user, and a HOME it can actually write to. Spaces runs the
# container unprivileged, and the default HOME is not writable — which shows up
# as an opaque permission error the first time SQLite opens the database.
RUN useradd -m -u 1000 app
ENV HOME=/home/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY inputs/jd/ ./inputs/jd/
COPY --from=ui /ui/dist ./frontend/dist

# The accounts database lives here. Mount a volume over it to keep accounts
# across a rebuild; without one, redeploying means seeding again.
RUN mkdir -p /app/data && chown -R app:app /app

USER app
EXPOSE 7860

# No --reload. Interviews live in memory, and a reload mid-call would wipe the
# transcript and the question plan.
CMD ["python", "-m", "src.gateway.app"]
