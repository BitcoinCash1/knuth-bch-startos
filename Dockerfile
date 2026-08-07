ARG KNUTH_VERSION=v1.3.0

# INTERIM: the official ghcr.io/k-nuth/kth image is built without the `rpc`
# conan option, so it has no JSON-RPC server compiled in (and its release
# workflow has failed on every tag since v1.1.0). Until k-nuth/docker-images#6
# lands, build the image locally with `rpc=True` + `tests=False`:
#   docker build -f kth/Dockerfile --build-arg KNUTH_VERSION=1.3.0 \
#     -t local/kth:v1.3.0-rpc .   # in a clone of k-nuth/docker-images
# Revert this to ghcr.io/k-nuth/kth:${KNUTH_VERSION} once that PR is released.
FROM --platform=linux/amd64 local/kth:v1.3.0-rpc AS upstream

FROM ubuntu:24.04

# Copy kth binary and its bundled GCC 15 C++ runtime from upstream GHCR image
COPY --from=upstream /usr/local/bin/kth /usr/local/bin/kth
COPY --from=upstream /opt/kth/lib/ /opt/kth/lib/
COPY --from=upstream /etc/ld.so.conf.d/kth.conf /etc/ld.so.conf.d/kth.conf

RUN ldconfig && \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /data

VOLUME /data
EXPOSE 8333
ENTRYPOINT ["kth"]
