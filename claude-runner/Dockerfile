# claude-runner: Hermes-compatible /v1/runs API over the Claude Code CLI.
FROM node:22-bookworm-slim

# Pin for reproducible builds, e.g. --build-arg CLAUDE_CODE_VERSION=2.1.283
ARG CLAUDE_CODE_VERSION=latest
# Toolchains the Worker needs to build/test your project, e.g. "python3-pip make g++"
ARG EXTRA_APT_PACKAGES=""

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-venv git ca-certificates curl ripgrep procps ${EXTRA_APT_PACKAGES} \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*' \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

WORKDIR /opt/claude-runner
COPY pyproject.toml ./
COPY src ./src
# Claude Code price list for models it does not know (the local Worker model); see README.
COPY managed-settings.json /etc/claude-code/managed-settings.json
# [dev] = pytest, ruff, mypy: Workers run the checks without building their own venv.
RUN python3 -m venv /opt/claude-runner/venv \
    && /opt/claude-runner/venv/bin/pip install --no-cache-dir ".[dev]" \
    && mkdir -p /data /home/node/.claude /workspace \
    && chown -R node:node /data /home/node/.claude

ENV PATH="/opt/claude-runner/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CONFIG_DIR=/home/node/.claude \
    CLAUDE_RUNNER_DATA_DIR=/data \
    CLAUDE_RUNNER_WORKSPACE=/workspace

# Non-root: Claude Code refuses bypassPermissions (the Worker profile) as root.
USER node
# 8700: run API (stack network only); 8701: the read-only dashboard (ahawr-dashboard container)
EXPOSE 8700 8701
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8700/health', timeout=3)"
CMD ["claude-runner", "--host", "0.0.0.0", "--port", "8700"]
