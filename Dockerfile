ARG KNUTH_VERSION=v1.3.0
# Runtime image ships kth (rpc=True), libstdc++ from GCC 15, ca-certs, and curl.
# Built offline from local/kth:v1.3.0-rpc + ubuntu until k-nuth/docker-images#7
# publishes an official ghcr.io/k-nuth/kth with RPC. Swap KTH_RUNTIME to
# ghcr.io/k-nuth/kth:${KNUTH_VERSION} (and re-add curl if missing) after that.
ARG KTH_RUNTIME=localhost:5000/knuth-startos:v1.3.0-rpc

FROM --platform=linux/amd64 ${KTH_RUNTIME}

# Identity only — binary + tools already in the runtime image.
WORKDIR /data
VOLUME /data
EXPOSE 8333 8332
ENTRYPOINT ["kth"]
