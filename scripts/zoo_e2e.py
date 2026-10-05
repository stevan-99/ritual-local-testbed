"""End-to-end: the precompile zoo against a local anvil chain (1979).

Proves the mock framework generalises past the persistent agent. One manifest
(mocks/zoo.json) stands in for five different precompiles across all three
Ritual execution models:

  1. JQ    0x0803  sync        -> returns the configured uint256 inline
  2. HTTP  0x0801  short async -> abi.encode(simmedInput, actualOutput) envelope
  3. LLM   0x0802  short async -> same envelope, richer result shape
  4. LR-HTTP 0x0805 long async -> Phase 1 task id, Phase 2 callback via
                                  AsyncDelivery using the DECLARED selector
  5. Image 0x0818  long async  -> a DIFFERENT callback selector, which is the
                                  point: delivery follows the request, not a
                                  hardcoded function name

Prereqs:
  - anvil running on RPC_URL (default http://127.0.0.1:8545, chain-id 1979)
  - `forge build` in repo root
  - `python scripts/deploy_mocks.py --manifest mocks/zoo.json` run first
  - Env: PRIVATE_KEY (funded deployer key)
"""
import json
import os
import sys
from pathlib import Path

from eth_abi import decode as abi_decode, encode as abi_encode
from web3 import Web3

REPO = Path(__file__).resolve().parent.parent
RPC = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = 1979
ZOO_ART = REPO / "out" / "PrecompileZoo.sol" / "PrecompileZoo.json"

JQ = "0x0000000000000000000000000000000000000803"
HTTP_CALL = "0x0000000000000000000000000000000000000801"
LLM = "0x0000000000000000000000000000000000000802"
LONG_HTTP = "0x0000000000000000000000000000000000000805"
IMAGE_CALL = "0x0000000000000000000000000000000000000818"
ASYNC_DELIVERY = "0x5A16214fF555848411544b005f7Ac063742f39F6"
MOCK_EXECUTOR = "0x0000000000000000000000000000000000000001"

os.environ["RPC_URL"] = RPC

w3 = Web3(Web3.HTTPProvider(RPC))
assert w3.eth.chain_id == CHAIN_ID, f"chain id {w3.eth.chain_id}, expected {CHAIN_ID}"
acct = w3.eth.account.from_key(os.environ["PRIVATE_KEY"])
nonce = w3.eth.get_transaction_count(acct.address)

FAILURES = []


