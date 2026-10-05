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
# Same lifecycle helper CI and the Makefile use: detached start, RPC readiness
# polled rather than slept, chain reset to genesis, deployer funded from the
# key actually present (not a hardcoded address).
export DEPL="$(cast wallet address --private-key "$PRIVATE_KEY")"
./scripts/anvil_ctl.sh down > /dev/null 2>&1 || true
./scripts/anvil_ctl.sh fresh
trap './scripts/anvil_ctl.sh down >/dev/null 2>&1 || true' EXIT

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

# The template the README tells adopters to copy. Nothing else deploys it, which
# is how it came to point at a contract that had been renamed — so exercise it
# here too, not only in CI.
echo "==> the template adopters copy, deployed for real"
python3 scripts/deploy_mocks.py --manifest mocks/example-custom.json --quiet

echo "==> DONE: Ritual local testbed passed end-to-end (three pipelines)."
