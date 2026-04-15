# ── Build Knuth from source ──────────────────────────────────────────
FROM ubuntu:24.04 AS build

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates \
    gcc-14 g++-14 \
    python3 python3-pip python3-venv pkg-config \
    libboost-all-dev libzmq3-dev libsecp256k1-dev \
    curl && \
    update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 100 \
      --slave /usr/bin/g++ g++ /usr/bin/g++-14 \
      --slave /usr/bin/gcov gcov /usr/bin/gcov-14 && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --break-system-packages "conan>=2,<3" "kthbuild>=4,<5"

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
      -o console=True \
      -o db=dynamic && \
    cmake --preset conan-release && \
    cmake --build --preset conan-release --parallel "$(nproc)" && \
    cmake --install build/build/Release --prefix=/usr/local

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
