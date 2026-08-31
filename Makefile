.PHONY: help check sim gateway gateway-live tunnel interview gate roles inputs stop kill status

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

help:
	@echo ""
	@echo "  FREE — no Agora minutes, no keys needed"
	@echo "    make check         26 regression tests, under a second"
	@echo "    make sim           full interview vs the AI candidate"
	@echo "    make roles         who can sit on the panel"
	@echo "    make inputs        what is in inputs/"
	@echo ""
	@echo "  RUNNING — two terminals, left open"
	@echo "    make gateway       the gateway, auto-reloading (development)"
	@echo "    make gateway-live  the gateway, no reload (use for live tests)"
	@echo "    make tunnel        public HTTPS Agora can reach"
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

check:
	@$(PY) -m src.mock.offline

sim:
	@$(PY) -m src.mock.replay --turns 8 --persona strong

roles:
	@$(PY) -m scripts.panel --list-roles

inputs:
	@$(PY) -m scripts.panel --list-inputs

# --- running ---------------------------------------------------------------

# --reload picks up code changes automatically. Use this while building: a
# stale gateway silently serving old code cost us an evening.
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
