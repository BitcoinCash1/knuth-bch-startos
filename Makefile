# Same set as Flowee / Fulcrum / the pools. The kth toolchain image is amd64
# only; aarch64 and riscv64 s9pks ship that image via emulateMissingAs x86_64.
ARCHES ?= x86 arm riscv
# overrides to s9pk.mk must precede the include statement
include s9pk.mk
