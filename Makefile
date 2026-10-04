SHELL := /bin/bash
RPC ?= http://127.0.0.1:8545
# Deployer key — anvil default account 0 by default (matches `anvil` key below).
PRIVATE_KEY ?= $(shell cat .anvil_key)
# Interpreter for the Python helpers. Override to use a venv, e.g.
#   make all-desk PY=/path/to/.venv/bin/python
PY ?= python3
export RPC PRIVATE_KEY
export RPC_URL = $(RPC)

.PHONY: deps build anvil down deploy-mocks e2e desk-mocks desk-e2e all all-desk demo \
        mocks-trading mocks-custom replay-record replay replay-golden clean

# Install Python deps for the helper scripts (web3, eth-abi, eciespy, coincurve).
deps:
	$(PY) -m pip install --quiet web3 eth-abi eciespy coincurve

# Compile mocks + official consumer (requires foundry: curl -L for foundry.sh).
build:
	forge build

# Start a local anvil on chain-id 1979 in the background.
anvil:
	@pkill -f "anvil --chain-id 1979" 2>/dev/null; sleep 1
	@nohup anvil --chain-id 1979 --port 8545 --block-time 1 > /tmp/anvil-1979.log 2>&1 &
	@sleep 3
	@echo "anvil up on $(RPC) (chain 1979)"

down:
	@pkill -f "anvil --chain-id 1979" 2>/dev/null; echo "anvil stopped"

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

# Fresh anvil + fund + mocks + e2e in one shot.
all: deps build
	@$(MAKE) anvil
	@curl -s -X POST $(RPC) -H "Content-Type: application/json" \
	  -d '{"jsonrpc":"2.0","id":1,"method":"anvil_setBalance","params":["0xE33154480053b2b9dA4365f2f0D13FAc72BaD1B4","0xDE0B6B3A7640000"]}' > /dev/null
	@echo "funded deployer 0xE33154480053b2b9dA4365f2f0D13FAc72BaD1B4"
	@$(MAKE) deploy-mocks
	@$(MAKE) e2e

# Both pipelines, one anvil: persistent E2E, then swap 0x0820 and run the desk.
all-desk: deps build
	@$(MAKE) anvil
	@curl -s -X POST $(RPC) -H "Content-Type: application/json" \
	  -d '{"jsonrpc":"2.0","id":1,"method":"anvil_setBalance","params":["0xE33154480053b2b9dA4365f2f0D13FAc72BaD1B4","0xDE0B6B3A7640000"]}' > /dev/null
	@echo "funded deployer 0xE33154480053b2b9dA4365f2f0D13FAc72BaD1B4"
	@$(MAKE) deploy-mocks
	@$(MAKE) e2e
	@$(MAKE) desk-mocks
	@$(MAKE) desk-e2e

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

# Regression gate: run the E2E against the CURRENT build and diff it against a
# golden recording. Change an event and this fails.
GOLDEN ?= replay/desk-golden.json
RUN ?= desk
replay-golden:
	$(PY) scripts/replay_run.py --golden $(GOLDEN) --run $(RUN)

clean:
	rm -rf out cache
