ARG ALPINE_VERSION=3.24

FROM alpine:${ALPINE_VERSION} AS apktools

RUN apk add --no-cache apk-tools-static


FROM n8nio/n8n:2.37.4

USER root

COPY --from=apktools /sbin/apk.static /sbin/apk.static
COPY --from=apktools /etc/apk/keys /tmp/apk-keys

RUN RUNTIME_ALPINE_VERSION=$(. /etc/os-release && printf '%s' "$VERSION_ID" | cut -d. -f1,2) \
    && mkdir -p /etc/apk /etc/apk/keys \
    && cp -n /tmp/apk-keys/* /etc/apk/keys/ || true \
    && printf 'https://dl-cdn.alpinelinux.org/alpine/v%s/main\nhttps://dl-cdn.alpinelinux.org/alpine/v%s/community\n' \
       "$RUNTIME_ALPINE_VERSION" "$RUNTIME_ALPINE_VERSION" > /etc/apk/repositories \
    && /sbin/apk.static add --no-cache python3 py3-pip py3-numpy \
    && mv /sbin/apk.static /sbin/apk \
    && rm -rf /tmp/apk-keys /var/cache/apk/*

RUN python3 -m pip install \
    --no-cache-dir \
    --break-system-packages \
    websockets

# AHAWR Context Retrieval Layer, embedded: executed by n8n Execute Command nodes
# (python3 -m ahawr_retrieval.cli exec ...). No extra container or daemon.
COPY retrieval-service/pyproject.toml /tmp/ahawr-retrieval/pyproject.toml
COPY retrieval-service/src /tmp/ahawr-retrieval/src
RUN python3 -m pip install \
    --no-cache-dir \
    --break-system-packages \
    /tmp/ahawr-retrieval \
    && rm -rf /tmp/ahawr-retrieval \
    && python3 -c "import ahawr_retrieval, numpy; print('ahawr-retrieval', ahawr_retrieval.__version__)"

USER node