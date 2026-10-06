#!/usr/bin/env bash
# anvil lifecycle helper.
#
# Why this exists rather than a bare `nohup anvil &` in the Makefile:
#   * `nohup ... &` inherits the caller's process group, so the daemon can be
#     reaped when the parent (make, or a shell the harness tears down) exits.
#     `setsid` + stdin from /dev/null detaches it properly.
#   * a fixed `sleep 3` is a race — too short on a loaded box, wasted time on a
#     fast one. Readiness is polled against the RPC instead.
#   * a fresh chain does NOT need a new process. `anvil_reset` returns the
#     running node to genesis, which keeps the lifecycle in one place and makes
#     "reset between recordings" work in a single non-interactive invocation.
#
# Usage: anvil_ctl.sh {up|down|restart|reset|fresh|status}
set -euo pipefail

RPC="${RPC:-http://127.0.0.1:8545}"
CHAIN_ID="${CHAIN_ID:-1979}"
PORT="${PORT:-${RPC##*:}}"
LOG="${ANVIL_LOG:-/tmp/anvil-${CHAIN_ID}.log}"
DEPL="${DEPL:-0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266}"
FUND="${FUND_WEI:-0xDE0B6B3A7640000}"
READY_TRIES="${READY_TRIES:-80}"

# Resolve the binary rather than trusting the caller's PATH: make, CI and a
# bare shell do not agree on where foundry lives (~/.foundry/bin by default).
ANVIL_BIN="${ANVIL_BIN:-$(command -v anvil 2>/dev/null || true)}"
if [ -z "$ANVIL_BIN" ]; then
  for cand in "$HOME/.foundry/bin/anvil" /usr/local/bin/anvil "$HOME/.cargo/bin/anvil"; do
    if [ -x "$cand" ]; then ANVIL_BIN="$cand"; break; fi
  done
fi
if [ -z "$ANVIL_BIN" ]; then
  echo "anvil not found on PATH, ~/.foundry/bin, /usr/local/bin or ~/.cargo/bin" >&2
  echo "install foundry: curl -L https://foundry.paradigm.xyz | bash && foundryup" >&2
  exit 1
fi

rpc() { curl -s --max-time 5 -X POST "$RPC" -H "Content-Type: application/json" -d "$1"; }

is_up() { rpc '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' | grep -q '"result"'; }

block() { rpc '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
            | sed -n 's/.*"result":"0x\([0-9a-fA-F]*\)".*/\1/p' | xargs printf '%d' 2>/dev/null || echo '?'; }

wait_up() {
  for _ in $(seq 1 "$READY_TRIES"); do
    is_up && return 0
    sleep 0.25
  done
  return 1
}

do_up() {
  if is_up; then echo "anvil already up on $RPC"; return 0; fi
  setsid "$ANVIL_BIN" --chain-id "$CHAIN_ID" --port "$PORT" --block-time 1 \
    >"$LOG" 2>&1 </dev/null &
  if wait_up; then
    echo "anvil up on $RPC (chain $CHAIN_ID, log $LOG)"
  else
    echo "anvil did not become ready in $((READY_TRIES / 4))s — last log lines:" >&2
    tail -20 "$LOG" >&2
    return 1
  fi
}

do_down() {
  if ! is_up; then echo "anvil already down"; return 0; fi
  pkill -f "anvil --chain-id $CHAIN_ID" 2>/dev/null || true
  for _ in $(seq 1 40); do
    is_up || { echo "anvil stopped"; return 0; }
    sleep 0.25
  done
  echo "anvil still responding after SIGTERM" >&2
  return 1
}

do_reset() {
  is_up || { echo "anvil is not up — run 'anvil_ctl.sh up' first" >&2; return 1; }
  rpc '{"jsonrpc":"2.0","id":1,"method":"anvil_reset","params":[]}' >/dev/null
  echo "chain reset to genesis (block $(block))"
}

do_fund() {
  rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"anvil_setBalance\",\"params\":[\"$DEPL\",\"$FUND\"]}" >/dev/null
  echo "funded deployer $DEPL"
}

case "${1:-}" in
  up)      do_up ;;
  down)    do_down ;;
  restart) do_down && do_up ;;
  reset)   do_reset ;;
  fund)    do_fund ;;
  # A chain that a recording can safely start from: present, at genesis, funded.
  fresh)   do_up && do_reset && do_fund ;;
  status)
    if is_up; then echo "up (chain $CHAIN_ID, block $(block))"; else echo "down"; exit 1; fi ;;
  *)
    echo "usage: $0 {up|down|restart|reset|fund|fresh|status}" >&2
    exit 2 ;;
esac
