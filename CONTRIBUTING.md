# Contributing

## Keep these in sync

- **[`README.md`](./README.md)** — what this package is and how it is built (image, volumes, interfaces). Technical reference for developers and AI assistants.
- **[`instructions.md`](./instructions.md)** — the user-facing instructions packed into the `.s9pk` and shown on the **Instructions** tab in StartOS, for the person running the service.

**Read both before starting any work.** Any code change that affects user-visible behavior must update `README.md` and `instructions.md` in the same change. Pending work is tracked as GitHub issues, not in files. Content rules: [Writing READMEs](https://docs.start9.com/packaging/writing-readmes.html), [Writing Instructions](https://docs.start9.com/packaging/writing-instructions.html).

## Environment setup

See [Environment Setup](https://docs.start9.com/packaging/environment-setup.html)

## Building

```bash
npm ci    # install dependencies
make      # build the universal .s9pk
```

For a complete list of build options, see [Makefile](https://docs.start9.com/packaging/makefile.html).

## Updating the upstream version

1. Apply the upstream bump per [UPDATING.md](./UPDATING.md).
2. Update `version` and `releaseNotes` in `startos/versions/current.ts` — see [Versions](https://docs.start9.com/packaging/versions.html).

## CI/CD

Workflows under `.github/workflows/`:

- **`build.yml`** — builds the `.s9pk` for every pull request.
- **`tagAndRelease.yml`** — on push to `master`, tags `v<version>` and publishes the release with the built `.s9pk` files (Start9's shared workflow).
- **`syncNext.yml`** — mirrors `master` into `next`.
- **`check-upstream.yml`** — daily check for a new upstream Knuth release; opens a bump PR.

## How to contribute

1. Fork the repository and create a branch from `master`.
2. Make your changes — including the doc updates above.
3. Open a pull request to `master`.
