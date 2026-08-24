# dirtywork worker image (ghcr.io/jimboschneider/dirtywork-worker)

Built from `docker/Dockerfile`: Debian bookworm-slim, `USER worker` (uid
1000), git, bash, coreutils, findutils, python3, node, .NET SDK, ripgrep,
and (since 0.8) jq, uuid-runtime, shellcheck and curl. This Dockerfile
installs .NET SDK **8.0 and 10.0**, both via Microsoft's official
`dotnet-install.sh` into the same `/usr/share/dotnet` (the apt feed lacks
arm64 packages for Debian 12). 8.0 stays alongside 10.0 because the sandbox
runs `--network none`: an SDK-10-only image can't build `net8.0` projects
offline, since SDK 10 doesn't bundle the 8.0 targeting packs and a restore
that can't reach NuGet fails with `NETSDK1145`. The **published**
`ghcr.io/jimboschneider/dirtywork-worker:0.10` image predates this change
and has SDK 8.0 only; 10.0 arrives with the 1.0 image (#59), which also
re-pins `PINNED_DIGEST`.
No `ENTRYPOINT`/`CMD` — every `docker create`/`run`/`exec` in dirtywork
passes its own explicit `--entrypoint` or absolute binary path.

The same image is also used by `dirtywork bench` for the post-run
acceptance containers (hash check via `/usr/bin/sha256sum`, the acceptance
command via `/bin/sh`; the node fixture needs `nodejs`, which this image
installs) and by `dirtywork runs export` for re-exports.

## Build

    docker build -t ghcr.io/jimboschneider/dirtywork-worker:0.10 docker/

## Verify locally

    docker run --rm --entrypoint /usr/bin/git ghcr.io/jimboschneider/dirtywork-worker:0.10 --version
    docker run --rm --entrypoint /usr/bin/rg ghcr.io/jimboschneider/dirtywork-worker:0.10 --version
    docker run --rm --entrypoint /usr/bin/python3 ghcr.io/jimboschneider/dirtywork-worker:0.10 --version
    docker run --rm --entrypoint /usr/bin/dotnet ghcr.io/jimboschneider/dirtywork-worker:0.10 --version

## Publishing (automated)

`.github/workflows/publish-image.yml` builds and pushes this image to
GitHub Container Registry (`ghcr.io/jimboschneider/dirtywork-worker`) on
each **new minor** release (`vX.Y.0`; same `release: published` trigger as
`publish.yml`, gated on the tag). Patch releases do not rebuild the image:
the `:X.Y` tag is what `DEFAULT_IMAGE` points at and `PINNED_DIGEST` pins,
and re-pushing it on every patch would let base-layer drift change the
digest under a shipped pin. To publish a Dockerfile fix inside a minor, run
the workflow by hand (`workflow_dispatch`, input: the release tag) and then
re-pin `PINNED_DIGEST` in a follow-up. It builds both `linux/amd64` and
`linux/arm64`, tags the push with both the release's minor version
(`v0.4.0` → `:0.4`) and its full version (`:0.4.0`), and writes the pushed
manifest digest to the workflow run's job summary. The one manual step it
cannot do itself: after the very first push, the owner must mark the
`ghcr.io/jimboschneider/dirtywork-worker` package public in its GitHub
Packages settings, or `docker pull` fails for anyone without repo access.

The image tag tracks the dirtywork minor version, so `:0.4` is a moving
tag across the 0.4.x patch series — `PINNED_DIGEST` (below) is what
actually pins a specific build for the *default* image; `--image` can
always be pointed at a full `name@sha256:...` reference directly.

## Pin a digest (PINNED_DIGEST)

`dirtywork/sandbox/docker_args.py`'s `PINNED_DIGEST` (unset on the first
release of each minor — 0.4.0, 0.5.0 — because that release is what first
publishes the `:X.Y` image; pinned from the following patch onward) is a
REGISTRY PROVENANCE guarantee for the default image: it only ever applies
when `--image` is left at its default (`--image` is the operator's own
choice and is never pinned — see below), and only checks digests fetched
from a registry. Once set, `resolve_image()` refuses to run a *pulled*
default image whose digest doesn't match, regardless of what the mutable
`:0.4` tag currently points to. The pin check compares `PINNED_DIGEST`
against the image's *registry* digest (`RepoDigests`, via
`image_repo_digest()`); the container itself always executes the image's
local content-addressed Id, never that registry digest, so a run can never
trigger a network pull.

A *locally built or loaded* default image (no `RepoDigests` entry — it was
never pushed to or pulled from a registry, e.g. `docker build -t
ghcr.io/jimboschneider/dirtywork-worker:0.10 docker/` run by hand, or the CI
gate that builds this same image locally) has nothing for the pin to
compare against. `resolve_image()` does not refuse it: it returns the
local Id and prints a one-line warning to stderr instead
(`image_pinned` in `run.json`/`run_start` is `false` for this case, even
though `PINNED_DIGEST` is set — the pin was not enforced). A `--image
<ref>` the operator supplies explicitly is never checked against
`PINNED_DIGEST` at all, pinned or not — that pin protects the *maintained
default image only*.

