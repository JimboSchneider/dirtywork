# Node 22 worker image (`node:22-bookworm-slim`): Implementation Plan

> **This repository's execution rule** (repo `CLAUDE.md`, "Dogfood rule"): the code task below
> (`W1`) is built by the **released dirtywork 0.12.1** running against this repository with a local
> worker (`qwen/qwen3-coder-next` via LM Studio, image `dirtywork-worker-pytest:0.12`). Claude
> writes the brief, reviews the branch, builds the image on the host, and writes the ledger. Owner
> approval is needed for the merge and the release, never assumed.

**Plan v1** (2026-08-29 13:57 CDT). Owner's ask (13:51, after a Codex-driven dirtywork run on
invoicr hit it): move the worker image off Debian bookworm's Node 18 — base on
`node:22-bookworm-slim`, drop Debian's `nodejs`/`npm`, add an image smoke assertion for
`node --version`, rebuild and publish `:0.13` before the 0.13.0 release. Design approved in chat
13:55 ("lets go", Node 22).

**Goal:** the worker sandbox can run a current Vite/Vitest project's tests; the image's Node
version is asserted on every CI build.

**Facts verified 2026-08-29:** `node:22-bookworm-slim` (pulled locally) is Debian 12.15 with Node
v22.23.2, npm 10.9.8 and corepack in `/usr/local/bin` (on `docker_args.PATH_ENV`), and ships a
`node` user at **uid/gid 1000** — the uid `useradd -u 1000 -m worker` needs, so that user is
removed first. Node 18 end-of-life 2025-04-30, Node 20 2026-04-30, Node 22 maintenance LTS to
2027-04-30, Node 24 active LTS to 2028-04-30 (nodejs/Release `schedule.json`). Vite 8: "requires
Node.js 20.19+, 22.12+" (vite.dev/blog/announcing-vite8). Vitest: 22.12+ (owner's source). Nothing
in `dirtywork/`, `tools/` or `tests/` calls Node by an absolute path; the bench's node fixture runs
`node` via `/bin/sh` on PATH. `tests/test_docker_image.py` pins the Dockerfile's apt list as text
and is the one unit test that changes. `publish-image.yml` builds only on a release tag, so this
must merge **before** `v0.13.0` is cut; `PINNED_DIGEST` is already `None` for 0.13.0 (#100).

## Global Constraints

- Only these files change: `docker/Dockerfile`, `tools/ci_sandbox_smoke.py`,
  `tests/test_docker_image.py`, `docker/README.md`, `docs/operating.md`. `SKILL.md` untouched (no
  hash bump); `DEFAULT_IMAGE`/`PINNED_DIGEST` untouched.
- `USER worker` stays uid 1000; no `ENTRYPOINT`/`CMD`/`WORKDIR` (Dockerfile comments explain why).
- The worker cannot run Docker; its verify is the unit suite. The image is proven by CI
  `docker-live` (build + `tools/ci_sandbox_smoke.py` + live suite) and by a local arm64 build.

## Execution model

Same as `docs/superpowers/plans/2026-08-29-issue-87-init-agent.md` ("Execution model"): `run87.sh`
with `--branch-from node22-worker-image`, brief at `$SCRATCH/brief-node22-w1.md`, review, at most
two resumes, export commit → rebase → `git merge --ff-only` → `runs clean --force
--keep-transcript`, one ledger row in `## #87`'s section (this ships in the same release).

---

### Task W1: Base the image on `node:22-bookworm-slim` (worker)

**Files:** `docker/Dockerfile:2` (FROM), `:4-21` (apt list), `:63` (`useradd`);
`tools/ci_sandbox_smoke.py:109` (after `START OK`); `tests/test_docker_image.py:31-42`;
`docker/README.md:3-4`, `:42-47` (Verify locally), `:35`; `docs/operating.md:19`.

- [ ] **Step 1: Pre-check on the host** — apply items 1–3 in a throwaway worktree, run
  `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider tests/test_docker_image.py`
  (the `docker`-marked test is deselected), discard.
- [ ] **Step 2: Brief** `$SCRATCH/brief-node22-w1.md`:

```
Node 22 worker image. Debian bookworm's Node is 18 (end-of-life 2025-04-30); Vite 8 needs Node 20.19+/22.12+ and current Vitest needs 22.12+, so the sandbox could not run a real Vite project's tests. Base the image on the official Node 22 image instead. Touch only docker/Dockerfile, tools/ci_sandbox_smoke.py, tests/test_docker_image.py, docker/README.md and docs/operating.md. You cannot run docker in the sandbox; your verify is the unit suite, and CI builds the image.

1. docker/Dockerfile:
   a. Line 2 `FROM debian:bookworm-slim` becomes `FROM node:22-bookworm-slim`, and directly after it insert this comment block verbatim:
# node:22-bookworm-slim is Debian 12 (bookworm-slim) plus Node 22 LTS, npm and
# corepack from the official image, installed under /usr/local/bin -- on the
# PATH dirtywork passes to every exec. Debian's own Node package is 18,
# end-of-life 2025-04-30 (nodejs/Release schedule.json); Vite 8 requires Node
# 20.19+ / 22.12+ and current Vitest 22.12+, so a worker on the old image could
# not run a real Vite project's tests. 22 is maintenance LTS until 2027-04-30.
   b. Delete the two apt-list lines `        nodejs \` and `        npm \` (every other package stays).
   c. Replace the line `RUN useradd -u 1000 -m worker` with these four lines:
# The official image ships a `node` user at uid/gid 1000. Remove it so
# `useradd -u 1000 -m worker` keeps giving `worker` the uid this image has
# always had; nothing in the sandbox refers to the `node` user.
RUN userdel -r node && useradd -u 1000 -m worker
   Nothing else in the Dockerfile changes.

2. tools/ci_sandbox_smoke.py: directly after the line `            print("START OK")` insert, with the same 12-space indentation:
            node_version = sb.bash("node --version").strip()
            print("node --version:", node_version)
            assert node_version.startswith("v22."), f"expected Node 22 in the image, got {node_version!r}"

3. tests/test_docker_image.py, in test_dockerfile_installs_the_packages_the_docs_promise: remove "nodejs" and "npm" from the package tuple. Directly after that for-loop add:
    assert "\nFROM node:22-bookworm-slim\n" in text, "the base image must be the official Node 22 image"
    assert "\nRUN userdel -r node && useradd -u 1000 -m worker\n" in text
    for package in ("nodejs", "npm"):
        assert f"\n        {package} \\\n" not in text, f"{package} comes from the base image, not apt"
   and change the readme loop's tuple to ("jq", "uuid-runtime", "shellcheck", "curl", "node:22-bookworm-slim").

4. docker/README.md: replace `Built from `docker/Dockerfile`: Debian bookworm-slim, `USER worker` (uid` with `Built from `docker/Dockerfile`: `node:22-bookworm-slim` (Debian 12 plus Node 22 LTS, npm and corepack from the official image -- Debian's own Node is 18, end-of-life), `USER worker` (uid`. Under `## Verify locally`, add this as a fifth line after the `--list-sdks` line (same 4-space indent):
    docker run --rm --entrypoint /usr/local/bin/node ghcr.io/jimboschneider/dirtywork-worker:0.13 --version
   and directly after the paragraph that follows that list add a new paragraph: `node --version` must print `v22.x` — the check CI's sandbox smoke (`tools/ci_sandbox_smoke.py`) makes on every image build. Replace `the node fixture needs `nodejs`, which this image installs)` with `the node fixture needs `node`, which the base image provides)`.

5. docs/operating.md: in the line containing `python3, node/npm, the .NET SDKs`, change `node/npm` to `node/npm (Node 22)`.

Checks before finishing: `grep -c '^FROM node:22-bookworm-slim$' docker/Dockerfile` prints 1; `grep -c '^        nodejs \\\|^        npm \\' docker/Dockerfile` prints 0; `grep -c 'userdel -r node && useradd -u 1000 -m worker' docker/Dockerfile` prints 1; `grep -c 'node_version.startswith("v22.")' tools/ci_sandbox_smoke.py` prints 1. Verify: python3 -m pytest -q -p no:cacheprovider tests/test_docker_image.py, then the full suite.
```

- [ ] **Step 3: Run** `DW_REL=0.12.1 DW_IMG=dirtywork-worker-pytest:0.12 $SCRATCH/run87.sh $SCRATCH/brief-node22-w1.md --branch-from node22-worker-image` — `run87.sh` hard-codes `--branch-from issue-87-init-agent`; the trailing argument overrides it (argparse last-wins) — **check `run.json`'s `base_commit` is this branch's HEAD**.
- [ ] **Step 4: Review.** Diff = exactly five files; `git diff docker/Dockerfile` shows only the FROM line + comment, the two removed apt lines, and the useradd block; host `tests/test_docker_image.py` green; full suite green. **Local build:** `docker build -t dirtywork-worker-node22:test docker/` (arm64), then the five README verify lines against it plus `id worker` (`uid=1000(worker)`), `docker run --rm --entrypoint /usr/local/bin/npm … --version`, and `docker run --rm dirtywork-worker-node22:test id node` must **fail** (user gone); record the image size next to `:0.12`'s (591 MB arm64 in the Dockerfile comment's history).
- [ ] **Step 5: Chain, ledger, PR.** Export commit, ff `node22-worker-image`, clean; ledger row under `## #87` (plus the owner's Codex-on-invoicr receipt from 13:51); PR titled "worker image: node:22-bookworm-slim — Node 18 is EOL and can't run Vite 8/Vitest; smoke asserts node --version (before 0.13.0)"; CI `docker-live` is the gate that matters. Owner merges; then `v0.13.0`.

## Self-review

Spec coverage: the owner's four asks — base image (1a), remove Debian's node/npm (1b), smoke assertion (2 + README), rebuild/publish before release (step 5 sequencing + `publish-image.yml` fact) — plus the uid-1000 collision (1c) and the test that pins the apt list (3). Placeholders: none; every edit has its literal text. Types: `sb.bash(...) -> str` (`docker.py:1188`), so `.strip()`/`.startswith` are valid.
