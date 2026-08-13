# ── Build ───────────────────────────────────────────────────────────
# Self-contained: kth is compiled from upstream source here rather than pulled
# from a prebuilt image. The official ghcr.io/k-nuth/kth is built without the
# `rpc` conan option (no JSON-RPC server), and its release workflow has failed
# on every tag since v1.1.0 — see k-nuth/docker-images#7. Building here also
# satisfies the Start9 registry requirement that packages build from source
# rather than pull external prebuilt binaries.
#
# Cross-compiled with pure Go-style static linkage is not applicable; kth is C++,
# so this stage uses the project's own toolchain image to match the compile
# profile published on packages.kth.cash.
ARG TOOLCHAIN=kthnode/gcc15-ubuntu24.04@sha256:4988fdeb3654c2b5ea591ffa4454f67e1a631549449c62e35060ba69da79eb67

FROM ${TOOLCHAIN} AS kth-build

# kth release version WITHOUT the leading "v" (conan wants 1.3.0, not v1.3.0).
ARG KNUTH_VERSION=1.3.0
ARG CURRENCY=BCH

# kthbuild is required to load the kth conan recipe.
RUN pip install --no-cache-dir --upgrade "kthbuild>=4,<5"

# `console=True` is deliberately NOT passed: it builds the per-module demo
# programs, and src/domain/console/main.cpp does not compile in v1.3.0. The node
# binary comes from src/node-exe under BUILD_NODE_EXE, which defaults ON.
# `tests=False` keeps the VMB test suite out of a source build.
# `rpc=True` compiles in the JSON-RPC server added in v1.3.0.
RUN conan profile detect --force \
 && conan remote add kth https://packages.kth.cash/api/ --force \
 && conan install --requires=kth/${KNUTH_VERSION} \
      --deployer=direct_deploy --output-folder=/deploy --build=missing \
      -o "kth/*:currency=${CURRENCY}" \
      -o "kth/*:rpc=True" \
      -o "kth/*:tests=False" \
      -o "utxoz/*:with_tests=False" \
      -o "utxoz/*:with_benchmarks=False" \
      -o "utxoz/*:with_large_benchmarks=False" \
      -s compiler.cppstd=23

# ── Runtime ─────────────────────────────────────────────────────────
FROM ubuntu:24.04

# curl is used by the package health checks to reach the JSON-RPC interface;
# kth ships no RPC client CLI of its own.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl python3 && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /data

# The binary is compiled with GCC 15; ship its matching C++ runtime so the image
# does not depend on the base distro's older libstdc++ / libgcc ABI.
COPY --from=kth-build /usr/local/lib64/libstdc++.so.6* /opt/kth/lib/
COPY --from=kth-build /usr/local/lib64/libgcc_s.so.1*  /opt/kth/lib/
RUN echo /opt/kth/lib > /etc/ld.so.conf.d/kth.conf && ldconfig

COPY --from=kth-build /deploy/direct_deploy/kth/bin/kth /usr/local/bin/kth
COPY scripts/rpc_compat.py /usr/local/bin/rpc_compat.py
RUN chmod 755 /usr/local/bin/rpc_compat.py

WORKDIR /data
VOLUME /data
# P2P and JSON-RPC (mainnet defaults; per-network ports are set in kth.cfg).
EXPOSE 8333 8332
ENTRYPOINT ["kth"]
