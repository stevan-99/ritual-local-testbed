# Interface assumptions

Where the mocks could disagree with the real chain, and what would settle each
one.

This list is short on purpose. The 26-field request encoder, the response
envelope, the ECIES encryption and the Phase-2 poller are the **official**
artifacts vendored byte-identical (`src/PersistentAgentConsumer.sol`,
`scripts/helpers.py`). Wherever official code sits on one side of an interface,
it checks the mock: a wrong decode reverts, a wrong response fails the poller.
What is left is what official code cannot see — the live deployment, and the
parts of the protocol a client never touches.

---

## A. Assumed, and only the team can confirm

### A1. Four of the six addresses

Two addresses come out of the vendored official code, so they need no
confirmation:

| Address | Contract | Carried by |
|---|---|---|
| `0x5A16214fF555848411544b005f7Ac063742f39F6` | AsyncDelivery | `src/PersistentAgentConsumer.sol` (official) |
| `0x000000000000000000000000000000000000081B` | DKMS key precompile | `scripts/helpers.py` (official) |

The other four are supplied *to* the official code as arguments, so nothing
validates them. If any is wrong, the testbed still passes — against the wrong
target:

| Address | Assumed to be | Supplied via |
|---|---|---|
| `0x9644e8562cE0Fe12b4deeC4163c064A8862Bf47F` | TEEServiceRegistry | `--registry` argument |
| `0x532F0dF0896F353d8C3DD8cc134e8129DA2a3948` | RitualWallet | `mocks/*.json` |
| `0xC069FFCa0389f44eCA2C626e55491b0ab045AEF5` | AsyncJobTracker | `mocks/*.json` |
| `0x0000000000000000000000000000000000000820` | Persistent Agent precompile | `mocks/*.json`, `--executor` |

**Settles it:** the deployment address list for the next live network.

### A2. Phase 2 is delivered inline

`MockLongRunningGeneric` calls `IDelivery.deliver(...)` inside the same
transaction that returns the launch handle (`src/Mocks.sol:283`). On a live
chain Phase 2 presumably arrives as a **separate** transaction from the TEE, at
a later block.

Consequence: the callback path is exercised, but not the timing, the gas
accounting, or the reorg behaviour of an out-of-band delivery.

**Settles it:** how delivery reaches `AsyncDelivery` on the live chain — same
transaction, later transaction, or relayer.

### A3. The DKMS key derivation is invented

`MockDKMS.derive` is `keccak256("ritual-dkms-child", owner, keyIndex)`, and the
public key it returns is a fixed constant (`src/Mocks.sol:129-131,19`). A real
precompile derives a genuine keypair.

Consequence: the ECIES path runs against a real *shape* but nothing ever
decrypts what it produces. The flow is exercised; the keys are not.

**Settles it:** confirmation that `derive()` is a testbed invention with no
interface to match.

### A4. The launch-handle return type

Phase 1 returns either `abi.encode(bytes32 jobId)` or `abi.encode(string
taskId)` — the mock supports both because only one can be right
(`src/Mocks.sol:286-290`). The agent pipelines use `bytes32`; the zoo uses
`string` to prove the switch works, not as a claim about those precompiles.

**Settles it:** which one a real precompile returns.

---

## B. Agreed with the official client, not confirmed against the deployed precompile

These are *not* guesses — they were read out of the official encoder — but they
still rest on the client and the deployed precompile sharing one definition. A
version skew between them would break the testbed in a way it cannot detect.

| What | Where it is fixed | Evidence it matches the official client |
|---|---|---|
| Request head-word **6** = delivery target, word **7** = callback selector | `mocks/persistent.json`, `mocks/trading.json` (`layout`) | The official encoder builds the request with `consumer` at index 6 and `delivery_selector` at index 7 — `scripts/helpers.py:313-321`. The mock decodes that output on every run. |
| Callback selector is *declared in the request*, not fixed per precompile | mock calls `setSelector` with the selector it read | `delivery_selector = keccak("onPersistentAgentResult(bytes32,bytes)")[:4]` is built client-side at `helpers.py:311` |
| Short-running async response is `abi.encode(bytes simmedInput, bytes actualOutput)` | `MockShortAsyncGeneric` builds this envelope | The official consumer unwraps it and the zoo asserts the unwrapped body |

**Settles it:** the head-word layout the deployed precompiles expect. If it
matches `helpers.py`, this section is closed.

Note the zoo's `target_word=8, selector_word=9` is **not** evidence of anything
about those precompiles: there is no official client for LR-HTTP or Image in
this repo, so the zoo writes those requests itself and the mock reads what the
zoo wrote. That pair proves the layout is configuration rather than a
hardcode — nothing more.

---

## Not on this list

The client side. Request encoding, envelope unwrapping, callback
authentication, ECIES and Phase-2 polling are the official implementations, so
those interfaces are agreed with themselves. This document covers only the seam
where official code meets a mocked precompile.
