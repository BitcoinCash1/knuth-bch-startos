ARCHES := x86 arm riscv
# One build target for all three arches. Every s9pk packs the same x86_64
# image (kth's toolchain image is linux/amd64 only; StartOS emulates it on the
# other arches), so CI builds them on one x86 runner, the way the release job
# does. A target named arm goes to an arm64 runner, which compiles kth under
# emulation and ran past the six-hour job limit.
TARGETS := arches
# overrides to s9pk.mk must precede the include statement
include node_modules/@start9labs/start-sdk/s9pk.mk
