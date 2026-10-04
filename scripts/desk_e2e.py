"""End-to-end: Autonomous Trading Desk against a local anvil chain (1979).

Proves the desk's on-chain risk logic, not just the plumbing:

  0. Unauthorized caller cannot invoke the Phase-2 callback.
  1. Oversized agent proposal      -> REJECTED (notional above cap), action clamped to HOLD.
  2. Compliant agent proposal      -> ACCEPTED (LONG, within every limit).
  3. Long-only desk + short signal -> REJECTED (long-only: short rejected).
  4. Malformed payload from the real AsyncDelivery address -> contained
     (DecodeFailed), delivery does NOT revert, no intent recorded.

Steps 1-3 drive the real path: desk -> 0x0820 precompile -> AsyncDelivery ->
desk callback. Step 4 impersonates AsyncDelivery via anvil to exercise the
containment branch that a live TEE could otherwise trigger.

Prereqs:
  - anvil running on RPC_URL (default http://127.0.0.1:8545, chain-id 1979)
  - `forge build` in repo root
  - `python scripts/deploy_mocks.py --agent trading` run first
  - Env: PRIVATE_KEY (funded deployer key)
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from eth_abi import decode as abi_decode, encode as abi_encode
from web3 import Web3

REPO = Path(__file__).resolve().parent.parent
RPC = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = 1979
HELPERS = REPO / "scripts" / "helpers.py"
DESK_ART = REPO / "out" / "AutonomousTradingDesk.sol" / "AutonomousTradingDesk.json"

REGISTRY = "0x9644e8562cE0Fe12b4deeC4163c064A8862Bf47F"
ASYNC_DELIVERY = "0x5A16214fF555848411544b005f7Ac063742f39F6"
AGENT_PRECOMPILE = "0x0000000000000000000000000000000000000820"

os.environ["RPC_URL"] = RPC

w3 = Web3(Web3.HTTPProvider(RPC))
assert w3.eth.chain_id == CHAIN_ID, f"chain id {w3.eth.chain_id}, expected {CHAIN_ID}"
acct = w3.eth.account.from_key(os.environ["PRIVATE_KEY"])
nonce = w3.eth.get_transaction_count(acct.address)

SHORT, HOLD, LONG = -1, 0, 1
FAILURES = []


def check(label: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"    [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def selector(sig: str) -> str:
    return Web3.keccak(text=sig)[:4].hex()


def enc(sig: str, types, values) -> str:
    return "0x" + selector(sig) + abi_encode(types, values).hex()


def send(to, data, gas=2_500_000, frm=None):
    global nonce
    frm_addr = frm if isinstance(frm, str) else acct.address
    tx = {
        "from": frm_addr, "to": to, "value": 0, "data": data,
        "chainId": CHAIN_ID, "gas": gas,
        "maxFeePerGas": w3.to_wei(5, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    }
    if frm is None:
        tx["nonce"] = nonce
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        h = w3.eth.send_raw_transaction(raw)
        nonce += 1
    else:
        # impersonated account path (anvil)
        h = w3.eth.send_transaction({k: v for k, v in tx.items() if k != "chainId"})
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    return h.hex() if hasattr(h, "hex") else h, rcpt


def deploy_bytecode(bc_hex: str) -> str:
    global nonce
    tx = {
        "from": acct.address, "nonce": nonce, "value": 0, "data": "0x" + bc_hex,
        "chainId": CHAIN_ID, "gas": 3_000_000,
        "maxFeePerGas": w3.to_wei(5, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    }
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    nonce += 1
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    assert rcpt.status == 1, "desk deploy failed"
    return rcpt["contractAddress"]


def run_helper(cmd, *args) -> dict:
    r = subprocess.run([sys.executable, str(HELPERS), cmd, *args],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"HELPER {cmd} FAILED rc={r.returncode}\n{r.stdout}\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def norm(h: str) -> str:
    return h if h.startswith("0x") else "0x" + h


def decode_intent(log):
    """IntentRecorded(bytes32 indexed jobId, int8 action, uint256 notionalUsd,
    uint16 leverage, bool accepted, string riskReason)"""
    job = log["topics"][1]
    action, notional, lev, accepted, reason = abi_decode(
        ["int8", "uint256", "uint16", "bool", "string"], bytes(log["data"]))
    return job, action, notional, lev, accepted, reason


def events(rcpt, sig: str, addr: str = None):
    topic = Web3.keccak(text=sig)
    out = []
    for log in rcpt["logs"]:
        if log["topics"] and log["topics"][0] == topic:
            if addr is None or log["address"].lower() == addr.lower():
                out.append(log)
    return out


# ── deploy desk ──────────────────────────────────────────────────────────────
art = json.load(open(DESK_ART))
bc = art["bytecode"]["object"]
bc = bc[2:] if bc.startswith("0x") else bc
DESK = deploy_bytecode(bc)
desk = w3.eth.contract(address=Web3.to_checksum_address(DESK), abi=art["abi"])
print(f"[1] AutonomousTradingDesk deployed: {DESK}")

# strategy: ETH/USD, cap 2000.00 USDC, max 5x, min 60% confidence, long-only
send(DESK, enc("setStrategy(string,uint256,uint16,uint16,bool)",
               ["string", "uint256", "uint16", "uint16", "bool"],
               ["ETH/USD", 2_000_000_000, 5, 6000, True]))
st = desk.functions.strategy().call()
print(f"    strategy: pair={st[0]} cap={st[1] / 1e6:.2f} maxLev={st[2]} minConf={st[3]} longOnly={st[4]}")

# official persistent-agent request, delivery target = the desk
pr = run_helper("build-persistent-request",
                "--rpc", RPC,
                "--registry", REGISTRY,
                "--consumer", DESK,
                "--executor-tee-address", "0x0000000000000000000000000000000000000001",
                "--da-provider", "hf",
                "--agent-rpc-url", RPC,
                "--heartbeat-chain-contract", "0xEF505E801f1Db392B5289690E2ffc20e840A3aCa",
                "--heartbeat-chain-interval-blocks", "100",
                "--heartbeat-chain-timeout-blocks", "200",
                "--agent-runtime", "zeroclaw")
REQ = norm(pr["REQUEST_INPUT"])
print(f"    official request built: provider={pr['LLM_PROVIDER']} model={pr['MODEL']} bytes={len(REQ) // 2 - 1}")

# ── 0. unauthorized callback must revert ─────────────────────────────────────
print("\n[0] unauthorized callback rejected")
try:
    w3.eth.call({"from": acct.address, "to": Web3.to_checksum_address(DESK),
                 "data": enc("onPersistentAgentResult(bytes32,bytes)",
                             ["bytes32", "bytes"], [b"\x00" * 32, b"\x00"])})
    check("non-AsyncDelivery caller reverts", False, "call did not revert")
except Exception as e:
    check("non-AsyncDelivery caller reverts", "unauthorized" in str(e).lower()
          or "revert" in str(e).lower(), str(e)[:80])


def set_agent_dec(pair, action, conf, notional, lev, reason):
    send(AGENT_PRECOMPILE, enc("setDecision(string,int8,uint16,uint256,uint16,string)",
                               ["string", "int8", "uint16", "uint256", "uint16", "string"],
                               [pair, action, conf, notional, lev, reason]), gas=500_000)


def request_and_read(label):
    txh, rcpt = send(DESK, enc("requestAnalysis(bytes)", ["bytes"], [bytes.fromhex(REQ[2:])]))
    rec = events(rcpt, "IntentRecorded(bytes32,int8,uint256,uint16,bool,string)", DESK)
    dcf = events(rcpt, "DecodeFailed(bytes32,bytes)", DESK)
    return txh, rec, dcf


# ── 1. oversized proposal -> rejected ────────────────────────────────────────
print("\n[1] agent proposes an oversized long (2500 > cap 2000)")
set_agent_dec("ETH/USD", LONG, 7200, 2_500_000_000, 3, "20d-high breakout, funding neutral")
tx, rec, _ = request_and_read("oversized")
check("IntentRecorded emitted", len(rec) == 1, f"tx={tx}")
if rec:
    job, action, notional, lev, accepted, reason = decode_intent(rec[0])
    check("accepted == false", accepted is False)
    check("action clamped to HOLD(0)", action == HOLD, f"action={action}")
    check("reason == 'notional above cap'", reason == "notional above cap", reason)

# ── 2. compliant proposal -> accepted ────────────────────────────────────────
print("\n[2] agent proposes a compliant long (1500 <= cap 2000, 75% conf, 3x)")
set_agent_dec("ETH/USD", LONG, 7500, 1_500_000_000, 3, "pullback into 0.618 retrace, spot bid")
tx, rec, _ = request_and_read("compliant")
check("IntentRecorded emitted", len(rec) == 1, f"tx={tx}")
if rec:
    job, action, notional, lev, accepted, reason = decode_intent(rec[0])
    check("accepted == true", accepted is True)
    check("action == LONG(1)", action == LONG, f"action={action}")
    check("notional == 1500.00", notional == 1_500_000_000, f"{notional / 1e6:.2f}")
    check("reason == 'accepted'", reason == "accepted", reason)

# ── 3. long-only desk rejects a short ────────────────────────────────────────
print("\n[3] agent proposes a short on a long-only desk")
set_agent_dec("ETH/USD", SHORT, 8000, 1_000_000_000, 2, "distribution below VWAP")
tx, rec, _ = request_and_read("short")
check("IntentRecorded emitted", len(rec) == 1, f"tx={tx}")
if rec:
    job, action, notional, lev, accepted, reason = decode_intent(rec[0])
    check("accepted == false", accepted is False)
    check("reason == 'long-only: short rejected'", reason == "long-only: short rejected", reason)

# ── 4. malformed payload from the REAL delivery address -> contained ─────────
print("\n[4] malformed payload delivered from AsyncDelivery (impersonated)")
before = desk.functions.intentCount().call()
w3.provider.make_request("anvil_impersonateAccount", [ASYNC_DELIVERY])
w3.provider.make_request("anvil_setBalance", [ASYNC_DELIVERY, hex(10 ** 18)])
try:
    tx, rcpt = send(DESK, "0x" + selector("onPersistentAgentResult(bytes32,bytes)")
                    + abi_encode(["bytes32", "bytes"], [b"\xab" * 32, bytes.fromhex("deadbeef")]).hex(),
                    gas=500_000, frm=ASYNC_DELIVERY)
    check("delivery did not revert (status 1)", rcpt.status == 1)
    dcf = events(rcpt, "DecodeFailed(bytes32,bytes)", DESK)
    check("DecodeFailed emitted", len(dcf) == 1, f"count={len(dcf)}")
    check("intent NOT recorded", desk.functions.intentCount().call() == before,
          f"{before} -> {desk.functions.intentCount().call()}")
finally:
    w3.provider.make_request("anvil_stopImpersonatingAccount", [ASYNC_DELIVERY])

# ── 5. on-chain state summary ────────────────────────────────────────────────
print("\n[5] on-chain desk state")
n = desk.functions.intentCount().call()
acc = desk.functions.acceptedCount().call()
rej = desk.functions.rejectedCount().call()
print(f"    intents={n} accepted={acc} rejected={rej}")
check("3 intents recorded (malformed payload records none)", n == 3, f"n={n}")
check("1 accepted", acc == 1, f"accepted={acc}")
check("2 rejected", rej == 2, f"rejected={rej}")
latest = desk.functions.latestIntent().call()
check("latest intent is the rejected short", latest[7] is False and latest[3] == HOLD,
      f"action={latest[3]} accepted={latest[7]}")

print()
if FAILURES:
    print(f"DESK E2E FAILED: {len(FAILURES)} assertion(s) -> {FAILURES}")
    sys.exit(1)
print("DESK E2E PASSED: real risk gate enforced on-chain — oversized, wrong-direction and")
print("malformed agent proposals were all contained; the compliant one became an intent.")
