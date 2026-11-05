# ── Build ───────────────────────────────────────────────────────────
# Build the released node with the recipe merged in k-nuth/docker-images#7
# (54a66910d7acda68766899a75cbb0304d617cb0a, kth/Dockerfile). The official
# GHCR image now includes RPC, but currently rejects pulls from this package.
# Keep the upstream toolchain and Conan options here until that image is
# accessible. Release checks prefer the official image and otherwise validate
# this source build. No node patches are applied to the released binary. The
# tempnet-only exception is built separately below. The one difference from that
# recipe is the Conan upgrade below, without which it cannot build kth 1.5.0.
#
# Cross-compiled with pure Go-style static linkage is not applicable; kth is C++,
# so this stage uses the project's own toolchain image to match the compile
# profile published on packages.kth.cash.
ARG TOOLCHAIN=kthnode/gcc15-ubuntu24.04@sha256:4988fdeb3654c2b5ea591ffa4454f67e1a631549449c62e35060ba69da79eb67

# The official gcc15 toolchain image is linux/amd64 only. Packing aarch64 and
# riscv64 s9pks reuses this image; StartOS runs it under qemu (emulateMissing).
FROM --platform=linux/amd64 ${TOOLCHAIN} AS kth-build

# kth release version WITHOUT the leading "v" (conan wants 1.3.0, not v1.3.0).
ARG KNUTH_VERSION=1.5.2
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

# ── Tempnet build ───────────────────────────────────────────────────
# Released kth 1.5.2 cannot run Tempnet. This commit contains k-nuth/kth#752
# with the fast-sync crash fix from #815 applied on top, merged onto kth master
# so it also carries master's consensus fixes (#763 to #768). Keep it in a separate
# binary so every other network still runs the released node. Remove this stage
# and kth-tempnet once a kth release includes both #752 and #815.
FROM --platform=linux/amd64 ${TOOLCHAIN} AS kth-tempnet-build

ARG TEMPNET_COMMIT=c67aa441b98085b4001b397842b622afe877d7aa
ARG CURRENCY=BCH

RUN pip install --no-cache-dir --upgrade "conan>=2.25,<3" "kthbuild>=4,<5"

WORKDIR /src/kth
RUN git clone --branch tempnet/start9 --single-branch https://github.com/CyberAshven/kth.git . \
 && git checkout --detach ${TEMPNET_COMMIT}

RUN conan profile detect --force \
 && conan remote add kth https://packages.kth.cash/api/ --force \
 && conan install conanfile.py --version 1.5.2 -of build --build=missing \
      -o "kth/*:currency=${CURRENCY}" \
      -o "kth/*:rpc=True" \
      -o "kth/*:tests=False" \
      -o "utxoz/*:with_tests=False" \
      -o "utxoz/*:with_benchmarks=False" \
      -o "utxoz/*:with_large_benchmarks=False" \
      -s compiler.cppstd=23 \
 && cmake --preset conan-release -DGLOBAL_BUILD=ON -DCMAKE_BUILD_TYPE=Release \
 && cmake --build --preset conan-release --target node-exe \
 && find build -type f -path '*/node-exe/*' -name kth -executable \
      -exec install -Dm755 {} /out/kth-tempnet \; \
 && test -x /out/kth-tempnet

# ── Job Declaration build (TEST ONLY) ───────────────────────────────
# TEST ONLY, Job Declaration client (k-nuth/kth#832, #834) on chipnet against the BCH P2Pool pool.
FROM --platform=linux/amd64 ${TOOLCHAIN} AS kth-jd-build

ARG JD_COMMIT=a2c400bd
ARG CURRENCY=BCH

RUN pip install --no-cache-dir --upgrade "conan>=2.25,<3" "kthbuild>=4,<5"

WORKDIR /src/kth
RUN git clone --branch test/sv2-jd-payout-outputs-announce --single-branch https://github.com/CyberAshven/kth.git . \
 && git checkout --detach ${JD_COMMIT}

RUN conan profile detect --force \
 && conan remote add kth https://packages.kth.cash/api/ --force \
 && conan install conanfile.py --version 1.5.2 -of build --build=missing \
      -o "kth/*:currency=${CURRENCY}" \
      -o "kth/*:rpc=True" \
      -o "kth/*:tests=False" \
      -o "utxoz/*:with_tests=False" \
      -o "utxoz/*:with_benchmarks=False" \
      -o "utxoz/*:with_large_benchmarks=False" \
      -s compiler.cppstd=23 \
 && cmake --preset conan-release -DGLOBAL_BUILD=ON -DCMAKE_BUILD_TYPE=Release \
 && cmake --build --preset conan-release --target node-exe \
 && find build -type f -path '*/node-exe/*' -name kth -executable \
      -exec install -Dm755 {} /out/kth-jd \; \
 && test -x /out/kth-jd

# ── Runtime ─────────────────────────────────────────────────────────
FROM --platform=linux/amd64 ubuntu:24.04

# curl is used by the package health checks to reach the JSON-RPC interface;
# kth ships no RPC client CLI of its own.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /data

# The binary is compiled with GCC 15; ship its matching C++ runtime so the image
# does not depend on the base distro's older libstdc++ / libgcc ABI.
# TEST ONLY: this package runs kth-jd on chipnet, so it compiles kth once.
# The released node and the tempnet binary are kth-jd here, which leaves the
# kth-build and kth-tempnet-build stages unused and skipped.
COPY --from=kth-jd-build /usr/local/lib64/libstdc++.so.6* /opt/kth/lib/
COPY --from=kth-jd-build /usr/local/lib64/libgcc_s.so.1*  /opt/kth/lib/
RUN echo /opt/kth/lib > /etc/ld.so.conf.d/kth.conf && ldconfig

COPY --from=kth-jd-build /out/kth-jd /usr/local/bin/kth
COPY --from=kth-jd-build /out/kth-jd /usr/local/bin/kth-tempnet
COPY --from=kth-jd-build /out/kth-jd /usr/local/bin/kth-jd

WORKDIR /data
VOLUME /data
# P2P and JSON-RPC (mainnet defaults; per-network ports are set in kth.cfg).
EXPOSE 8333 8332
ENTRYPOINT ["kth"]
