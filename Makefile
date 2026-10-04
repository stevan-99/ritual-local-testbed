SHELL := /bin/bash
RPC ?= http://127.0.0.1:8545
# Deployer key — anvil default account 0 by default (matches `anvil` key below).
PRIVATE_KEY ?= $(shell cat .anvil_key)
# Interpreter for the Python helpers. Override to use a venv, e.g.
#   make all-desk PY=/path/to/.venv/bin/python
PY ?= python3
export RPC PRIVATE_KEY
export RPC_URL = $(RPC)

.PHONY: deps build anvil anvil-fresh anvil-status down fund clean-anvil-state deploy-mocks e2e desk-mocks desk-e2e zoo-mocks zoo-e2e \
        all all-desk demo mocks-trading mocks-custom replay-record record-golden record-goldens \
        replay replay-golden replay-goldens clean

# Install Python deps for the helper scripts (web3, eth-abi, eciespy, coincurve).
deps:
	$(PY) -m pip install --quiet web3 eth-abi eciespy coincurve

# Compile mocks + official consumer (requires foundry: curl -L for foundry.sh).
build:
	forge build

# anvil lifecycle lives in scripts/anvil_ctl.sh: it starts the node detached
# (setsid + stdin from /dev/null, so it survives make exiting), polls the RPC
# for readiness instead of sleeping a fixed interval, and resets an already
# running chain to genesis in place.
anvil:
	@scripts/anvil_ctl.sh up

# A chain a recording can safely start from: up, at genesis, deployer funded.
# No process restart — `anvil_reset` returns the running node to genesis.
anvil-fresh:
	@scripts/anvil_ctl.sh fresh

# A chain you can transact on WITHOUT wiping it: up (idempotent) + deployer
# funded. `fresh` is wrong for a replay -- a golden diff runs against the
# current head and never needs genesis, so resetting would destroy a chain
# the caller was already using. Funding is a state override: it mines no
# block, so it cannot widen the run window.
anvil-ready:
	@scripts/anvil_ctl.sh up
	@scripts/anvil_ctl.sh fund

anvil-status:
	@scripts/anvil_ctl.sh status

down:
	@scripts/anvil_ctl.sh down

# The committed .anvil_key is NOT one of anvil's pre-funded default accounts,
# so it needs explicit funding before any script can deploy.
DEPL ?= 0xE33154480053b2b9dA4365f2f0D13FAc72BaD1B4
export DEPL
fund:
	@scripts/anvil_ctl.sh fund

# Deploy a mock set from a manifest and patch it onto the canonical addresses.
# MANIFEST defaults to mocks/persistent.json; override for your own: MANIFEST=mocks/example-custom.json
MANIFEST ?= mocks/persistent.json
deploy-mocks:
	$(PY) scripts/deploy_mocks.py --manifest $(MANIFEST)

# Alias kept for the persistent pipeline's original name.
mocks-trading:
	$(PY) scripts/deploy_mocks.py --manifest mocks/trading.json

# Full end-to-end: deploy consumer, DKMS flow, spawn persistent agent, poll Phase-2.
e2e:
	$(PY) scripts/e2e.py

# Put the trading-agent mock at 0x0820 (returns an AgentDecision, not a spawn tuple).
desk-mocks:
	$(PY) scripts/deploy_mocks.py --manifest mocks/trading.json

# Autonomous Trading Desk E2E: risk gate, containment, on-chain intent state.
desk-e2e:
	$(PY) scripts/desk_e2e.py

# Precompile zoo: mocks for precompiles OTHER than 0x0820 — JQ 0x0803 (sync),
# HTTP 0x0801 + LLM 0x0802 (short async), LR-HTTP 0x0805 + Image 0x0818 (long
# async, two different Phase-2 callback selectors). Proves a manifest can stand
# in for any precompile, not just the persistent agent.
zoo-mocks:
	$(PY) scripts/deploy_mocks.py --manifest mocks/zoo.json

zoo-e2e:
	$(PY) scripts/zoo_e2e.py

# Fresh anvil + fund + mocks + e2e in one shot.
all: deps build
	@$(MAKE) anvil-fresh
	@$(MAKE) deploy-mocks
	@$(MAKE) e2e

# Everything, one anvil: persistent E2E, swap 0x0820 and run the desk, then the
# precompile zoo (mocks for precompiles other than 0x0820).
all-desk: deps build
	@$(MAKE) anvil-fresh
	@$(MAKE) deploy-mocks
	@$(MAKE) e2e
	@$(MAKE) desk-mocks
	@$(MAKE) desk-e2e
	@$(MAKE) zoo-mocks
	@$(MAKE) zoo-e2e

