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
* **Six mock contracts per manifest**, drawn from a set of eight, patched onto
  the **canonical addresses** via `anvil_setCode`, so no address in your code
  changes. Three of the eight are generic and cover every execution model the
  chain exposes — sync, short-running async, long-running async — so mocking a
  new precompile is a JSON manifest, not Solidity.
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
| Persistent-agent pipeline | DKMS → spawn → Phase-2 → on-chain state readback |
| Trading-desk pipeline | 20 printed on-chain assertions, all passing |
| Precompile zoo | 20 printed assertions, 5 precompiles across 3 execution models |
| Golden regression | 3 recordings replayed on every push |
| License | MIT |

The CI runs **three** pipelines on every push — persistent agent, trading desk,
and the precompile zoo — and then replays all three golden recordings against
the current build, so a regression anywhere fails the run. The zoo is the piece
that proves the framework is general rather than one hardcoded address: a single
manifest mocks five precompiles across all three execution models, and one case
deliberately declares a *different* Phase-2 callback selector — to prove the mock
reads the selector out of the request instead of assuming it.

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
| Three pipelines enforced in CI, Docker one-command run | Done |
| Autonomous Trading Desk example with an on-chain risk gate | Done |
| Generic mock framework — a new precompile is mocked from a JSON manifest, no Solidity | Done |
| Replay harness — record a session, diff a fresh run against it, fail on drift | Done |
| Recording a session against live mainnet | Blocked on RPC access (§7) |
| Multi-agent test scenarios (several desks, shared delivery, ordering) | Planned |

## 7. The ask

The generic mock framework and the replay harness used to be the ask. Both are
in the repo now, both run in CI, and the template an adopter starts from is
deployed on every push so it cannot silently rot.

What is still open is narrower and cannot be closed from this side: **the mocks
are this repo's reading of the precompile interfaces, not a measurement of
them.** Every payload under `mocks/payloads/` traces back to the public skills
pack. That is enough to develop against — and not enough to prove fidelity.

[`docs/INTERFACE-ASSUMPTIONS.md`](docs/INTERFACE-ASSUMPTIONS.md) enumerates
exactly where a mock could be wrong, split by who could catch it: what the
official code already checks by sitting on the other side of an interface, and
what it cannot see — four addresses it takes as arguments, three protocol
behaviours only a live chain settles, and three interfaces that agree with the
official client but have never met a deployed precompile. Each item states what
would settle it. It is written to be *answered*, not read.

So the ask is:

* **Confirm or correct that list.** Most items need a sentence, not analysis —
  the expensive question is a live network, not this. An interface owner closes
  it in well under half a day.
* **If a network is or becomes reachable, one archived response per precompile.**
  A snapshot would let the mocks be diffed against the real thing instead of
  against our reading of it, and would let the replay harness record real
  sessions rather than local ones only.

The second is worth more. The first is worth more *now* — it does not wait for a
deployment.

---

*Everything in this document is reproducible from the repo at the commit
carrying this file. No benchmark or adoption numbers are claimed, because none
would be verifiable yet — the evidence here is the pipeline, the assertions, and
the badge.*
