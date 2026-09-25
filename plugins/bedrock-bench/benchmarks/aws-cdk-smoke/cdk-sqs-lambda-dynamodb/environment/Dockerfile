FROM node:22.19.0-bookworm-slim@sha256:4a4884e8a44826194dff92ba316264f392056cbe243dcc9fd3551e71cea02b90

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl git python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV CDK_DISABLE_CLI_TELEMETRY=1
ENV AWS_EC2_METADATA_DISABLED=true
COPY project/package.json project/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY project/ ./
