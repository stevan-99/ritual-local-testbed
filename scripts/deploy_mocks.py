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

`init` is applied to the patched address after the code is placed. Keys are
additive, so one entry can set several at once:

    { "payload_file": "<path>" }   setPayload(abi.encode(abi_types, values))
                                   → MockLongRunningGeneric Phase-2 result
    { "response_file": "<path>" }  setResponse(abi.encode(abi_types, values))
                                   → MockSyncGeneric / MockShortAsyncGeneric reply
    { "decision": {…} }            shorthand: encodes a trade decision into the
                                   payload (no Solidity-side encoder needed)
    { "job_id": "0x…" }            setJobId(bytes32)
    { "task_id": "…" }             setTaskId(string) — string launch handle
    { "layout": { "target_word": N, "selector_word": M,
                  "launch_as_string": true|false } }
                                   setLayout(...) on MockLongRunningGeneric.
                                   Field offsets differ per precompile, so they
                                   are configuration, not code.

Contract classes by execution model:

    MockSyncGeneric         ONNX 0x0800, JQ 0x0803, Ed25519 0x0009 …
    MockShortAsyncGeneric   HTTP 0x0801, LLM 0x0802
    MockLongRunningGeneric  0x0805, 0x0806, 0x0807, 0x080C, 0x0818-0x081A, 0x0820

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


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = REPO / p if (REPO / p).exists() else p
    return p


def _split_top(s: str):
    """Split a tuple type's inner types on top-level commas only."""
    depth, cur, out = 0, "", []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _coerce_one(t: str, v):
    """JSON has no bytes type, so hex strings stand in for bytes/bytesN.

    eth_abi wants real bytes objects, so convert here rather than forcing every
    payload file to carry an out-of-band encoding hint.
    """
    t = t.strip()
    if t.startswith("(") and t.endswith(")"):
        subs = _split_top(t[1:-1])
        if isinstance(v, (list, tuple)):
            return tuple(_coerce_one(sub, x) for sub, x in zip(subs, v))
        return v
    if t.endswith("[]"):
        base = t[:-2]
        if isinstance(v, (list, tuple)):
            return [_coerce_one(base, x) for x in v]
        return v
    if t == "bytes" or (t.startswith("bytes") and t[5:].isdigit()):
        # JSON cannot express bytes, so a string here is always hex. Accept it
        # with or without the 0x prefix rather than making the prefix load-bearing.
        if isinstance(v, str):
            h = v[2:] if v.startswith("0x") else v
            if len(h) % 2:
                raise ValueError(f"odd-length hex for {t}: {v!r}")
            return bytes.fromhex(h)
        return v
    if t == "bool" and isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return v


def _coerce(types, values):
    assert len(types) == len(values), f"abi_types/values length mismatch ({len(types)} vs {len(values)})"
    return [_coerce_one(t, v) for t, v in zip(types, values)]


def _encode_spec(path_str: str):
    """Read a {abi_types, values} spec and ABI-encode it."""
    p = _resolve(path_str)
    spec = json.loads(p.read_text())
    values = _coerce(spec["abi_types"], spec["values"])
    return abi_encode(spec["abi_types"], values), p, len(spec["abi_types"])


def apply_init(target: str, init: dict) -> str:
    """Apply every init key present. Returns a short human-readable summary."""
    notes = []

    if "payload_file" in init:
        encoded, p, n = _encode_spec(init["payload_file"])
        send(target, "0x" + selector("setPayload(bytes)") + abi_encode(["bytes"], [encoded]).hex(),
             gas=500_000)
        notes.append(f"payload_file={p.name} ({len(encoded)}B, {n} fields)")

    if "response_file" in init:
        encoded, p, n = _encode_spec(init["response_file"])
        send(target, "0x" + selector("setResponse(bytes)") + abi_encode(["bytes"], [encoded]).hex(),
             gas=500_000)
        notes.append(f"response_file={p.name} ({len(encoded)}B, {n} fields)")

    if "response_raw" in init:
        raw = init["response_raw"]
        data = bytes.fromhex(raw[2:] if raw.startswith("0x") else raw)
        send(target, "0x" + selector("setResponse(bytes)") + abi_encode(["bytes"], [data]).hex(),
             gas=500_000)
        notes.append(f"response_raw ({len(data)}B)")

    if "decision" in init:
        # Same field order as the desk's AgentDecision — encoded here so the
        # contract needs no domain-specific setter.
        d = init["decision"]
        fields = ["string", "int8", "uint16", "uint256", "uint16", "string"]
        values = [d["pair"], int(d["action"]), int(d["confidenceBps"]),
                  int(d["notionalUsd"]), int(d["leverage"]), d["reasoning"]]
        encoded = abi_encode(fields, values)
        send(target, "0x" + selector("setPayload(bytes)") + abi_encode(["bytes"], [encoded]).hex(),
             gas=500_000)
        notes.append(f"decision={d['pair']} action={d['action']} notional={d['notionalUsd'] / 1e6:.2f}")

    if "job_id" in init:
        h = init["job_id"]
        h = h[2:] if h.startswith("0x") else h
        raw = bytes.fromhex(h)
        assert len(raw) == 32, f"job_id must be 32 bytes (got {len(raw)})"
        send(target, "0x" + selector("setJobId(bytes32)") + raw.hex(), gas=200_000)
        notes.append(f"job_id={init['job_id'][:18]}…")

    if "task_id" in init:
        send(target, "0x" + selector("setTaskId(string)")
             + abi_encode(["string"], [init["task_id"]]).hex(), gas=200_000)
        notes.append(f"task_id={init['task_id'][:24]}")

    if "layout" in init:
        lay = init["layout"]
        tw, sw = int(lay["target_word"]), int(lay["selector_word"])
        as_string = bool(lay.get("launch_as_string", False))
        send(target, "0x" + selector("setLayout(uint8,uint8,bool)")
             + abi_encode(["uint8", "uint8", "bool"], [tw, sw, as_string]).hex(), gas=200_000)
        notes.append(f"layout=target@{tw} selector@{sw} launch={'string' if as_string else 'bytes32'}")

    return ", ".join(notes) if notes else "no init"


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
