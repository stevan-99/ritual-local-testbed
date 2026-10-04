"""Deploy mock Ritual system contracts onto a local anvil (chain 1979).

Each mock is deployed to a fresh address, then its runtime bytecode is patched
onto the canonical Ritual system/precompile addresses via anvil_setCode, so the
unmodified official consumer contract + helpers.py work against this chain:

  0x9644e8562cE0Fe12b4deeC4163c064A8862Bf47F  TEEServiceRegistry
  0x532F0dF0896F353d8C3DD8cc134e8129DA2a3948  RitualWallet
  0xC069FFCa0389f44eCA2C626e55491b0ab045AEF5  AsyncJobTracker
  0x5A16214fF555848411544b005f7Ac063742f39F6  AsyncDelivery
  0x000000000000000000000000000000000000081B  DKMS key precompile
  0x0000000000000000000000000000000000000820  Persistent Agent precompile

--agent picks which mock answers at 0x0820:
  persistent (default) -> MockPersistentAgent, returns the spawn tuple
  trading              -> MockTradingAgent, returns an AgentDecision

Requires: anvil running on http://127.0.0.1:8545 (chain-id 1979), and
  `forge build` to have produced out/Mocks.sol/*.json in this repo.
Env: PRIVATE_KEY (0x-prefixed deployer key, funded on this anvil).
"""
import argparse, json, os, sys
from pathlib import Path
from web3 import Web3

REPO = Path(__file__).resolve().parent.parent
RPC = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = 1979
OUT = REPO / "out" / "Mocks.sol"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--agent", choices=("persistent", "trading"), default="persistent",
                    help="which mock answers at 0x0820 (default: persistent)")
args = parser.parse_args()
AGENT_MOCK = "MockTradingAgent" if args.agent == "trading" else "MockPersistentAgent"

PK = os.environ["PRIVATE_KEY"]
w3 = Web3(Web3.HTTPProvider(RPC))
assert w3.eth.chain_id == CHAIN_ID, f"chain id {w3.eth.chain_id}, expected {CHAIN_ID}"
acct = w3.eth.account.from_key(PK)
nonce = w3.eth.get_transaction_count(acct.address)
print("deployer:", acct.address)


def deploy(name: str) -> str:
    global nonce
    art = json.load(open(OUT / f"{name}.json"))
    bc = art["bytecode"]["object"]  # creation bytecode (NOT deployedBytecode)
    bc = bc[2:] if bc.startswith("0x") else bc
    tx = {
        "from": acct.address, "nonce": nonce, "value": 0, "data": "0x" + bc,
        "chainId": CHAIN_ID, "gas": 2_000_000,
        "maxFeePerGas": w3.to_wei(5, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    }
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw)
    nonce += 1
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    assert rcpt.status == 1, f"{name} deploy failed"
    return rcpt["contractAddress"]


def set_code(addr: str, code_hex: str) -> None:
    w3.provider.make_request("anvil_setCode", [addr, "0x" + code_hex])


targets = {
    "MockTEERegistry": "0x9644e8562cE0Fe12b4deeC4163c064A8862Bf47F",
    "MockRitualWallet": "0x532F0dF0896F353d8C3DD8cc134e8129DA2a3948",
    "MockAsyncJobTracker": "0xC069FFCa0389f44eCA2C626e55491b0ab045AEF5",
    "MockAsyncDelivery": "0x5A16214fF555848411544b005f7Ac063742f39F6",
    "MockDKMS": "0x000000000000000000000000000000000000081B",
    AGENT_MOCK: "0x0000000000000000000000000000000000000820",
}

for name, addr in targets.items():
    dep = deploy(name)
    code = w3.eth.get_code(Web3.to_checksum_address(dep)).hex()
    code = code[2:] if code.startswith("0x") else code
    set_code(addr, code)
    live = w3.eth.get_code(Web3.to_checksum_address(addr))
    print(f"{name:22s} deploy={dep}  patched->{addr}  len={len(live)} ok={len(live) > 2}")

print(f"AGENT MOCK AT 0x0820: {AGENT_MOCK} ({args.agent})\nALL MOCKS IN PLACE")
