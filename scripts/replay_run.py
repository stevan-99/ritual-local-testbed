"""Replay a recorded session on a local anvil and diff the outcome.

The regression half of the harness: record once, then re-drive it locally on
every commit and catch the moment a mock or a contract stops emitting what it
used to emit.

    python3 scripts/replay_run.py --file replay/desk-session.json --manifest mocks/trading.json

Two modes, and they prove different things
-----------------------------------------
`--file` (exact replay) reconstructs the recorded chain and re-sends the
recorded transactions. It proves the recording is self-contained and that the
chain executes it deterministically. It does NOT re-test your source: it
re-sends recorded bytecode, so a change under `src/` is invisible to it.

`--golden` (regression) is the one that catches source regressions. It deploys
the mock set from the CURRENT build, runs the E2E script fresh, then diffs the
events it emitted against the golden recording. Change a contract so it stops
emitting what it used to emit — or emits the same event with a different
payload — and this fails.

How an exact replay is reconstructed
------------------------------------
1. `setup.code_overrides` from the recording is re-applied with `anvil_setCode`.
   Mock placement is an RPC call, not a transaction, so it cannot be replayed
   from the block scan — only from the captured code.
2. Every sender is impersonated and funded via anvil. The recorded keys are
   never needed and never stored, which is what makes a recording taken against
   a live chain replayable locally.
3. Each transaction is re-sent with its **original nonce**, so CREATE addresses
   reproduce exactly instead of drifting.
4. `status`, the ordered event **signatures**, and a hash of each event's data
   payload are diffed — the last one catches the same event firing with a wrong value.

Assertions are address-free on purpose: a nonce shift can move a CREATE address
without changing behaviour, so comparing addresses yields false alarms. Event
signatures are what actually regress.

Env: RPC_URL (default http://127.0.0.1:8545), PRIVATE_KEY (to fund senders)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from web3 import Web3

REPO = Path(__file__).resolve().parent.parent
RPC = os.environ.get("RPC_URL", "http://127.0.0.1:8545")

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--file", help="replay file (exact-replay mode)")
parser.add_argument("--golden", help="golden recording to diff a fresh run against (regression mode)")
parser.add_argument("--run", choices=("desk", "persistent", "zoo"),
                    help="which E2E script to run in regression mode")
parser.add_argument("--rpc", default=RPC)
parser.add_argument("--manifest", help="override the manifest to re-apply (defaults to the recording's)")
parser.add_argument("--deploy-mocks", action="store_true",
                    help="deploy the mock set instead of applying captured code overrides")
args = parser.parse_args()
if bool(args.golden) == bool(args.file):
    sys.exit("pass exactly one of --file (exact replay) or --golden (regression)")

# Exit codes. A failure here has two very different causes, and collapsing them
# into one code makes the output lie: a dead chain, a missing dependency, or a
# failed mock deploy is NOT "the build regressed". Callers key off these.
EXIT_HARNESS = 1  # the run could not be performed at all
EXIT_DRIFT = 2    # ran fine, behaviour drifted from the recording

SCRIPTS = {"desk": "desk_e2e.py", "persistent": "e2e.py", "zoo": "zoo_e2e.py"}


def norm(sig) -> str:
    s = sig.hex() if hasattr(sig, "hex") else str(sig)
    return s.removeprefix("0x").lower()



def capture(w3, from_block: int, to_block: int) -> list:
    """The same event capture replay_record.py writes, over a block range."""
    out = []
    for n in range(from_block, to_block + 1):
        block = w3.eth.get_block(n, full_transactions=True)
        for tx in block.get("transactions") or []:
            rcpt = w3.eth.get_transaction_receipt(tx["hash"])
            sigs = [log["topics"][0] for log in rcpt["logs"] if log["topics"]]
            out.append({
                "block_offset": n - from_block,
                "to": tx["to"] or "CREATE",
                "status": rcpt["status"],
                "log_sigs": [norm(s) for s in sigs],
                "log_data": [Web3.keccak(bytes(log["data"])).hex().removeprefix("0x").lower()
                             for log in rcpt["logs"] if log["topics"]],
            })
    return out


w3 = Web3(Web3.HTTPProvider(args.rpc))

# ── regression mode: fresh run of the CURRENT build vs a golden recording ───
if args.golden:
    gpath = Path(args.golden)
    if not gpath.is_absolute():
        gpath = REPO / gpath
    golden = json.loads(gpath.read_text())
    if golden["chain_id"] != w3.eth.chain_id:
        sys.exit(f"chain mismatch: golden={golden['chain_id']} local={w3.eth.chain_id}")
    if not args.run:
        sys.exit("--golden requires --run desk|persistent")
    manifest = args.manifest or golden.get("mock_manifest") or "mocks/trading.json"

    print(f"regression: {SCRIPTS[args.run]} vs golden {gpath.name}")
    print(f"golden recorded {golden['recorded_at']} ({len(golden['transactions'])} txs)")

    print(f"\n[1] deploying mock set from the CURRENT build: {manifest}")
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "deploy_mocks.py"),
                        "--manifest", manifest, "--quiet"],
                       capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        sys.exit("mock deploy failed")
    print("    " + r.stdout.strip().splitlines()[-1])

    volatile = set((golden.get("volatile_events") or {}).keys())
    if volatile:
        print(f"    volatile events (signature asserted, payload not compared): "
              f"{', '.join(sorted((golden['volatile_events'][k] for k in volatile)))}")

    mark = w3.eth.block_number + 1
    print(f"\n[2] running scripts/{SCRIPTS[args.run]} (from block {mark})")
    run = subprocess.run([sys.executable, str(REPO / "scripts" / SCRIPTS[args.run])],
                         capture_output=True, text=True, cwd=REPO)
    if run.returncode != 0:
        print(run.stdout[-3000:], run.stderr[-2000:])
        sys.exit(f"scripts/{SCRIPTS[args.run]} FAILED")
    print("    script passed")

    actual = capture(w3, mark, w3.eth.block_number)
    want = golden["transactions"]
    print(f"\n[3] diffing {len(actual)} fresh txs against {len(want)} golden txs")

    problems = []
    if len(actual) != len(want):
        problems.append(f"transaction count {len(want)} -> {len(actual)}")
    for i, (a, w) in enumerate(zip(want, actual)):
        if a["status"] != w["status"]:
            problems.append(f"tx[{i}] status {a['status']} -> {w['status']}")
        if a["log_sigs"] != w["log_sigs"]:
            problems.append(
                f"tx[{i}] event signatures {len(a['log_sigs'])} -> {len(w['log_sigs'])}"
                + (f" (first diff at #{next((k for k,(x,y) in enumerate(zip(a['log_sigs'],w['log_sigs'])) if x!=y), 'len')})"
                   if a["log_sigs"] and w["log_sigs"] else ""))
        elif a.get("log_data") and a["log_data"] != w["log_data"]:
            # skip positions whose signature is declared volatile
            volatile_idx = {k for k, s in enumerate(a["log_sigs"]) if s in volatile}
            k = next((k for k, (x, y) in enumerate(zip(a["log_data"], w["log_data"]))
                      if x != y and k not in volatile_idx), None)
            if k is not None:
                problems.append(f"tx[{i}] event[{k}] payload changed (same signature, different data)")

    print(f"\n[4] {len(want) - len(problems)}/{len(want)} golden transactions reproduced")
    if problems:
        print("\nREGRESSION DETECTED against the golden recording:")
        for pr in problems[:12]:
            print(f"  - {pr}")
        sys.exit(EXIT_DRIFT)
    print("GOLDEN MATCH: the current build emits exactly what the golden recording captured.")
    sys.exit(0)


rec_path = Path(args.file)
if not rec_path.is_absolute():
    rec_path = REPO / rec_path
rec = json.loads(rec_path.read_text())
VOLATILE = set((rec.get("volatile_events") or {}).keys())

w3 = Web3(Web3.HTTPProvider(args.rpc))
local_chain = w3.eth.chain_id
if rec["chain_id"] != local_chain:
    sys.exit(f"chain mismatch: recording={rec['chain_id']} local={local_chain}")

manifest = args.manifest or rec.get("mock_manifest")
n_tx = len(rec["transactions"])
print(f"replay: {rec_path.name}" + (f"  ({rec['label']})" if rec.get("label") else ""))
print(f"recorded {rec['recorded_at']} against {rec['source_rpc']}")
print(f"{n_tx} transactions, chain_id={rec['chain_id']}, manifest={manifest or '(none)'}")
if VOLATILE:
    print("volatile events (signature asserted, payload not compared): "
          + ", ".join(sorted(rec["volatile_events"][k] for k in VOLATILE)))

# ── 1. reconstruct chain state ──────────────────────────────────────────────
overrides = (rec.get("setup") or {}).get("code_overrides") or {}
if args.deploy_mocks or not overrides:
    if not manifest:
        sys.exit("no code overrides in the recording and no manifest to deploy from")
    print(f"\n[1] deploying mock manifest: {manifest}")
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "deploy_mocks.py"),
                        "--manifest", manifest, "--quiet"],
                       capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        sys.exit("mock manifest deploy failed")
    print("    " + r.stdout.strip().splitlines()[-1])
else:
    print(f"\n[1] re-applying {len(overrides)} captured code override(s)")
    for addr, code in overrides.items():
        w3.provider.make_request("anvil_setCode", [addr, "0x" + code.removeprefix("0x")])
        live = w3.eth.get_code(Web3.to_checksum_address(addr))
        assert len(live) > 2, f"code override did not land at {addr}"
        print(f"    {addr}  {len(live)} bytes restored")

# ── 2. impersonate every sender ─────────────────────────────────────────────
senders = sorted({t["from"].lower() for t in rec["transactions"]})
print(f"\n[2] impersonating {len(senders)} sender(s)")
for s in senders:
    cs = Web3.to_checksum_address(s)
    w3.provider.make_request("anvil_setBalance", [cs, hex(1000 * 10 ** 18)])
    w3.provider.make_request("anvil_impersonateAccount", [cs])
    print(f"    {cs}  funded + impersonated")

# ── 3+4. replay with original nonces, diff status + event signatures ────────
print(f"\n[3] replaying and diffing")


failures = []
for i, t in enumerate(rec["transactions"]):
    tx = {
        "from": Web3.to_checksum_address(t["from"]),
        "nonce": t["nonce"],
        "value": int(t["value"]),
        "data": "0x" + t["input"].removeprefix("0x"),
        "gas": hex(int(t["gas"]) * 2),
    }
    if t["to"]:
        tx["to"] = Web3.to_checksum_address(t["to"])

    tgt = (t["to"] or "CREATE")[:12]
    win = rec.get("from_block", 0)
    label = (f"tx[{i:2d}] blk{win + t.get('block_offset', 0):>3} "
             f"nonce{t['nonce']:>3} ->{tgt}")
    try:
        h = w3.eth.send_transaction(tx)
        rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    except Exception as e:
        failures.append((label, f"send failed: {str(e)[:90]}"))
        print(f"    [FAIL] {label}  send failed: {str(e)[:70]}")
        continue

    got_sigs = [norm(log["topics"][0]) for log in rcpt["logs"] if log["topics"]]
    want_sigs = [s.removeprefix("0x").lower() for s in t["log_sigs"]]
    got_data = [Web3.keccak(bytes(log["data"])).hex().removeprefix("0x").lower()
                for log in rcpt["logs"] if log["topics"]]
    want_data = [h.removeprefix("0x").lower() for h in t.get("log_data", got_data)]

    ok_status = rcpt.status == t["status"]
    ok_sigs = got_sigs == want_sigs
    if VOLATILE:
        live_idx = {k for k, s in enumerate(got_sigs) if s in VOLATILE}
        ok_data = all(a == b for k, (a, b) in enumerate(zip(want_data, got_data))
                      if k not in live_idx)
    else:
        ok_data = got_data == want_data
    if ok_status and ok_sigs and ok_data:
        print(f"    [OK]   {label}  status={rcpt.status} events={len(got_sigs)}")
        continue

    detail = []
    if not ok_status:
        detail.append(f"status {t['status']}->{rcpt.status}")
    if not ok_sigs:
        detail.append(f"events {len(want_sigs)}->{len(got_sigs)}")
        for a, b in zip(want_sigs, got_sigs):
            if a != b:
                detail.append(f"sig {a[:12]}.. != {b[:12]}..")
                break
    elif not ok_data:
        for i, (a, b) in enumerate(zip(want_data, got_data)):
            if a != b and got_sigs[i] not in VOLATILE:
                detail.append(f"event[{i}] ({got_sigs[i][:10]}..) payload {a[:10]}.. != {b[:10]}..")
                break
    failures.append((label, "; ".join(detail)))
    print(f"    [FAIL] {label}  {'; '.join(detail)}")

# ── summary ─────────────────────────────────────────────────────────────────
print(f"\n[4] {n_tx - len(failures)}/{n_tx} transactions reproduced")
if failures:
    print("\nREPLAY FAILED — behaviour drifted from the recording:")
    for label, why in failures:
        print(f"  - {label}: {why}")
    sys.exit(EXIT_DRIFT)
print("REPLAY PASSED: every transaction reproduced its status and event sequence.")
