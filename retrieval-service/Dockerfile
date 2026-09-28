FROM python:3.12-slim

# Built-in CPU models (onnxruntime, no CUDA), baked into the image so they work offline and
# without external services. Both are opt-in: RETRIEVAL_RERANKER=local, RETRIEVAL_EMBEDDER=local.
ARG RETRIEVAL_RERANKER_MODEL=jinaai/jina-reranker-v1-tiny-en
ARG RETRIEVAL_LOCAL_EMBEDDING_MODEL=intfloat/multilingual-e5-small

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RETRIEVAL_DATA_DIR=/data \
    RETRIEVAL_RERANKER_MODEL=${RETRIEVAL_RERANKER_MODEL} \
    RETRIEVAL_RERANKER_MODEL_DIR=/models

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[server,local-rerank]" \
    && useradd --uid 10001 --create-home retrieval \
    && mkdir -p /data /models \
    && python -c "import os; from fastembed.rerank.cross_encoder import TextCrossEncoder as T; T(os.environ['RETRIEVAL_RERANKER_MODEL'], cache_dir='/models', providers=['CPUExecutionProvider'], cuda=False)"     && python -c "from ahawr_retrieval.embeddings import LocalEmbedder; LocalEmbedder('${RETRIEVAL_LOCAL_EMBEDDING_MODEL}', cache_dir='/models').embed_query('warm up')" \
    && chown -R retrieval /data /models

# The model is in /models: never reach Hugging Face at run time.
ENV HF_HUB_OFFLINE=1

USER retrieval
EXPOSE 8500
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8500/health', timeout=3)"
CMD ["ahawr-retrieval", "serve", "--host", "0.0.0.0", "--port", "8500"]
