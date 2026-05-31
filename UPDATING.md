# Updating the upstream version

This package builds **Knuth** (k-nuth/kth) from source using `Dockerfile.binary`.
Upstream releases live at [github.com/k-nuth/kth](https://github.com/k-nuth/kth/releases).

## Determining the upstream version

Check the latest tag on the [releases page](https://github.com/k-nuth/kth/releases).
The current pin is `ARG KNUTH_VERSION=` in `Dockerfile.binary`.

## Applying the bump

1. Update `ARG KNUTH_VERSION=v<new version>` in `Dockerfile.binary`.
2. Add a new `startos/versions/v<X>.<Y>.<Z>.0.ts` file and update `startos/versions/index.ts` to set it as `current`.
3. Update version references in `README.md` and `instructions.md`.
4. Trigger the **Build Binary Image** workflow (`workflow_dispatch`) — Knuth compiles from source (30-60+ min) and then auto-triggers `tagAndRelease`.
