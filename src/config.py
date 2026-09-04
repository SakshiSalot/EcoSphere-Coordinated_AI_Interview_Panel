"""Settings, loaded once from .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return v


# --- Agora ---
AGORA_APP_ID = os.getenv("AGORA_APP_ID", "").strip()
AGORA_APP_CERTIFICATE = os.getenv("AGORA_APP_CERTIFICATE", "").strip()
AGORA_CUSTOMER_ID = os.getenv("AGORA_CUSTOMER_ID", "").strip()
AGORA_CUSTOMER_SECRET = os.getenv("AGORA_CUSTOMER_SECRET", "").strip()
AGORA_REST_BASE = "https://api.agora.io/api/conversational-ai-agent/v2/projects"
# Seconds of candidate silence before Agora removes an agent from the channel.
#
# THIS IS A THINKING BUDGET, not a crash timer, and 30 was badly wrong: it is
# shorter than one question-and-answer cycle. A live interview died mid-way
# because the candidate spent more than half a minute considering "how did you
# prioritise safety over throughput" — all four agents left, and from the
# candidate's side the panel simply stopped existing, with no error anywhere.
#
# The cost of raising it is that a crashed test bills until it expires. Three
# minutes of thinking room is worth a few stray agent-minutes; an interview
# that ends itself because somebody paused to think is not.
AGORA_IDLE_TIMEOUT = int(os.getenv("AGORA_IDLE_TIMEOUT", "180"))

# --- Providers ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

# --- Gateway ---
GATEWAY_SHARED_SECRET = os.getenv("GATEWAY_SHARED_SECRET", "").strip()
GATEWAY_PUBLIC_URL = os.getenv("GATEWAY_PUBLIC_URL", "").rstrip("/")

# --- Panel ---
PANEL_SIZE = int(os.getenv("PANEL_SIZE", "2"))
# Explicit panel by role name, e.g. "technical,hiring_manager,customer".
# Overrides PANEL_SIZE when set.
PANEL_ROLES = os.getenv("PANEL_ROLES", "").strip()

# How many candidate answers before the panel wraps up, if the question plan
# has not already been covered. A demo runs four minutes; fourteen answers is
# a real screening interview and far more than anyone will sit through on
# stage.
INTERVIEW_MAX_TURNS = int(os.getenv("INTERVIEW_MAX_TURNS", "14"))
SILENCE_MODE = os.getenv("SILENCE_MODE", "empty_completion").strip()

PERSONAS_FILE = ROOT / "src" / "conductor" / "personas.yaml"


def require_agora() -> None:
    """Fail loudly before spending an agent-minute on a misconfigured run."""
    for n in ("AGORA_APP_ID", "AGORA_CUSTOMER_ID", "AGORA_CUSTOMER_SECRET"):
        _req(n)
    if not GATEWAY_PUBLIC_URL:
        raise RuntimeError(
            "GATEWAY_PUBLIC_URL is not set. Agora calls your gateway over the "
            "public internet — localhost is unreachable by definition. Start a "
            "cloudflared tunnel and put its https:// URL here."
        )
    if not GATEWAY_SHARED_SECRET or GATEWAY_SHARED_SECRET == "change-me-to-something-random":
        raise RuntimeError(
            "GATEWAY_SHARED_SECRET is unset or still the placeholder. Your "
            "public endpoint would be open to anyone who finds the URL."
        )
