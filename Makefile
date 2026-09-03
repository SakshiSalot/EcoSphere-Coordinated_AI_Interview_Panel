.PHONY: help serve ui check score demo demo-live sim gateway gateway-live tunnel interview gate roles inputs stop kill status

PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn
PORT := 7860

# Override on the command line:
#   make interview JD=ml-engineer.txt RESUME=harsh.pdf ROLES=technical,customer
JD     ?= ml-engineer.txt
RESUME ?= harsh.pdf
ROLES  ?= technical,product
TITLE  ?= Machine Learning Engineer
NAME   ?= Candidate
CHANNEL?= interview
PERSONA?= strong

help:
	@echo ""
	@echo "  FREE — no keys, no network, no minutes"
	@echo "    make check         273 regression tests, under a minute"
	@echo "    make roles         who can sit on the panel"
	@echo "    make inputs        what is in inputs/"
	@echo ""
	@echo "  RUN THE PRODUCT"
	@echo "    make serve         tunnel + gateway + UI, addresses wired — USE THIS"
	@echo "    make ui            rebuild the browser app after frontend changes"
	@echo ""
	@echo "  RUNNING THE PIECES SEPARATELY"
	@echo "    make gateway       the gateway, auto-reloading (development)"
	@echo "    make gateway-live  the gateway, no reload (use for live tests)"
	@echo "    make tunnel        public HTTPS Agora can reach"
	@echo ""
	@echo "  NEEDS MODEL KEYS — still no Agora minutes"
	@echo "    make sim           full interview vs the AI candidate"
	@echo "    make demo          one interview marked end to end"
	@echo "    make score         35 more marking checks, real judging (~2 min)"
	@echo "    make demo-live     mark a live AI-candidate interview"
	@echo ""
	@echo "  COSTS AGORA MINUTES"
	@echo "    make gate          one interviewer, prove interruption works"
	@echo "    make interview     the real thing, personalised from a CV"
	@echo ""
	@echo "  STOPPING"
	@echo "    make stop          stop every Agora agent (do this if unsure)"
	@echo "    make kill          stop the gateway and the tunnel"
	@echo "    make status        what is running, and any agents still billing"
	@echo ""

# --- free ------------------------------------------------------------------

# Both free suites: 35 conductor checks + 64 marking checks. Neither spends a
# model call — the marking suite stubs the judge under --offline.
check:
	@$(PY) -m src.mock.offline
	@$(PY) -m src.analysis.selftest --offline
	@$(PY) -m src.integrity.selftest

# The other 35 marking checks, which DO judge with a real model (~2 min).
score:
	@$(PY) -m src.analysis.selftest

# One interview marked end to end: rubrics, evidence, allocation, assessment.
demo:
	@$(PY) -m src.analysis.demo

# The same, but interviewing a live AI candidate instead of a fixed transcript.
demo-live:
	@$(PY) -m src.analysis.demo --live --persona $(PERSONA)

sim:
	@$(PY) -m src.mock.replay --turns 8 --persona strong

roles:
	@$(PY) -m scripts.panel --list-roles

inputs:
	@$(PY) -m scripts.panel --list-inputs

# --- running ---------------------------------------------------------------

# --reload picks up code changes automatically. Use this while building: a
# stale gateway silently serving old code cost us an evening.
# Everything, in the right order, with the tunnel address wired into .env.
# Use this to demo or to test voice — it is the only target that guarantees
# Agora can actually reach the gateway.
serve:
	@scripts/serve.sh

# Build the browser app. The gateway serves frontend/dist, so without this
# the site is a 404 at /.
ui:
	@npm --prefix frontend install --silent && npm --prefix frontend run build

gateway:
	$(UVICORN) src.gateway.app:app --port $(PORT) --reload

# No reload. Session state lives in memory, so a reload mid-interview would
# wipe the transcript and the question plan.
gateway-live:
	$(UVICORN) src.gateway.app:app --port $(PORT)

tunnel:
	@echo "Copy the https:// URL below into .env as GATEWAY_PUBLIC_URL"
	cloudflared tunnel --url http://localhost:$(PORT)

# --- costs minutes ---------------------------------------------------------

gate:
	@$(PY) -m scripts.day1_interrupt_test

interview:
	@$(PY) -m scripts.panel --channel $(CHANNEL) --jd $(JD) --resume $(RESUME) \
		--roles $(ROLES) --title "$(TITLE)" --name "$(NAME)"

# --- stopping --------------------------------------------------------------

stop:
	@$(PY) -m scripts.panel --stop-all

kill:
	@pkill -f "uvicorn src.gateway.app" 2>/dev/null && echo "  gateway stopped" || echo "  gateway was not running"
	@pkill -f "cloudflared tunnel" 2>/dev/null && echo "  tunnel stopped" || echo "  tunnel was not running"

status:
	@echo "  gateway :" $$(pgrep -f "uvicorn src.gateway.app" >/dev/null && echo running || echo stopped)
	@echo "  tunnel  :" $$(pgrep -f "cloudflared tunnel" >/dev/null && echo running || echo stopped)
	@curl -fsS --max-time 5 http://localhost:$(PORT)/health 2>/dev/null && echo "" || echo "  health  : no answer on :$(PORT)"
	@$(PY) -c "import asyncio;from src.agora.session import survivors;n=asyncio.run(survivors());print(f'  agents  : {n} running' + ('  <-- BILLING, run: make stop' if n else ''))"