The first release of a minor (0.4.0, 0.5.0, 0.6.0, 0.7.0, 0.8.0, 0.9.0, 0.10.0) ships with `PINNED_DIGEST = None`: there is no prior publish to pin
against on the very first release, so `resolve_image()` performs no pin
check and trusts whatever `docker image inspect` currently reports for
`ghcr.io/jimboschneider/dirtywork-worker:0.10`. The next patch release
(0.4.1 for 0.4; 0.5.1 for 0.5; 0.6.1 for 0.6; 0.8.1 for 0.8; 0.9.1 for 0.9; 0.10.1 for 0.10 — 0.7.x shipped unpinned) pins — once `publish-image.yml` has run, take the
digest from its job summary (or resolve it yourself below) and commit it
as `PINNED_DIGEST` ahead of the next release.

1. Resolve the published digest:

       docker pull ghcr.io/jimboschneider/dirtywork-worker:0.10
       docker image inspect --format '{{json .RepoDigests}}' ghcr.io/jimboschneider/dirtywork-worker:0.10

   This prints a JSON array like
   `["ghcr.io/jimboschneider/dirtywork-worker@sha256:<64 hex chars>"]`.

2. Set `PINNED_DIGEST` in `dirtywork/sandbox/docker_args.py` to the
   `sha256:<...>` portion only (not the whole `name@sha256:...` string).

3. Commit the `PINNED_DIGEST` change as part of the release.

## Manual build and push (fallback)

`publish-image.yml` is the normal path; a manual push is only needed to
recover from a broken automated run:

    docker build -t ghcr.io/jimboschneider/dirtywork-worker:0.10 docker/
    docker push ghcr.io/jimboschneider/dirtywork-worker:0.10

then resolve/pin the digest as above.

## Derived images (extra packages)

The worker cannot install anything at run time: docker mode runs with
`--network none` and mounts no host directories, so `apt-get`/`npm i -g`
inside a run will always fail. If your gate needs a tool this image does not
ship, build a derived image once and point `--image` at it:

```Dockerfile
FROM ghcr.io/jimboschneider/dirtywork-worker:0.10
USER root
RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
    && rm -rf /var/lib/apt/lists/*
USER worker
```

    docker build -t my-worker:0.10 .
    dirtywork run --repo ~/repos/thing --image my-worker:0.10 "..."

Keep `USER worker` as the last instruction and add no `ENTRYPOINT`/`CMD`:
dirtywork always passes its own `--entrypoint` and `--user` explicitly at
`docker create` (`dirtywork/sandbox/docker.py`'s `DockerSandbox.start()`
passes `os.getuid()`/`os.getgid()` as `--user uid:gid` — the **host's** uid,
not the image's `worker` uid 1000 — and the run volume is chowned to that
host uid), and every later `docker exec` inherits that user, so `USER
worker` here is defence in depth for anyone who runs the image by hand, not
something dirtywork itself relies on. A custom `--image` is **never** checked against
`PINNED_DIGEST`; that pin protects the maintained default image only, so a
derived image's provenance is yours to manage.

### Baking a pre-restored package cache

Because the worker runs as the host uid, not the image's `worker` (uid
1000), anything a derived image bakes in for the worker to read or write at
run time — a restored package cache, a tool's state dir — must be
world-readable/writable (`chmod -R a+rwX`); skip this and a run fails with
something like `Failed to read NuGet.Config due to unauthorized access`
(seen in the #48 soak). dirtywork also sets `HOME=/home/worker` (with
`TMPDIR=/tmp` and `PATH`) with `-e` at `docker create`, and every `docker
exec` starts from that container environment — so it overrides any `ENV
HOME` this Dockerfile bakes in, and a cache directory chosen via `ENV
HOME=...` here is not what the worker sees at run time. And because the
rootfs is `--read-only` at run time, a baked cache must be complete at
build time (run the restore as root — `/opt` is root-owned — then open the
result up to the host uid):

```Dockerfile
FROM ghcr.io/jimboschneider/dirtywork-worker:0.10
USER root
ENV NUGET_PACKAGES=/opt/nuget
COPY MyProject.csproj /tmp/restore/MyProject.csproj
RUN dotnet restore /tmp/restore/MyProject.csproj --packages /opt/nuget \
    && rm -rf /tmp/restore \
    && chmod -R a+rwX /opt/nuget
USER worker
```

    docker build -t my-worker:0.10 .
    dirtywork run --repo ~/repos/thing --image my-worker:0.10 "..."

A baked `ENV NUGET_PACKAGES` (or `npm_config_cache`, `PIP_CACHE_DIR`, …)
survives into the running container — the `-e` flags dirtywork passes at
`docker create` only cover `HOME`, `TMPDIR`, `PATH` and the `GIT_*`/`LANG`
variables, not arbitrary image `ENV` values — so a worker `bash` call needs
no per-command redirect once the cache is baked in this way.

A live restore instead of a baked one needs `--allow-network` (docker
mode's `--network none` default blocks NuGet/npm/pip) plus a `--home-size`
large enough for it — see `docs/machine-contract.md`'s `--tmp-size` /
`--gitdir-size` / `--home-size` bullet for the tmpfs caps, the default
package-cache locations under `$HOME`, and why `HOME` can't be redirected
once for a whole run (only per command).

## Updating the image

Rebuild (normally by cutting a release, which drives `publish-image.yml`),
verify with the commands above, then repeat the pin procedure. `--image`
lets an operator override the default per run; `PINNED_DIGEST` only
constrains the maintained default.
