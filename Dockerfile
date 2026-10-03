# Ritual Local Testbed — one-command environment
#
# Build:  docker build -t ritual-local-testbed .
# Run:    docker run --rm -e OPENROUTER_API_KEY=*** -e HF_TOKEN=*** \
#            ritual-local-testbed
#
# Installs Foundry + Python deps, builds contracts, starts anvil (chain 1979),
# funds the deployer, deploys the mocks, and runs the full E2E pipeline.
# All under an optional OPENROUTER_API_KEY / HF_TOKEN (pass via env or mount
# an .env file — the script loads it if present).

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    RPC_URL=http://127.0.0.1:8545 \
    FORGE_CAST=no

# Base deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git make python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Foundry (anvil + forge)
RUN curl -sL https://foundry.paradigm.xyz | bash \
    && /root/.foundry/bin/foundryup \
    && ln -sf /root/.foundry/bin/anvil /usr/local/bin/anvil \
    && ln -sf /root/.foundry/bin/forge /usr/local/bin/forge \
    && ln -sf /root/.foundry/bin/cast /usr/local/bin/cast

WORKDIR /app

# Python deps (system-wide; keep simple for a testbed image)
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --quiet -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

# Repo sources
COPY foundry.toml Makefile README.md .env.example .
COPY src/ src/
COPY scripts/ scripts/

# Build contracts now (bakes the toolchain into the image)
RUN forge build

# Entrypoint: fresh anvil -> fund -> mocks -> E2E
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8545
ENTRYPOINT ["/entrypoint.sh"]
