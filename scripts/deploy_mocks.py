"""Deploy mock Ritual system contracts onto a local anvil (chain 1979).

Manifest-driven: the mock set, the canonical addresses it patches, and the
agent payload the mock returns all come from a JSON manifest. Adding a new
agent behaviour means writing a JSON file — no Solidity required.

    python3 scripts/deploy_mocks.py                       # mocks/persistent.json
    python3 scripts/deploy_mocks.py --agent trading       # mocks/trading.json
    python3 scripts/deploy_mocks.py --manifest my.json    # your own set

Manifest shape
--------------
    {
      "chain_id": 1979,
      "artifacts_dir": "out",
      "source": "Mocks.sol",
      "mocks": [
        { "name": "MockTEERegistry", "address": "0x9644…" },
        { "name": "MockAgentGeneric", "address": "0x…0820",
          "init": { "payload_file": "mocks/payloads/my-response.json" } }
      ]
    }

`init` is applied to the patched address after the code is placed, and accepts:

    { "payload_file": "<path>" }   raw ABI tuple from {abi_types, values}
    { "decision": {…} }            setDecision(pair,action,confidenceBps,
                                               notionalUsd,leverage,reasoning)
    { "job_id": "0x…" }            setJobId(bytes32)

Requires: anvil on RPC_URL (chain-id 1979) and `forge build` in the repo root.
Env: PRIVATE_KEY (0x-prefixed deployer key, funded on this anvil).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from eth_abi import encode as abi_encode
from web3 import Web3

REPO = Path(__file__).resolve().parent.parent
RPC = os.environ.get("RPC_URL", "http://127.0.0.1:8545")

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--manifest", help="path to a mock manifest JSON (default: mocks/<agent>.json)")
parser.add_argument("--agent", choices=("persistent", "trading"), default="persistent",
                    help="shortcut for the bundled manifests (default: persistent)")
parser.add_argument("--quiet", action="store_true")
args = parser.parse_args()

MANIFEST = Path(args.manifest) if args.manifest else REPO / "mocks" / f"{args.agent}.json"
if not MANIFEST.is_absolute():
    MANIFEST = REPO / MANIFEST if (REPO / MANIFEST).exists() else MANIFEST
if not MANIFEST.exists():
    sys.exit(f"manifest not found: {MANIFEST}")

manifest = json.loads(MANIFEST.read_text())
CHAIN_ID = int(manifest.get("chain_id", 1979))
ARTIFACTS = REPO / manifest.get("artifacts_dir", "out")
SOURCE = manifest.get("source", "Mocks.sol")
ENTRIES = manifest["mocks"]

PK = os.environ["PRIVATE_KEY"]
w3 = Web3(Web3.HTTPProvider(RPC))
got = w3.eth.chain_id
assert got == CHAIN_ID, f"chain id {got}, manifest expects {CHAIN_ID}"
acct = w3.eth.account.from_key(PK)
nonce = w3.eth.get_transaction_count(acct.address)

if not args.quiet:
    print(f"manifest: {MANIFEST.relative_to(REPO) if MANIFEST.is_relative_to(REPO) else MANIFEST}")
    print(f"deployer: {acct.address}")


def selector(sig: str) -> str:
    return Web3.keccak(text=sig)[:4].hex()


def send(to: str, data: str, gas: int = 2_000_000) -> dict:
    global nonce
    tx = {
        "from": acct.address, "to": Web3.to_checksum_address(to), "value": 0, "data": data,
        "chainId": CHAIN_ID, "gas": gas,
        "maxFeePerGas": w3.to_wei(5, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    }
    signed = acct.sign_transaction(tx | {"nonce": nonce})
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    nonce += 1
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    assert rcpt.status == 1, f"tx to {to} reverted ({h.hex()})"
    return rcpt


def deploy(name: str) -> str:
    art = json.loads((ARTIFACTS / SOURCE / f"{name}.json").read_text())
    bc = art["bytecode"]["object"]
    bc = bc[2:] if bc.startswith("0x") else bc
    # contract creation carries no `to` field
    global nonce
    tx = {
        "from": acct.address, "nonce": nonce, "value": 0, "data": "0x" + bc,
        "chainId": CHAIN_ID, "gas": 3_000_000,
        "maxFeePerGas": w3.to_wei(5, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    }
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    nonce += 1
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    assert rcpt.status == 1, f"{name} deploy failed"
    return rcpt["contractAddress"]


def apply_init(target: str, init: dict) -> str:
    """Returns a short human-readable description of what was applied."""
    if "payload_file" in init:
        p = Path(init["payload_file"])
        if not p.is_absolute():
            p = REPO / p if (REPO / p).exists() else p
        spec = json.loads(p.read_text())
        encoded = abi_encode(spec["abi_types"], spec["values"])
        send(target, "0x" + selector("setPayload(bytes)") + abi_encode(["bytes"], [encoded]).hex(),
             gas=500_000)
        return f"payload_file={p.name} ({len(encoded)} bytes, {len(spec['abi_types'])} fields)"

    if "decision" in init:
        d = init["decision"]
        fields = ["string", "int8", "uint16", "uint256", "uint16", "string"]
        values = [d["pair"], int(d["action"]), int(d["confidenceBps"]),
                  int(d["notionalUsd"]), int(d["leverage"]), d["reasoning"]]
        send(target, "0x" + selector("setDecision(string,int8,uint16,uint256,uint16,string)")
             + abi_encode(fields, values).hex(), gas=500_000)
        return f"decision={d['pair']} action={d['action']} notional={d['notionalUsd'] / 1e6:.2f}"

    if "job_id" in init:
        raw = bytes.fromhex(init["job_id"][2:])
        assert len(raw) == 32, "job_id must be 32 bytes"
        send(target, "0x" + selector("setJobId(bytes32)") + raw.hex(), gas=200_000)
        return f"job_id={init['job_id'][:18]}…"

    return "no init"


agent_desc = ""
for entry in ENTRIES:
    name = entry["name"]
    addr = entry["address"]
    dep = deploy(name)
    runtime = w3.eth.get_code(Web3.to_checksum_address(dep)).hex()
    runtime = runtime[2:] if runtime.startswith("0x") else runtime
    w3.provider.make_request("anvil_setCode", [Web3.to_checksum_address(addr), "0x" + runtime])
    live = w3.eth.get_code(Web3.to_checksum_address(addr))
    assert len(live) > 2, f"{name} did not land at {addr}"

    note = ""
    if entry.get("init"):
        note = "  init: " + apply_init(addr, entry["init"])
        if addr.lower().endswith("0820"):
            agent_desc = note

    if not args.quiet:
        pad = " " * max(0, 22 - len(name))
        print(f"{name}{pad} {addr}  len={len(live)}{note}")

if agent_desc:
    print(f"AGENT AT 0x0820:{agent_desc}")
print(f"ALL MOCKS IN PLACE ({len(ENTRIES)} contracts, {MANIFEST.name})")
