# Ritual Local Testbed — one-command environment
#
# Build:  docker build -t ritual-local-testbed .
# Run:    docker run --rm -e OPENROUTER_API_KEY=*** ritual-local-testbed
#
# Installs Foundry + Python deps, builds contracts, starts anvil (chain 1979),
# funds the deterministic anvil account-0, deploys the six mocks, and runs the
# full E2E pipeline. OPENROUTER_API_KEY is required (pass via env or mount an
# .env file — the entrypoint loads it if present). HF_TOKEN/HF_REPO_ID are
# optional (Data Availability provider; a dummy value works for the mock DA).

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    RPC_URL=http://127.0.0.1:8545

# Base deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git make python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Foundry — pinned release tarball (deterministic; foundryup is fragile)
ARG FOUNDRY_VERSION=1.8.4
RUN FDIR=/usr/local/foundry \
    && mkdir -p "$FDIR" \
    && curl -LsSf "https://github.com/foundry-rs/foundry/releases/download/v${FOUNDRY_VERSION}/foundry_v${FOUNDRY_VERSION}_linux_amd64.tar.gz" -o /tmp/foundry.tar.gz \
    && tar -xzf /tmp/foundry.tar.gz -C "$FDIR" \
    && chmod +x "$FDIR"/* \
    && ln -sf "$FDIR/anvil" /usr/local/bin/anvil \
    && ln -sf "$FDIR/forge" /usr/local/bin/forge \
    && ln -sf "$FDIR/cast" /usr/local/bin/cast \
    && forge --version

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --quiet -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

# Repo sources (including the deterministic anvil account-0 key)
COPY foundry.toml Makefile README.md .env.example .anvil_key ./
COPY src/ src/
COPY scripts/ scripts/

# Build contracts now (bakes the toolchain into the image)
RUN forge build

# Entrypoint: fresh anvil -> fund -> mocks -> E2E
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8545
ENTRYPOINT ["/entrypoint.sh"]
