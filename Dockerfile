# ── Build Knuth from source ──────────────────────────────────────────
FROM ubuntu:22.04 AS build

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates \
    python3 python3-pip pkg-config \
    libboost-all-dev libzmq3-dev libsecp256k1-dev \
    curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir "conan>=2,<3" "kthbuild>=4,<5"

# Clone Knuth
ARG KNUTH_VERSION=v0.79.0
WORKDIR /build
RUN git clone --depth 1 --branch ${KNUTH_VERSION} --recurse-submodules \
    https://github.com/k-nuth/kth.git

WORKDIR /build/kth
RUN conan profile detect --force && \
    conan remote add kth https://packages.kth.cash/api/ && \
    conan install . --build=missing -of=build \
      -s compiler.cppstd=23 \
      -o currency=BCH \
      -o db=dynamic && \
    cmake --preset conan-release && \
    cmake --build --preset conan-release --parallel "$(nproc)" && \
    cmake --install build/Release --prefix=/usr/local

# ── Runtime ─────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates libzmq5 libsecp256k1-1 libboost-all-dev && \
    rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/local/bin/kth-node /usr/local/bin/

RUN mkdir -p /data
VOLUME /data
EXPOSE 8332 8333 18332

ENTRYPOINT ["kth-node"]
