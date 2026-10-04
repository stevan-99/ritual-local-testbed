"""Record a chain session into a replay file.

Works against any JSON-RPC endpoint — the local testbed, or a live Ritual RPC
when one is reachable.

    # full session from a fresh chain (the deterministic case — sees everything)
    python3 scripts/replay_record.py --manifest mocks/trading.json \\
        --out replay/desk-session.json --label "desk session"

    # only what happened after a given block
    python3 scripts/replay_record.py --from-block 100 --out replay/tail.json

What is recorded, and why
-------------------------
* `nonce` per transaction. Replay re-sends with the ORIGINAL nonce, so CREATE
  addresses are reproduced exactly instead of shifting when the chain state
  differs. Without this, re-applying a mock manifest double-counts nonces and
  every contract lands somewhere new.
* `setup.code_overrides` — the runtime code sitting at each canonical mock
  address when the recording ended. Mock placement happens over RPC
  (`anvil_setCode`), not as a transaction, so it is invisible to a block scan;
  capturing it is what lets replay reconstruct the same chain.
* Which events are declared VOLATILE (`--volatile-event`), i.e. whose data payload
  legitimately changes between runs. `PrecompileCalled` is the built-in example: it
  embeds the ECIES-encrypted persistent request, and ECIES uses a fresh ephemeral
  key every run. Recording the policy next to the data keeps the comparison honest
  instead of quietly loosening it later.
* Event **signatures** AND a hash of each event's data payload per transaction,
  never addresses. Signatures catch "the event stopped firing"; the data hash catches
  "same event, wrong payload".
  Signatures are the thing that actually regresses, and they survive an address
  shift. No private keys are ever stored.

Env: RPC_URL (default http://127.0.0.1:8545)
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from web3 import Web3

REPO = Path(__file__).resolve().parent.parent
RPC = os.environ.get("RPC_URL", "http://127.0.0.1:8545")

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--rpc", default=RPC)
parser.add_argument("--out", required=True, help="output replay file")
parser.add_argument("--from-block", type=int, default=0, help="first block to record (default 0)")
parser.add_argument("--manifest", help="mock manifest to capture canonical-address code from")
parser.add_argument("--label", default="", help="human label for the session")
parser.add_argument("--volatile-event", action="append", default=[], metavar="SIG",
                    help="event signature whose DATA legitimately varies per run "
                         "(e.g. it embeds an ECIES-encrypted request). Its signature is "
                         "still asserted; only the payload hash is skipped. Repeatable.")
args = parser.parse_args()

w3 = Web3(Web3.HTTPProvider(args.rpc))
chain_id = w3.eth.chain_id
head = w3.eth.block_number
start = args.from_block

if start == 0 and head > 500:
    sys.exit(f"refusing to scan {head} blocks from 0 — pass --from-block (chain head is {head})")

transactions = []
for n in range(start, head + 1):
    block = w3.eth.get_block(n, full_transactions=True)
    for tx in block.get("transactions") or []:
        rcpt = w3.eth.get_transaction_receipt(tx["hash"])
        raw_input = tx["input"]
        transactions.append({
            # window-relative, NOT the absolute block: the golden mode derives
            # its own window from the live head, so absolute numbers differ on
            # every re-record and would drown a real change in churn. The offset
            # is what the diff can actually be read against.
            "block_offset": n - start,
            "nonce": tx["nonce"],
            "hash": tx["hash"].hex(),
            "from": tx["from"],
            "to": tx["to"],
            "value": str(tx["value"]),
            "input": (raw_input.hex() if hasattr(raw_input, "hex") else str(raw_input)).removeprefix("0x"),
            "gas": tx["gas"],
            "status": rcpt["status"],
            "gas_used": rcpt["gasUsed"],
            "log_data": [
                Web3.keccak(bytes(log["data"])).hex().removeprefix("0x").lower()
                for log in rcpt["logs"] if log["topics"]
            ],
            "log_sigs": [
                (log["topics"][0].hex() if hasattr(log["topics"][0], "hex") else str(log["topics"][0]))
                .removeprefix("0x").lower()
                for log in rcpt["logs"] if log["topics"]
            ],
            "log_addrs": [log["address"] for log in rcpt["logs"]],
        })

# ── capture canonical-address code (mock placement is RPC, not a tx) ─────────
code_overrides = {}
manifest_path = None
if args.manifest:
    manifest_path = Path(args.manifest)
    mpath = manifest_path if manifest_path.is_absolute() else (REPO / manifest_path)
    manifest = json.loads(mpath.read_text())
    for entry in manifest["mocks"]:
        addr = Web3.to_checksum_address(entry["address"])
        code = w3.eth.get_code(addr)
        if len(code) > 2:
            code_overrides[addr] = code.hex().removeprefix("0x")
    print(f"captured code at {len(code_overrides)}/{len(manifest['mocks'])} manifest addresses")

out = Path(args.out)
if not out.is_absolute():
    out = REPO / out
out.parent.mkdir(parents=True, exist_ok=True)

payload = {
    "version": 2,
    "label": args.label,
    "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    "source_rpc": args.rpc,
    "chain_id": chain_id,
    "head_block": head,
    "from_block": start,
    "mock_manifest": str(manifest_path) if manifest_path else None,
    "setup": {"code_overrides": code_overrides},
    "volatile_events": {
        Web3.keccak(text=s).hex().removeprefix("0x").lower(): s for s in args.volatile_event
    },
    "transactions": transactions,
}
out.write_text(json.dumps(payload, indent=2) + "\n")

n_ok = sum(1 for t in transactions if t["status"] == 1)
print(f"recorded {len(transactions)} txs from blocks {start}..{head} ({n_ok} succeeded)")
print(f"chain_id={chain_id}  manifest={manifest_path or '(none)'}")
print(f"-> {out.relative_to(REPO) if out.is_relative_to(REPO) else out}")
