# Ritual Local Testbed

[![CI](https://github.com/stevan-99/ritual-local-testbed/actions/workflows/ci.yml/badge.svg)](https://github.com/stevan-99/ritual-local-testbed/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![chain-id](https://img.shields.io/badge/chain-1979-blueviolet)](https://github.com/stevan-99/ritual-local-testbed)

**A local Ritual Chain (chain-id `1979`) with mock system contracts and the
official Persistent Agent pipeline running end-to-end** — no public testnet
RPC required.

The public RPC endpoint (`rpc.ritualfoundation.org`) was not reliably
reachable at the time of writing. This testbed gives you a fully working
local equivalent so you can build, debug, and demo Ritual dApps **now**:
the unmodified official consumer contract, the unmodified official request
encoders, and the official Phase-2 poller, wired against a local anvil.

## Contents

- [What's in it](#whats-in-it)
- [Quick start](#quick-start)
  - [Make](#make)
  - [Docker](#docker)
  - [Step by step](#step-by-step)
- [Expected output](#expected-output)
- [Canonical addresses](#canonical-addresses-mocked)
- [What's real vs. mocked](#whats-real-vs-mocked)
- [Env vars](#env-vars)
- [Notes / gotchas](#notes--gotchas)
- [Contributing](#contributing)
- [License](#license)

## What's in it

| Piece | What it is |
|---|---|
| `src/PersistentAgentConsumer.sol` | The **unmodified official** consumer contract from the Ritual skills pack — handles `callDKMSKey`, `callPersistentAgent`, and the `onPersistentAgentResult` callback (gated to `msg.sender == ASYNC_DELIVERY`). |
| `src/Mocks.sol` | Six mock system contracts placed (via `anvil_setCode`) on the canonical Ritual addresses. |
| `scripts/helpers.py` | The **unmodified official** request builder + Phase-2 poller from the Ritual skills pack. |
| `scripts/deploy_mocks.py` | Deploys each mock and patches its bytecode onto the canonical address. |
| `scripts/e2e.py` | Full end-to-end: deploy consumer → DKMS flow → spawn persistent agent → poll Phase-2 → verify on-chain state. |

## Quick start

### Make

```bash
# One-shot: deps, build, fresh anvil, fund, deploy mocks, run E2E.
make all
```

### Docker

```bash
# One-command environment: build image, run the full pipeline.
docker build -t ritual-local-testbed .
docker run --rm -e OPENROUTER_API_KEY=*** ritual-local-testbed
```

The entrypoint spins a fresh anvil (chain 1979), funds the deterministic
anvil account-0, deploys the six mocks, and runs the E2E driver.

### Step by step

```bash
make deps build
make anvil              # starts anvil on chain-id 1979, port 8545
make deploy-mocks       # deploys + patches the six mocks
make e2e                # runs the full pipeline
```

## Expected output

```
[1] Official PersistentAgentConsumer deployed: 0x...
[2a] Official executor discovery -> 0x0000...0001
[2b] callDKMSKey tx: 0x...
[2c] Child DKMS payment address: 0x...
[2d] Child DKMS pubkey: 0x...
[3a] Official request built: provider=openrouter model=... runtime=zeroclaw
[3b] callPersistentAgent tx: 0x...
[4a] Phase-2 delivered. jobId=0x...
[4b] official poll_phase2 ...
PHASE2_SECONDS=0.0
INSTANCE_ID=mock-instance-0001
GATEWAY_URL=http://127.0.0.1:8642/gateway
CONTAINER_ID=mock-container-7f3a
CHECKPOINT_CID=bafybeigdyrmockcheckpoint...
ERROR_MESSAGE=
GATEWAY_TOKEN=***
[5] lastJobId():   0x...
[5] lastResult():  ('mock-instance-0001', ...)

E2E PASSED: official consumer + official encoders + official Phase-2 poller
on chain 1979 (local anvil).
```

CI runs this exact pipeline on every push/PR
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Canonical addresses (mocked)

| Address | Contract |
|---|---|
| `0x9644e8562cE0Fe12b4deeC4163c064A8862Bf47F` | TEEServiceRegistry |
| `0x532F0dF0896F353d8C3DD8cc134e8129DA2a3948` | RitualWallet |
| `0xC069FFCa0389f44eCA2C626e55491b0ab045AEF5` | AsyncJobTracker |
| `0x5A16214fF555848411544b005f7Ac063742f39F6` | AsyncDelivery |
| `0x000000000000000000000000000000000000081B` | DKMS key precompile |
| `0x0000000000000000000000000000000000000820` | Persistent Agent precompile |

## What's real vs. mocked

| Real (unmodified) | Mocked |
|---|---|
| Official consumer contract, deployed | TEE executor (`0x0820`) |
| Official 26-field request encoder | DKMS precompile (`0x081B`) |
| Official ECIES secret encryption | TEEServiceRegistry / RitualWallet / AsyncJobTracker / AsyncDelivery |
| Official Phase-2 poller | Live LLM inference, live DA, live container spawn |

Everything on the **developer-controlled side** (encoding, signing, callback
auth, polling) is the genuine Ritual code. The mocks replace only the on-chain
precompiles and TEE infrastructure that require the live chain.

## Env vars

| Var | Default | Notes |
|---|---|---|
| `RPC_URL` | `http://127.0.0.1:8545` | Anvil endpoint. |
| `PRIVATE_KEY` | anvil account 0 key | Funded deployer key. |
| `OPENROUTER_API_KEY` | — | LLM provider key (required by `build-persistent-request`). |
| `HF_TOKEN` | — | Hugging Face repo token (used as the Data Availability provider in the default run). |
| `HF_REPO_ID` | — | DA repo `org/name`. |

See [`.env.example`](.env.example).

## Notes / gotchas

- **Gas:** the consumer's call into `0x0820` plus the nested
  `0x0820 → AsyncDelivery → consumer` callback needs ~490k gas. Set the
  top-level tx gas limit to ≥ 500k (the scripts use 2.5M).
- **DKMS result on anvil:** anvil receipts lack the Ritual `spcCalls`
  extension, so `e2e.py` reads the DKMS result from the consumer's
  `DkmsKeyResult(bytes)` event instead of `helpers.poll_dkms`.
- **Phase-2 jobId:** `helpers.poll_phase2` filters the
  `PersistentAgentResultDelivered` event by `jobId == txHash`. The mock
  precompile emits its own deterministic jobId (cheat-code `vm.getTxHash`
  doesn't apply to live RPC), so `e2e.py` feeds the actually-emitted jobId to
  the official poller.
- **One async commitment per sender per block:** the mock
  `AsyncJobTracker.senderPreEnabled` returns `true` and `hasPendingJobForSender`
  returns `false` to keep the testbed single-shot.
- **Python 3.14:** `coincurve` must build from source on 3.14; a 3.10–3.12
  venv is the smooth path.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: keep the official
files byte-identical to upstream, keep chain-id 1979, and **CI must stay
green** — it runs the full E2E on every push/PR.

## License

MIT. The official consumer contract and helpers are vendored unmodified from
the Ritual Foundation skills pack.
