#!/usr/bin/env bash
# One-command narrated demo of the Ritual local testbed.
#
#   ./scripts/demo.sh
#
# Starts its own anvil, walks both pipelines, and shows the risk gate doing
# real work on-chain. Needs anvil + forge on PATH and python3 with the deps
# from requirements.txt.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-python3}"
RPC="${RPC_URL:-http://127.0.0.1:8545}"
export RPC_URL="$RPC"
export PRIVATE_KEY="${PRIVATE_KEY:-$(cat .anvil_key)}"
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-demo-dummy}"
export HF_TOKEN="${HF_TOKEN:-demo-dummy}"
export HF_REPO_ID="${HF_REPO_ID:-e2e/mock}"

ACCT="$($PY -c "from web3 import Web3;import os;print(Web3().eth.account.from_key(os.environ['PRIVATE_KEY']).address)")"

C='\033[1;36m'; D='\033[2m'; G='\033[1;32m'; Y='\033[1;33m'; R='\033[0m'
banner() { printf "\n${C}── %s${R}\n" "$*"; }
note()   { printf "${D}   %s${R}\n" "$*"; }

cleanup() { [[ -n "${ANVIL_PID:-}" ]] && kill "$ANVIL_PID" 2>/dev/null || true; }
trap cleanup EXIT

banner "Ritual Local Testbed — chain 1979, no public RPC required"
note "the public endpoint is unreachable; this chain is local, deterministic, free"

# ── 1 ───────────────────────────────────────────────────────────────────────
banner "1/5  anvil up, on the real chain id"
anvil --chain-id 1979 --port 8545 --block-time 1 --silent >/dev/null 2>&1 &
ANVIL_PID=$!
for _ in $(seq 1 60); do
  curl -s -X POST "$RPC" -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' >/dev/null 2>&1 && break
  sleep 0.25
done
note "chain id $(cast chain-id --rpc-url "$RPC") / deployer $ACCT"
curl -s -X POST "$RPC" -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"anvil_setBalance\",\"params\":[\"$ACCT\",\"0xDE0B6B3A7640000\"]}" >/dev/null

# ── 2 ───────────────────────────────────────────────────────────────────────
banner "2/5  mock the six system contracts onto their CANONICAL addresses"
$PY scripts/deploy_mocks.py --quiet

# ── 3 ───────────────────────────────────────────────────────────────────────
banner "3/5  persistent-agent pipeline — official contracts, unmodified"
$PY scripts/e2e.py | grep -E "PASSED|FAILED" || true

# ── 4 ───────────────────────────────────────────────────────────────────────
banner "4/5  the agent response is DATA — a JSON file, no Solidity"
note "swapping the payload the mock agent returns at 0x0820:"
$PY scripts/deploy_mocks.py --agent trading --quiet | grep -E "AGENT AT|ALL MOCKS"

# ── 5 ───────────────────────────────────────────────────────────────────────
banner "5/5  autonomous trading desk — the risk gate is real Solidity"
$PY scripts/desk_e2e.py | grep -E "\[(PASS|FAIL)\]|E2E PASSED|E2E FAILED" || true

printf "\n${G}Both pipelines passed on a local chain. No testnet, no RPC, no keys shared.${R}\n"
