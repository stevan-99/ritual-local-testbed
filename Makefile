SHELL := /bin/bash
RPC ?= http://127.0.0.1:8545
# Deployer key — anvil default account 0 by default (matches `anvil` key below).
PRIVATE_KEY ?= 0xac09...3d5b
export RPC PRIVATE_KEY

.PHONY: deps build anvil down deploy-mocks e2e all clean

# Install Python deps for the helper scripts (web3, eth-abi, eciespy, coincurve).
deps:
	python3 -m pip install --quiet web3 eth-abi eciespy coincurve

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

# Fund the deployer, deploy mocks, patch them onto the canonical addresses.
deploy-mocks:
	python3 scripts/deploy_mocks.py

# Full end-to-end: deploy consumer, DKMS flow, spawn persistent agent, poll Phase-2.
e2e:
	python3 scripts/e2e.py

# Fresh anvil + fund + mocks + e2e in one shot.
all: deps build
	@$(MAKE) anvil
	@curl -s -X POST $(RPC) -H "Content-Type: application/json" \
	  -d '{"jsonrpc":"2.0","id":1,"method":"anvil_setBalance","params":["0xE33154480053b2b9dA4365f2f0D13FAc72BaD1B4","0xDE0B6B3A7640000"]}' > /dev/null
	@echo "funded deployer 0xE33154480053b2b9dA4365f2f0D13FAc72BaD1B4"
	@$(MAKE) deploy-mocks
	@$(MAKE) e2e

clean:
	rm -rf out cache
