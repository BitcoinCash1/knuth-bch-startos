# AGENTS.md

This is a StartOS service-package repository — it builds a `.s9pk` for StartOS.

Develop it inside a StartOS packaging workspace created by `start-cli s9pk init-workspace`,
which provides the packaging guide and agent context one level up. If you're reading this in a
bare clone with no workspace, the full guide is at <https://docs.start9.com/packaging>.

**Start every task at the recipe index** — `../start-technologies/projects/start-sdk/docs/src/recipes.md`
(or <https://docs.start9.com/packaging/recipes.html>). It maps an intent ("prompt the user to create
admin credentials", "expose a web UI") to the constructs, the reference pages, and a named production
package to copy. Find the recipe before you read this package's neighbours: a package you reach by
grepping may be non-conformant, and the recipe outranks it.

Freshly scaffolded? Work the
[New Package Checklist](../start-technologies/projects/start-sdk/docs/src/new-package-checklist.md)
(or <https://docs.start9.com/packaging/new-package-checklist.html>) from top to bottom. It is a
guide page, not a file in this repo — read it, don't copy it in.

Keep `README.md` (technical reference for an AI support or administering agent) and
`instructions.md` (end-user docs) in sync with your changes.

**Bugs and feature requests are GitHub issues on this repo** — file them as you find them.
Don't record work in the repo instead: no `TODO.md`, no `NOTES.md`, no `PLAN.md`. What you
verified, tried, and decided belongs in the commit message and the PR body.

## This repo

- **kth is compiled from source in the `Dockerfile`, with `rpc=True`.** The official `ghcr.io/k-nuth/kth` image is built without the JSON-RPC server, so it cannot be used as a base. The build takes a long time on a hosted runner; that is expected.
- **The toolchain image is `linux/amd64` only.** The aarch64 and riscv64 s9pks carry the same amd64 image and StartOS runs it under emulation (`emulateMissingAs: 'x86_64'`). That is why `ARCHES` keeps `arm` and `riscv`; removing them drops those platforms.
- **kth 1.3.0 renamed its config keys and `db_mode` values** (`network.*` → `net.*`, `database.*` → `db.*`, `blockchain.*` → `chain.*`; `db_mode` is `full|blocks|pruned`). kth ignores unknown keys instead of rejecting them, so a wrong key silently falls back to kth's default, while a wrong `db_mode` value crash-loops the node. Check every key against the upstream release when touching `startos/fileModels/knuth.conf.ts`.
- **`scripts/rpc_compat.py` sits on the public RPC port; kth itself listens on `127.0.0.1:19332`.** The sidecar fills the Bitcoin-RPC calls kth 1.3.0 cannot answer (`getblock`, `getrawtransaction`, classic `getblocktemplate`, `submitblock`, …) so Fulcrum, BCH Explorer and the pools can use Knuth. Its responses end with a newline because ckpool reads the body line by line.
- **Each network keeps its own data directory and hosts file under `/data`.** kth writes its hosts file to the working directory unless told otherwise, which is outside the volume.
- **`peerInterfaceId` and `rpcInterfaceId` in `startos/utils.ts` are what dependents connect to.** Keep them stable.

## Repository conventions

This repo is the original the Start9-Community copy is imported from. Keep it a
near-replica of that copy: every difference must be one of those listed below.
- **Syncing with Start9-Community:** `git merge` their `master` into ours, never
  rebase or force-push. Take their side for packaging, layout, docs and CI;
  keep only the deliberate differences below.
- **Branches:** `master` is released — every push to it runs Tag and Release,
  and so does the upstream bot's dispatch after an auto-bump.
  `next` is kept on purpose: Start9's Sync Next workflow mirrors `master` into
  it, so do not delete it.
- **Versions:** `<upstream>:<revision>` in the single `startos/versions/current.ts`.
  Never change the upstream part by hand; a new upstream starts at `:0` (the
  auto-bump does this). The revision is bumped only when the maintainer
  decides — never for alignment, template, docs, CI or archive changes. `ALLOW_DOWNGRADE` stays `false` unless a
  release is known to be reversible.
- **`assets/` vs `archive/`:** `assets/` is packed into the s9pk as a whole, so
  it holds only `.gitkeep` unless the service reads a file at runtime.
  `archive/` holds reference material (`ABOUT.md`, logos, picture variants) and
  is not packed. Never delete anything in `archive/`.
- **What StartOS shows:** name from `title` in `startos/manifest/index.ts`,
  description and About text from `description.short`/`description.long` in
  the same file, Instructions tab from `instructions.md` (required), logo from
  `icon.svg`.
- **Commit and PR hygiene:** no session links, `Co-Authored-By` trailers or
  "Generated with" footers in commit messages, PR descriptions or comments.
  The Session Link Guard workflow fails any PR or push that carries one.
  Commits are authored by the maintainer, and all repository text (code
  comments, docs, commit messages, PR text) is written in the maintainer's
  voice, without naming the tools used to produce it.
- **Toolchain:** always follow the latest Start9 tooling — the newest
  `@start9labs/start-sdk` on npm (pinned exactly, with the `overrides` entry),
  the newest `start-cli` release, and the latest `Start9Labs/hello-world-startos`
  template. Its boilerplate files (workflows, `Makefile`, `tsconfig.json`,
  `.gitignore`, `.dockerignore`, `CLAUDE.md`, `startos/index.ts`,
  `startos/sdk.ts`, `startos/i18n/index.ts`, `startos/versions/index.ts`) stay
  byte-identical to it unless a difference is listed below. When the template,
  SDK or CLI moves, update every package. Where the template and the
  Start9-Community copy disagree, the template wins.
- **Deliberate differences from Start9-Community:** the template layout itself (Start9-Community's copy still has the older one); `ALLOW_DOWNGRADE` in `current.ts`; the older `startos/versions/v*.ts` files kept in `versions/index.ts` so every upgrade path runs the same migrations (`1.3.0:1` carries settings over the kth 1.3.0 key renames, `1.3.0:2` moves each network to its own data directory); `ARCHES := x86 arm riscv` in the `Makefile`; the maintainer's own automation, kept as it is: `check-upstream.yml` + `scripts/auto-bump.sh` (daily check of the latest `k-nuth/kth` release; bumps `current.ts` and the `Dockerfile` straight on `master`, then dispatches Tag and Release) and the package's own `tagAndRelease.yml` (builds kth from source with a 180-minute timeout and publishes the GitHub release); `dependabot.yml`; `session-link-guard.yml`; `archive/`; the matching README note.
