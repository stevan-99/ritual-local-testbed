# Ritual Local Chain Testbed

**A working local Ritual Chain (chain-id `1979`) so developers can build and
test Persistent Agent dApps while the public RPC is unreachable — with the
official contracts and encoders running unmodified.**

Repo: `github.com/stevan-99/ritual-local-testbed` · MIT · CI green

---

## 1. The problem

Ritual's value proposition is developer-facing: spawn a persistent agent, give it
tools, let it act. But you cannot test any of that when the chain endpoint does
not answer. Probed on the date of writing:

| Endpoint | Result |
|---|---|
| `https://rpc.ritualfoundation.org` | connection failed (HTTP 000) — DNS resolves to `162.255.119.231`, a parking IP |
| `https://rpc.ritual.net` | HTTP 404 — Vercel `DEPLOYMENT_NOT_FOUND` |
| `https://ritual-rpc.publicnode.com` | HTTP 404 |

The failure mode is the worst kind for a young ecosystem: the docs, the contract
addresses, and the skills pack are all public, so a developer gets **far enough**
to write a real integration — and then hits a wall at the first RPC call. There
is no way to distinguish "my contract is wrong" from "the chain is down".

## 2. The solution

A local chain that behaves enough like Ritual to develop against:

* **anvil, chain-id `1979`** — the real chain id, so anything that validates it
  keeps working.
* **Six mock system contracts** patched onto the **canonical addresses** via
  `anvil_setCode`, so no address in your code changes.
* **The official artifacts, unmodified** — the consumer contract, the 26-field
  request encoder, ECIES secret encryption, and the Phase-2 poller are vendored
  byte-identical to the skills pack. `CONTRIBUTING.md` makes that a hard rule.
* **Three ways to run it** — `make all`, `docker run`, or GitHub Actions.

```
make all         # deps + build + fresh anvil + fund + mocks + E2E
docker run ...   # same pipeline, one command, no host setup
```

## 3. Proof, not promises

Everything below is reproducible from a clean checkout:

| Evidence | Value |
|---|---|
| CI badge | `passing` (public, no auth needed) |
| Docker image | builds in 12 steps; `docker run` → `E2E PASSED` |
| Persistent-agent pipeline | DKMS → spawn → Phase-2 → on-chain state verified |
| Trading-desk pipeline | 15 on-chain assertions, all passing |
| License | MIT |

The CI runs **both** pipelines on every push — the persistent-agent flow and the
trading-desk flow — so a regression in either is caught automatically.

## 4. The example dApp: why the testbed is more than plumbing

Plumbing alone proves nothing. So the repo ships one worked example that shows
what the chain is *for*: an **Autonomous Trading Desk**.

An agent proposes a trade. A **real on-chain risk gate** decides whether that
proposal may become an intent:

```
agent (TEE) --decision--> 0x0820 --> AsyncDelivery --> desk callback
                                                          |
                                       riskGate(): notional cap / leverage /
                                       confidence floor / direction / pair
                                                          |
                                        accept -> TradeIntent recorded
                                        reject -> clamp to HOLD + reason
```

Two design choices that matter in production:

* **Clamp, don't revert.** A rejected proposal still records an intent with the
  action forced to `HOLD` and a machine-readable reason. The desk keeps an audit
  trail of what it refused and why — reverting would destroy that evidence.
* **Contain malformed output.** A bad agent payload emits `DecodeFailed` and
  returns instead of reverting the delivery, so one hallucinating agent cannot
  brick the desk or stall the delivery path.

The E2E proves all of it on-chain: unauthorized callers are rejected; an
oversized long is refused as `notional above cap` and clamped to `HOLD`; a
compliant long is accepted; a short on a long-only desk is refused; and a
malformed payload from the *real* AsyncDelivery address is contained without
recording an intent.

This is the pattern any Ritual agent needs: **trust the TEE for judgement, never
for limits.** Limits belong in Solidity.

## 5. Why this matters to Ritual

* **Removes the cold-start wall.** A developer can clone, run one command, and
  see the full persistent-agent lifecycle succeed before they have ever spoken
  to the chain.
* **Makes the docs testable.** The official encoders and poller are exercised on
  every commit, so encoder drift is caught by a third party rather than by users.
* **A demo surface that survives an outage.** You can present the Persistent
  Agent story at a hackathon or a partner call with no dependency on the public
  endpoint.
* **A template for ecosystem projects.** Fork it, swap the mock payload, and you
  have a test harness for your own agent dApp.

## 6. Roadmap

| Milestone | Status |
|---|---|
| Local chain + canonical mocks + official pipeline E2E | Done |
| Both pipelines enforced in CI, Docker one-command run | Done |
| Autonomous Trading Desk example with an on-chain risk gate | Done |
| Generic mock framework — mocks currently live in Solidity; other projects need to define their own agent payloads | Next |
| Replay harness: record a live mainnet session, replay it locally for regression | Next |
| Multi-agent test scenarios (several desks, shared delivery, ordering) | Planned |

## 7. The ask

Support to finish the generic mock framework and the replay harness — the two
pieces that turn a working demo into infrastructure other teams can build on
without copying this repo's Solidity.

Concretely: an RPC snapshot or a documented way to obtain one, plus review from
whoever owns the precompile interfaces, would let the mocks track the real
precompiles instead of tracking this repo's reading of them.

---

*Everything in this document is reproducible from the repo at the commit
carrying this file. No benchmark or adoption numbers are claimed, because none
would be verifiable yet — the evidence here is the pipeline, the assertions, and
the badge.*
