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
    && /sbin/apk.static add --no-cache python3 py3-pip \
    && mv /sbin/apk.static /sbin/apk \
    && rm -rf /tmp/apk-keys /var/cache/apk/*

RUN python3 -m pip install \
    --no-cache-dir \
    --break-system-packages \
    websockets

USER node