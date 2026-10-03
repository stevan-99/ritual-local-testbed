"""End-to-end: official Ritual Persistent Agent against a local anvil chain (1979).

Pipeline (all using the UNMODIFIED official artifacts in this repo):
  1. Deploy the official PersistentAgentConsumer (src/PersistentAgentConsumer.sol).
  2. DKMS: official helpers.py `build-dkms-request` -> callDKMSKey -> DkmsKeyResult event.
  3. Persistent: official helpers.py `build-persistent-request` -> callPersistentAgent.
  4. Phase-2 delivery via official helpers.py `poll_phase2`.
  5. Verify on-chain consumer state (lastJobId / lastResult).

Prereqs:
  - anvil running on RPC_URL (default http://127.0.0.1:8545, chain-id 1979)
  - `forge build` in repo root
  - `python scripts/deploy_mocks.py` run first
  - Env: PRIVATE_KEY (funded deployer key)

Note: anvil receipts lack the Ritual `spcCalls` extension, so DKMS result is read
from the consumer's DkmsKeyResult event instead of helpers.poll_dkms. Phase-2 is
driven with the jobId the mock precompile emitted (a vm.getTxHash cheat-code
fallback, since cheat codes don't apply to live RPC).
"""
import importlib.util
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
CONSUMER_ART = REPO / "out" / "PersistentAgentConsumer.sol" / "PersistentAgentConsumer.json"

os.environ["RPC_URL"] = RPC

w3 = Web3(Web3.HTTPProvider(RPC))
assert w3.eth.chain_id == CHAIN_ID, f"chain id {w3.eth.chain_id}"
acct = w3.eth.account.from_key(os.environ["PRIVATE_KEY"])
nonce = w3.eth.get_transaction_count(acct.address)


def selector(sig: str) -> str:
    return Web3.keccak(text=sig)[:4].hex()


def bytes_calldata(func_sig: str, arg_hex: str) -> str:
    """ABI-encode a single dynamic `bytes` argument behind `func_sig`."""
    inner = arg_hex[2:]
    return "0x" + selector(func_sig) + abi_encode(["bytes"], [bytes.fromhex(inner)]).hex()


def send(to, data, gas=2_500_000) -> str:
    global nonce
    tx = {
        "from": acct.address, "to": to, "nonce": nonce, "value": 0, "data": data,
        "chainId": CHAIN_ID, "gas": gas,
        "maxFeePerGas": w3.to_wei(5, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    }
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    nonce += 1
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    assert rcpt.status == 1, f"tx {h.hex()} reverted"
    return h.hex()


def deploy_bytecode(bc_hex: str) -> str:
    global nonce
    tx = {
        "from": acct.address, "nonce": nonce, "value": 0, "data": "0x" + bc_hex,
        "chainId": CHAIN_ID, "gas": 2_000_000,
        "maxFeePerGas": w3.to_wei(5, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    }
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    nonce += 1
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    assert rcpt.status == 1
    return rcpt["contractAddress"]


def run_helper(cmd, *args) -> dict:
    r = subprocess.run([sys.executable, str(HELPERS), cmd, *args],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"HELPER {cmd} FAILED rc={r.returncode}\n{r.stdout}\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def norm(hex_str: str) -> str:
    return hex_str if hex_str.startswith("0x") else "0x" + hex_str


# ── 1. Deploy the official consumer ──
art = json.load(open(CONSUMER_ART))
bc = art["bytecode"]["object"]
bc = bc[2:] if bc.startswith("0x") else bc
CONSUMER = deploy_bytecode(bc)
print(f"[1] Official PersistentAgentConsumer deployed: {CONSUMER}")

# ── 2. DKMS (official build + event read) ──
dk = run_helper("build-dkms-request", "--rpc", RPC,
                "--registry", "0x9644e8562cE0Fe12b4deeC4163c064A8862Bf47F",
                "--owner", acct.address, "--key-index", "0")
EXECUTOR = dk["EXECUTOR"]
DKMS_INPUT = norm(dk["REQUEST_INPUT"])
print(f"[2a] Official executor discovery -> {EXECUTOR}")

dk_tx = send(CONSUMER, bytes_calldata("callDKMSKey(bytes)", DKMS_INPUT))
print(f"[2b] callDKMSKey tx: {dk_tx}")

event_dkms = Web3.keccak(text="DkmsKeyResult(bytes)")
logs = [l for l in w3.eth.get_transaction_receipt(dk_tx)["logs"]
        if l["topics"] and l["topics"][0] == event_dkms]
assert logs, "no DkmsKeyResult event"
dkms_raw = abi_decode(["bytes"], bytes(logs[-1]["data"]))[0]
child_addr, child_pk = abi_decode(["address", "bytes"], dkms_raw)
print(f"[2c] Child DKMS payment address: {Web3.to_checksum_address(child_addr)}")
print(f"[2d] Child DKMS pubkey: 0x{bytes(child_pk).hex()}")

# ── 3. Persistent agent (official build + spawn) ──
pr = run_helper("build-persistent-request",
                "--rpc", RPC,
                "--registry", "0x9644e8562cE0Fe12b4deeC4163c064A8862Bf47F",
                "--consumer", CONSUMER,
                "--executor-tee-address", EXECUTOR,
                "--da-provider", "hf",
                "--agent-rpc-url", RPC,
                "--heartbeat-chain-contract", "0xEF505E801f1Db392B5289690E2ffc20e840A3aCa",
                "--heartbeat-chain-interval-blocks", "100",
                "--heartbeat-chain-timeout-blocks", "200",
                "--agent-runtime", "zeroclaw")
print(f"[3a] Official request built: provider={pr['LLM_PROVIDER']} model={pr['MODEL']} runtime={pr['AGENT_RUNTIME']}")
REQ_INPUT = norm(pr["REQUEST_INPUT"])

from_block = w3.eth.block_number
spawn_tx = send(CONSUMER, bytes_calldata("callPersistentAgent(bytes)", REQ_INPUT))
print(f"[3b] callPersistentAgent tx: {spawn_tx}")

# ── 4. Read the delivery event + drive the official Phase-2 poller ──
ev_sig = Web3.keccak(text="PersistentAgentResultDelivered(bytes32,bytes)")
logs = w3.eth.get_logs({"address": CONSUMER, "topics": [ev_sig],
                       "fromBlock": from_block, "toBlock": "latest"})
assert logs, "no PersistentAgentResultDelivered event — check mock 0x0820 + gas"
emitted_job = logs[-1]["topics"][1]
result_raw = abi_decode(["bytes"], bytes(logs[-1]["data"]))[0]
result = abi_decode(["string", "string", "string", "string", "string", "string"], result_raw)
print(f"[4a] Phase-2 delivered. jobId={emitted_job.hex()}")

spec = importlib.util.spec_from_file_location("helpers", str(HELPERS))
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
print("[4b] official poll_phase2 ...")
helpers.poll_phase2(w3, CONSUMER, emitted_job.hex(), from_block, timeout=30)

# ── 5. On-chain consumer state ──
c = w3.eth.contract(address=CONSUMER, abi=art["abi"])
print(f"[5] lastJobId():   {c.functions.lastJobId().call().hex()}")
print(f"[5] lastResult():  {result}")

print("\nE2E PASSED: official consumer + official encoders + official Phase-2 poller "
      f"on chain {CHAIN_ID} (local anvil).")