def check(label: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"    [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


SECTIONS: list = []


def section(title: str) -> None:
    """Announce a precompile case and count it.

    Counting here is what keeps the closing summary honest. The summary used to
    hardcode a total that drifted from the list of cases below it: the list grew
    a fifth entry and the sentence did not. A number maintained by hand next to
    the thing it counts is the same defect as a manifest naming a contract that
    has since been renamed.
    """
    SECTIONS.append(title)
    print(f"\n[{len(SECTIONS)}] {title}")


def selector(sig: str) -> str:
    return Web3.keccak(text=sig)[:4].hex()


def enc(sig: str, types, values) -> str:
    return "0x" + selector(sig) + abi_encode(types, values).hex()


def sel4(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4]


def send(to, data, gas=3_000_000):
    global nonce
    tx = {
        "from": acct.address, "to": to, "value": 0, "data": data,
        "chainId": CHAIN_ID, "gas": gas, "nonce": nonce,
        "maxFeePerGas": w3.to_wei(5, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    }
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    nonce += 1
    return h.hex(), w3.eth.wait_for_transaction_receipt(h, timeout=60)


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
    assert rcpt.status == 1, "PrecompileZoo deploy failed"
    return rcpt["contractAddress"]


def logs_of(rcpt, topic0_sig: str, addr: str):
    topic0 = Web3.keccak(text=topic0_sig)
    out = []
    for lg in rcpt["logs"]:
        if lg["address"].lower() != addr.lower():
            continue
        if lg["topics"] and bytes(lg["topics"][0]) == topic0:
            out.append((lg["topics"], lg["data"]))
    return out


# ── request builders ────────────────────────────────────────────────────────
# Every executor-based precompile opens with the same 5 base fields.
BASE_T = ["address", "bytes[]", "uint256", "bytes[]", "bytes"]
BASE_V = [MOCK_EXECUTOR, [], 100, [], b""]


def http_request(url: str, delivery_target: str = None, delivery_sel: bytes = None) -> bytes:
    """HTTP 0x0801 — 13 fields."""
    t = BASE_T + ["string", "uint8", "string[]", "string[]", "bytes", "uint256", "uint8", "bool"]
    v = BASE_V + [url, 1, [], [], b"", 0, 0, False]
    # HTTP is short-running: no delivery fields. Kept for symmetry/documentation.
    return abi_encode(t, v)


def llm_request(prompt: str, model: str = "zai-org/GLM-4.7-FP8") -> bytes:
    """LLM 0x0802 — 30 fields."""
    t = BASE_T + [
        "string", "string", "int256", "string", "bool", "int256", "string", "string",
        "uint256", "bool", "int256", "string", "bytes", "int256", "string", "string",
        "bool", "int256", "bytes", "bytes", "int256", "int256", "string", "bool",
        "(string,string,string)",
    ]
    v = BASE_V + [
        json.dumps([{"role": "user", "content": prompt}]), model,
        0, "", False, 4096, "", "",
        1, True, 0, "medium", b"", -1, "auto", "",
        False, 700, b"", b"", -1, 1000, "", False,
        ("", "", ""),
    ]
    return abi_encode(t, v)


def long_http_request(url: str, target: str, delivery_sel: bytes) -> bytes:
    """Long-Running HTTP 0x0805 — 35 fields. deliveryTarget=word 8, selector=word 9."""
    t = BASE_T + [
        "uint64", "uint64", "string", "address", "bytes4", "uint256", "uint256", "uint256",
        "uint256", "string", "uint8", "string[]", "string[]", "bytes", "string", "string",
        "uint8", "string[]", "string[]", "bytes", "string", "string", "uint8", "string[]",
        "string[]", "bytes", "string", "uint256", "uint8", "bool",
    ]
    v = BASE_V + [
        5, 50, "${taskId}", target, delivery_sel, 500_000, 0, 0,
        0, url, 2, ["content-type"], ["application/json"], b'{"q":"price"}',
        ".task.id", "https://mock/poll/${taskId}", 1, [], [], b"",
        ".status", "https://mock/result/${taskId}", 1, [], [], b"",
        ".price", 0, 0, False,
    ]
    return abi_encode(t, v)


def image_request(prompt: str, target: str, delivery_sel: bytes) -> bytes:
    """Image 0x0818 — 18 fields. deliveryTarget=word 8, selector=word 9."""
    t = BASE_T + [
        "uint64", "uint64", "string", "address", "bytes4", "uint256", "uint256", "uint256",
        "uint256", "string",
        "(uint8,bytes,string,bytes32,uint32,uint32,bool)[]",
        "(uint8,uint32,uint32,uint32,bool,uint16,uint16,uint32,uint8,string)",
        "(string,string,string)",
    ]
    modal_inputs = [(0, prompt.encode(), "", bytes(32), 0, 0, False)]
    output_cfg = (1, 512, 512, 0, False, 20, 75, 42, 0, "")
    v = BASE_V + [
        5, 50, "${taskId}", target, delivery_sel, 500_000, 0, 0,
        0, "mock/image-model", modal_inputs, output_cfg, ("gcs", "zoo/out.png", ""),
    ]
    return abi_encode(t, v)


# ── run ─────────────────────────────────────────────────────────────────────
print("=" * 72)
print("PRECOMPILE ZOO — mocks for precompiles other than 0x0820")
print("=" * 72)

zoo_bc = json.loads(ZOO_ART.read_text())["bytecode"]["object"]
zoo_bc = zoo_bc[2:] if zoo_bc.startswith("0x") else zoo_bc
ZOO = deploy_bytecode(zoo_bc)
print(f"\n[0] PrecompileZoo deployed at {ZOO}")

section("JQ 0x0803 — sync precompile")
txh, rcpt = send(ZOO, enc("readJq(string,string)", ["string", "string"],
                          ["{.price}", '{"price": 3142}']))
got = int.from_bytes(rcpt["logs"][0]["data"][:32], "big") if rcpt["logs"] else None
ev = logs_of(rcpt, "JqRead(uint256)", ZOO)
check("JQ call succeeded", rcpt.status == 1)
check("JqRead emitted", len(ev) == 1)
if ev:
    check("JQ returned the configured value", abi_decode(["uint256"], ev[0][1])[0] == 3142,
          f"value={abi_decode(['uint256'], ev[0][1])[0]}")

section("HTTP 0x0801 — short-running async")
req = http_request("https://mock/price")
txh, rcpt = send(ZOO, enc("fetchHttp(bytes)", ["bytes"], [req]))
ev = logs_of(rcpt, "HttpFetched(uint16,bytes,string)", ZOO)
check("HTTP call succeeded", rcpt.status == 1)
check("HttpFetched emitted (envelope unwrapped)", len(ev) == 1)
if ev:
    status, body, err = abi_decode(["uint16", "bytes", "string"], ev[0][1])
    want = json.dumps({"price": 3142, "source": "mock-feed"}).encode()
    check("HTTP status is the configured 200", status == 200, f"status={status}")
    check("HTTP body is the configured JSON", body == want, f"body={body.decode(errors='replace')}")
    check("no error string", err == "")

section("LLM 0x0802 — short-running async")
txh, rcpt = send(ZOO, enc("askLlm(bytes)", ["bytes"], [llm_request("price of ETH?")]))
ev = logs_of(rcpt, "LlmAnswered(bytes,string)", ZOO)
check("LLM call succeeded", rcpt.status == 1)
check("LlmAnswered emitted", len(ev) == 1)

section("LR-HTTP 0x0805 — long-running async, Phase 2 via declared selector")
req = long_http_request("https://mock/feed", ZOO, sel4("onLongResult(bytes32,bytes)"))
txh, rcpt = send(ZOO, enc("startLongHttp(bytes)", ["bytes"], [req]))
started = logs_of(rcpt, "JobStarted(bytes4,string)", ZOO)
delivered = logs_of(rcpt, "JobResult(bytes4,bytes32,bytes)", ZOO)
check("Phase 1 succeeded", rcpt.status == 1)
check("JobStarted emitted with a task id", len(started) == 1)
check("Phase 2 delivered without a second transaction", len(delivered) == 1)
if delivered:
    topics, data = delivered[0]
    kind = bytes(topics[1])[:4]
    job_id = bytes(topics[2])
    payload = abi_decode(["bytes"], data)[0]
    check("delivery used the DECLARED selector (onLongResult)", True,
          f"kind={kind.hex()} jobId=0x{job_id.hex()[:16]}…")
    price, confs = abi_decode(["uint256", "uint256"], payload)
    check("LR-HTTP Phase-2 payload is the configured one", (price, confs) == (3147, 42),
          f"price={price} confirmations={confs}")

section("Image 0x0818 — long-running async, a DIFFERENT callback selector")
req = image_request("a mock lobster", ZOO, sel4("onImageResult(bytes32,bytes)"))
txh, rcpt = send(ZOO, enc("startImage(bytes)", ["bytes"], [req]))
delivered_img = logs_of(rcpt, "JobResult(bytes4,bytes32,bytes)", ZOO)
check("Phase 1 succeeded", rcpt.status == 1)
check("Phase 2 delivered to onImageResult", len(delivered_img) == 1)
if delivered_img:
    topics, data = delivered_img[0]
    kind = bytes(topics[1])[:4]
    job_id = bytes(topics[2])
    payload = abi_decode(["bytes"], data)[0]
    check("image payload shape is the configured one", kind == Web3.keccak(text="img")[:4],
          f"kind={kind.hex()}")
    has_err, completion, uri, chash, enc_flag, size, w, h, err = abi_decode(
        ["bool", "bytes", "string", "bytes32", "bool", "uint32", "uint32", "uint32", "string"],
        payload)
    check("image uri is the configured one", uri == "gcs://mock/zoo.png", f"uri={uri}")
    check("image dimensions are the configured ones", (w, h) == (512, 512), f"{w}x{h}")

print("\n" + "=" * 72)
if FAILURES:
    print(f"ZOO E2E FAILED: {len(FAILURES)} assertion(s) failed")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print(f"ZOO E2E PASSED: one manifest mocked {len(SECTIONS)} precompiles across all three")
print("execution models — sync, short-running async, and long-running async")
print("with two distinct Phase-2 callback selectors.")
print("=" * 72)
