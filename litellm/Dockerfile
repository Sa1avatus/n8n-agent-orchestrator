# LiteLLM proxy for claude-runner: Anthropic Messages API (/v1/messages) in front of an
# OpenAI-compatible local model server (llama.cpp llama-server).
FROM python:3.12-slim

# Pinned: tested with Claude Code 2.1.283 → LiteLLM → OpenAI-compatible /v1/chat/completions.
ARG LITELLM_VERSION=1.102.1

ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "litellm[proxy]==${LITELLM_VERSION}" \
    && useradd --uid 10002 --create-home litellm

WORKDIR /app
# Default config and hooks; docker-compose mounts ./litellm over them so edits need no rebuild.
COPY config.yaml ahawr_hooks.py /app/

USER litellm
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness', timeout=3)"
CMD ["litellm", "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000"]
