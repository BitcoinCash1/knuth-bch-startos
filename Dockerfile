ARG KNUTH_VERSION=v1.1.0

FROM --platform=linux/amd64 ghcr.io/k-nuth/kth:${KNUTH_VERSION} AS upstream

FROM ubuntu:24.04

# Copy kth binary and its bundled GCC 15 C++ runtime from upstream GHCR image
COPY --from=upstream /usr/local/bin/kth /usr/local/bin/kth
COPY --from=upstream /opt/kth/lib/ /opt/kth/lib/
COPY --from=upstream /etc/ld.so.conf.d/kth.conf /etc/ld.so.conf.d/kth.conf

RUN ldconfig && \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /data

VOLUME /data
EXPOSE 8333
ENTRYPOINT ["kth"]
