# Updating the upstream version

This package compiles **Knuth** (k-nuth/kth) from source in the `Dockerfile`.
Upstream releases live at [github.com/k-nuth/kth](https://github.com/k-nuth/kth/releases).

## Determining the upstream version

Check the latest tag on the [releases page](https://github.com/k-nuth/kth/releases).
The current pin is `ARG KNUTH_VERSION=` in the `Dockerfile`, written without the
leading `v` (conan wants `1.3.0`, not `v1.3.0`).

## Applying the bump

`check-upstream.yml` does this daily: it commits the bump to `master` and dispatches
Tag and Release. By hand, the steps are the same:

1. Set `ARG KNUTH_VERSION=<new version>` in the `Dockerfile`.
2. Set `version` in `startos/versions/current.ts` to `<upstream>:0` and rewrite
   `releaseNotes` for all five locales.
3. Check the upstream release notes for renamed config keys or values and update
   `startos/fileModels/knuth.conf.ts`; kth ignores unknown keys silently.
4. Update version references in `README.md` and `instructions.md`.
5. Rebuild (`make x86`) and install. kth compiles from source, so the build is long.
