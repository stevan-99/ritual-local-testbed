# Contributing

Ritual Local Testbed welcomes PRs. The bar is simple: **the CI pipeline must
stay green.** That pipeline (`.github/workflows/ci.yml`) runs the full E2E on
a local anvil chain 1979 — the same one you can run locally with `make all`
or `docker run`.

## Local workflow

```bash
git clone https://github.com/stevan-99/ritual-local-testbed
cd ritual-local-testbed
make all          # deps + build + anvil + fund + deploy mocks + E2E
```

## Rules

1. **Never modify the official files.** `src/PersistentAgentConsumer.sol`,
   `scripts/helpers.py` are vendored from the Ritual Foundation skills pack
   and must stay byte-identical to upstream. If upstream changes, vendor the
   new version whole and document the diff.
2. **Keep the chain-id 1979.** The canonical mock addresses and precompile
   map are defined for chain 1979; don't re-target.
3. **Gas:** any new nested call through `0x0820 → AsyncDelivery → consumer`
   needs ≥ 500k tx-level gas (scripts use 2.5M).
4. **No secrets in commits.** `PRIVATE_KEY` in `.env.example` / `.anvil_key`
   is anvil's *published* account-0 key (`0xac09…ff80`), the one anvil prints
   on startup. It is not a secret and holds nothing on any real network — it
   is funded only inside the local chain. If you change it, use another
   anvil-default account; anything else is a secret and must not be
   committed. (This file previously described a key that was not anvil's
   default, which broke this rule.)
5. **CI is the acceptance test.** If your change touches pipeline logic,
   add the expected new behavior to `scripts/e2e.py` assertions so CI
   verifies it.

## What good contributions look like

- A missing mock for a new system contract (mirror `Mocks.sol`, add the
  canonical address, extend `deploy_mocks.py` + the address table in README).
- A new E2E scenario (e.g. a second Phase-2 poll, a failure-path check).
- Docs fixes, Docker/CI hardening, portable toolchain fixes.

## Repo layout

```
src/     Solidity: official consumer + mocks
scripts/ Python: official helpers + deploy + e2e driver
Makefile one-shot local run
Dockerfile / entrypoint.sh one-shot container run
.github/workflows/ci.yml CI (anvil + E2E on every push/PR)
```
