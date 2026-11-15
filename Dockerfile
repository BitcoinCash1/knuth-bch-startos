# ── Build ───────────────────────────────────────────────────────────
# Build the released node with the upstream recipe, k-nuth/docker-images
# kth/Dockerfile at 8a75ca809d2cb8b25d13c8f58a8b8ea0fc8a5b56 (#10: GCC 16.2,
# which kth requires since 1.6.0; #13: rpc=jsonrpc, the RPC backend option kth
# has taken since 1.6.0 in place of rpc=True). The official
# GHCR image now includes RPC, but currently rejects pulls from this package.
# Keep the upstream toolchain and Conan options here until that image is
# accessible. Release checks prefer the official image and otherwise validate
# this source build. No node patches are applied to the released binary. The one difference from that
# recipe is the Conan upgrade below, without which it cannot build kth 1.5.0.
#
# Cross-compiled with pure Go-style static linkage is not applicable; kth is C++,
# so this stage uses the project's own toolchain image to match the compile
# profile published on packages.kth.cash.
# kth refuses GCC older than 16.2 (k-nuth/kth#865): the GCC 16.2.0 toolchain image.
ARG TOOLCHAIN=kthnode/gcc16-ubuntu24.04@sha256:cde0d8a8b911ba290e4019fa4fac81723c7cae93cd864e509fd93a7c05d25dfe

# The official toolchain images are linux/amd64 only. Packing aarch64 and
# riscv64 s9pks reuses this image; StartOS runs it under qemu (emulateMissing).
FROM --platform=linux/amd64 ${TOOLCHAIN} AS kth-build

# kth release version WITHOUT the leading "v" (conan wants 1.3.0, not v1.3.0).
ARG KNUTH_VERSION=1.7.0
ARG CURRENCY=BCH

# kthbuild is required to load the kth conan recipe. Conan is upgraded the same
# way kth's own CI does (ci_utils setup-conan: newest Conan 2): the pinned
# toolchain ships Conan 2.24.0, and kth 1.5.0 needs openssl/4.1.0, whose
# ConanCenter recipe requires Conan >= 2.25.
RUN pip install --no-cache-dir --upgrade "conan>=2.25,<3" "kthbuild>=4,<5"

# `console=True` is deliberately NOT passed: it builds the per-module demo
# programs, and src/domain/console/main.cpp does not compile in v1.3.0. The node
# binary comes from src/node-exe under BUILD_NODE_EXE, which defaults ON.
# `tests=False` keeps the VMB test suite out of a source build.
# `rpc=jsonrpc` compiles in the JSON-RPC server (kth's default backend). The two
# boost options are the upstream recipe's, from its move to GCC 16.2.
RUN conan profile detect --force \
 && conan remote add kth https://packages.kth.cash/api/ --force \
 && conan install --requires=kth/${KNUTH_VERSION} \
      --deployer=direct_deploy --output-folder=/deploy --build=missing \
      -o "kth/*:currency=${CURRENCY}" \
      -o "kth/*:rpc=jsonrpc" \
      -o "kth/*:tests=False" \
      -o "utxoz/*:with_tests=False" \
      -o "utxoz/*:with_benchmarks=False" \
      -o "utxoz/*:with_large_benchmarks=False" \
      -o "boost/*:without_cobalt=True" \
      -o "boost/*:without_stacktrace_backtrace=True" \
      -s compiler.cppstd=23

# ── Runtime ─────────────────────────────────────────────────────────
FROM --platform=linux/amd64 ubuntu:24.04

# curl is used by the package health checks to reach the JSON-RPC interface;
# kth ships no RPC client CLI of its own.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /data

# The node is compiled with GCC 16.2; ship its C++ runtime so the image does not
# depend on the base distro's older libstdc++ / libgcc ABI.
COPY --from=kth-build /usr/local/lib64/libstdc++.so.6* /opt/kth/lib/
COPY --from=kth-build /usr/local/lib64/libgcc_s.so.1*  /opt/kth/lib/
RUN echo /opt/kth/lib > /etc/ld.so.conf.d/kth.conf && ldconfig

COPY --from=kth-build /deploy/direct_deploy/kth/bin/kth /usr/local/bin/kth

WORKDIR /data
VOLUME /data
# P2P and JSON-RPC (mainnet defaults; per-network ports are set in kth.cfg).
EXPOSE 8333 8332
ENTRYPOINT ["kth"]
