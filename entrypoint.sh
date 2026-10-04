#!/usr/bin/env bash
set -euo pipefail

RPC="${RPC_URL:-http://127.0.0.1:8545}"

# Default deployer = anvil account 0 (deterministic key shipped in .anvil_key).
if [ -z "${PRIVATE_KEY:-}" ] && [ -f .anvil_key ]; then
  export PRIVATE_KEY="$(cat .anvil_key)"
fi
: "${PRIVATE_KEY:?"PRIVATE_KEY not set (and no .anvil_key present)"}"

# Load .env if the caller mounted one (OPENROUTER_API_KEY, HF_TOKEN, ...).
if [ -f .env ]; then
  set -a; source .env; set +a
fi
: "${OPENROUTER_API_KEY:?"OPENROUTER_API_KEY required (pass via env or mounted .env)"}"

echo "==> forge build"
forge build --quiet || forge build

echo "==> starting anvil (chain-id 1979)"
pkill -f "anvil --chain-id 1979" 2>/dev/null || true
anvil --chain-id 1979 --port 8545 --block-time 1 > /tmp/anvil.log 2>&1 &
ANVIL_PID=$!
trap 'kill "$ANVIL_PID" 2>/dev/null || true' EXIT
sleep 3

echo "==> funding deployer"
DEPL_ADDR="$(cast wallet address --private-key "$PRIVATE_KEY")"
curl -s -X POST "$RPC" -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"anvil_setBalance\",\"params\":[\"$DEPL_ADDR\",\"0xDE0B6B3A7640000\"]}" > /dev/null

echo "==> deploy mocks"
python3 scripts/deploy_mocks.py --manifest mocks/persistent.json

echo "==> E2E: persistent agent pipeline"
python3 scripts/e2e.py

echo "==> E2E: autonomous trading desk pipeline"
python3 scripts/deploy_mocks.py --manifest mocks/trading.json
python3 scripts/desk_e2e.py

echo "==> E2E: precompile zoo (precompiles other than 0x0820)"
python3 scripts/deploy_mocks.py --manifest mocks/zoo.json
python3 scripts/zoo_e2e.py

echo "==> DONE: Ritual local testbed passed end-to-end (three pipelines)."
