FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RETRIEVAL_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]" \
    && useradd --uid 10001 --create-home retrieval \
    && mkdir -p /data \
    && chown retrieval /data

USER retrieval
EXPOSE 8500
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8500/health', timeout=3)"
CMD ["ahawr-retrieval", "serve", "--host", "0.0.0.0", "--port", "8500"]
