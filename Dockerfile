FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates libzmq5 libsecp256k1-1 libboost-all-dev && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/bitcoincash1/knuth-bch:latest /usr/local/bin/kth /usr/local/bin/

RUN mkdir -p /data
VOLUME /data
EXPOSE 8333

ENTRYPOINT ["kth"]