# Narrated one-command demo (starts its own anvil, runs both pipelines).
demo:
	./scripts/demo.sh

# Record a session into a replay file, then re-drive it locally.
#   make replay-record LABEL="desk session"
#   make replay FILE=replay/session.json
LABEL ?= local session
replay-record:
	$(PY) scripts/replay_record.py --manifest $(MANIFEST) --label "$(LABEL)" \
	  --volatile-event "PrecompileCalled(address,bytes,bytes)" --out replay/session.json

FILE ?= replay/session.json
replay:
	$(PY) scripts/replay_run.py --file $(FILE)

# Record a GOLDEN file for the regression gate.
#
# Golden mode captures the run window only — it deploys the mocks, notes the
# block, runs the E2E, and captures from there. So the recording must be made
# with the SAME window. Recording from block 0 (what `replay-record` does, which
# is right for a self-contained exact replay) would bake the deploy
#
# Per-run defaults: the script that produces the run, its mock manifest, and any
# event whose payload legitimately varies per run — its signature is still
# asserted, only its data is not compared.
RUN ?= desk

ifeq ($(RUN),persistent)
  RUN_SCRIPT   := scripts/e2e.py
  RUN_MANIFEST := mocks/persistent.json
  RUN_VOLATILE := --volatile-event "PrecompileCalled(address,bytes,bytes)"
else ifeq ($(RUN),zoo)
  RUN_SCRIPT   := scripts/zoo_e2e.py
  RUN_MANIFEST := mocks/zoo.json
  # every zoo result is static manifest data — nothing varies per run
  RUN_VOLATILE :=
else
  RUN_SCRIPT   := scripts/desk_e2e.py
  RUN_MANIFEST := mocks/trading.json
  RUN_VOLATILE := --volatile-event "PrecompileCalled(address,bytes,bytes)"
endif

# GOLDEN_DIR lets you record into a scratch directory (e.g. to verify the
# recorder itself without churning the committed goldens):
#   make record-goldens GOLDEN_DIR=/tmp/scratch
GOLDEN_DIR ?= replay
GOLDEN ?= $(GOLDEN_DIR)/$(RUN)-golden.json

record-golden:
	@$(PY) scripts/deploy_mocks.py --manifest $(RUN_MANIFEST) --quiet
	@B=$$(curl -s -X POST $(RPC) -H "Content-Type: application/json" \
	    -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
	    | $(PY) -c 'import json,sys;print(int(json.load(sys.stdin)["result"],16)+1)'); \
	  echo "$(RUN): run window starts at block $$B"; \
	  $(PY) $(RUN_SCRIPT) > /dev/null || exit 1; \
	  $(PY) scripts/replay_record.py --manifest $(RUN_MANIFEST) --label "$(RUN) session" \
	    $(RUN_VOLATILE) --from-block $$B --out $(GOLDEN)

# Record all three goldens. Each needs a FRESH chain: the run window is derived
# from the current head, so a previous run's transactions would otherwise fall
# inside the next recording.
record-goldens:
	@for r in desk persistent zoo; do \
	  echo "── recording $$r ──"; \
	  $(MAKE) --no-print-directory anvil-fresh > /dev/null || exit 1; \
	  $(MAKE) --no-print-directory record-golden RUN=$$r || exit 1; \
	done

# Regression gate: run the E2E against the CURRENT build and diff it against a
# golden recording. Change an event and this fails.
replay-golden:
	$(PY) scripts/replay_run.py --golden $(GOLDEN) --run $(RUN)

replay-goldens: anvil-ready
	@fail=0; \
	for r in desk persistent zoo; do \
	  printf '%-12s' "$$r"; \
	  if $(MAKE) --no-print-directory replay-golden RUN=$$r > /tmp/rg-$$r.log 2>&1; \
	    then echo "GOLDEN MATCH"; \
	    else echo "REGRESSION"; tail -12 /tmp/rg-$$r.log; fail=1; fi; \
	done; \
	exit $$fail

# anvil leaves crashed-state dumps under ~/.foundry/anvil/tmp and never prunes
# them. Long-lived testbeds can accumulate many GB — run this when the disk
# looks full. Safe: only temp state, never your project.
clean-anvil-state:
	@du -sh "$$HOME/.foundry/anvil/tmp" 2>/dev/null || echo "nothing to clean"
	@rm -rf "$$HOME/.foundry/anvil/tmp"/anvil-state-* 2>/dev/null || true
	@echo "pruned stale anvil state dumps"

clean:
	rm -rf out cache
