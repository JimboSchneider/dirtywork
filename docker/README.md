# dirtywork worker image (ghcr.io/jimboschneider/dirtywork-worker)

Built from `docker/Dockerfile`: Debian bookworm-slim, `USER worker` (uid
1000), git, bash, coreutils, findutils, python3, node, .NET SDK, ripgrep,
and (since 0.8) jq, uuid-runtime, shellcheck and curl. This Dockerfile
installs .NET SDK **8.0 and 10.0**, both via Microsoft's official
`dotnet-install.sh` into the same `/usr/share/dotnet` (the apt feed lacks
arm64 packages for Debian 12). 8.0 stays alongside 10.0 because the sandbox
runs `--network none`: an SDK-10-only image can't build `net8.0` projects
offline, since SDK 10 doesn't bundle the 8.0 targeting packs and a restore
that can't reach NuGet fails with `NETSDK1145`. The
`ghcr.io/jimboschneider/dirtywork-worker:0.11` image predated this change
and had SDK 8.0 only; 10.0 arrived with the `:0.11` image (0.11.0, the
image #59 asked for): that release bumped `DEFAULT_IMAGE` to `:0.11` and
shipped `PINNED_DIGEST = None` (first publish of the minor); the pin lands
in 0.11.1 — see "Pin a digest" below.
Unless a repo's `global.json` pins otherwise, `dotnet` resolves to the
highest installed SDK (10.0.x here), so a repo that previously built on
8.0.424 in the old `:0.10` image sees a default-SDK change: newer
analyzers/audit warnings, and `net10.0` as the `dotnet new` default. The
Dockerfile also sets
`DOTNET_EnableWriteXorExecute=0`: dirtywork's `bash` tool runs every command
under `ulimit -f 524288` (256 MiB), and with W^X enabled the .NET 8 runtime
trips that limit at startup (even at 8 GiB — consistent with the size of its
double-mapping file), so on the old `:0.10` image every .NET 8 process
— `dotnet build`, `dotnet test`, a built app — died with `File size limit
exceeded` (exit 153); the .NET 10 runtime does not. Verified 2026-08-24; the
variable fixes both and `:0.11` bakes it, so a derived image `FROM :0.11`
does not need to repeat it.
No `ENTRYPOINT`/`CMD` — every `docker create`/`run`/`exec` in dirtywork
passes its own explicit `--entrypoint` or absolute binary path.

The same image is also used by `dirtywork bench` for the post-run
acceptance containers (hash check via `/usr/bin/sha256sum`, the acceptance
command via `/bin/sh`; the node fixture needs `nodejs`, which this image
installs) and by `dirtywork runs export` for re-exports.

## Build

    docker build -t ghcr.io/jimboschneider/dirtywork-worker:0.11 docker/

## Verify locally

    docker run --rm --entrypoint /usr/bin/git ghcr.io/jimboschneider/dirtywork-worker:0.11 --version
    docker run --rm --entrypoint /usr/bin/rg ghcr.io/jimboschneider/dirtywork-worker:0.11 --version
    docker run --rm --entrypoint /usr/bin/python3 ghcr.io/jimboschneider/dirtywork-worker:0.11 --version
    docker run --rm --entrypoint /usr/bin/dotnet ghcr.io/jimboschneider/dirtywork-worker:0.11 --list-sdks

`dotnet --version` prints only the highest-resolved SDK (`10.0.400`); use
`--list-sdks` instead and check that both an `8.0.x` and a `10.0.x` line
appear — that's what confirms the coexisting-SDK build actually shipped,
not just that *a* .NET SDK is present.

## Publishing (automated)

`.github/workflows/publish-image.yml` builds and pushes this image to
GitHub Container Registry (`ghcr.io/jimboschneider/dirtywork-worker`) on
each **new minor** release (`vX.Y.0`; same `release: published` trigger as
`publish.yml`, gated on the tag). Patch releases do not rebuild the image:
the `:X.Y` tag is what `DEFAULT_IMAGE` points at and `PINNED_DIGEST` pins,
and re-pushing it on every patch would let base-layer drift change the
digest under a shipped pin. To publish a Dockerfile fix inside a minor, run
the workflow by hand (`workflow_dispatch`, input: the release tag) and then
re-pin `PINNED_DIGEST` in a follow-up.

**Do not dispatch the current Dockerfile onto a `0.10.x` tag.** It is now
intentionally *ahead* of the old `:0.10` image (two SDKs plus
`DOTNET_EnableWriteXorExecute=0`) — it shipped as `:0.11` with 0.11.0, not as a
0.10 patch. Re-pushing `:0.10` from it would mismatch the `PINNED_DIGEST`
shipped in 0.10.1, and `resolve_image()` refuses to run a *pulled* default
image on a digest mismatch — breaking fresh 0.10.1 installs rather than
fixing anything.

It builds both `linux/amd64` and
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
ghcr.io/jimboschneider/dirtywork-worker:0.11 docker/` run by hand, or the CI
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
`ghcr.io/jimboschneider/dirtywork-worker:0.11`. The next patch release
(0.4.1 for 0.4; 0.5.1 for 0.5; 0.6.1 for 0.6; 0.8.1 for 0.8; 0.9.1 for 0.9; 0.10.1 for 0.10 — 0.7.x shipped unpinned) pins — once `publish-image.yml` has run, take the
digest from its job summary (or resolve it yourself below) and commit it
as `PINNED_DIGEST` ahead of the next release.

1. Resolve the published digest:

       docker pull ghcr.io/jimboschneider/dirtywork-worker:0.11
       docker image inspect --format '{{json .RepoDigests}}' ghcr.io/jimboschneider/dirtywork-worker:0.11

   This prints a JSON array like
   `["ghcr.io/jimboschneider/dirtywork-worker@sha256:<64 hex chars>"]`.

2. Set `PINNED_DIGEST` in `dirtywork/sandbox/docker_args.py` to the
   `sha256:<...>` portion only (not the whole `name@sha256:...` string).

3. Commit the `PINNED_DIGEST` change as part of the release.

## Manual build and push (fallback)

`publish-image.yml` is the normal path; a manual push is only needed to
recover from a broken automated run:

    docker build -t ghcr.io/jimboschneider/dirtywork-worker:0.11 docker/
    docker push ghcr.io/jimboschneider/dirtywork-worker:0.11

then resolve/pin the digest as above.

## Derived images (extra packages)

The worker cannot install anything at run time: docker mode runs with
`--network none` and mounts no host directories, so `apt-get`/`npm i -g`
inside a run will always fail. If your gate needs a tool this image does not
ship, build a derived image once and point `--image` at it:

```Dockerfile
FROM ghcr.io/jimboschneider/dirtywork-worker:0.11
USER root
RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
    && rm -rf /var/lib/apt/lists/*
USER worker
```

    docker build -t my-worker:0.11 .
    dirtywork run --repo ~/repos/thing --image my-worker:0.11 "..."

Keep `USER worker` as the last instruction and add no `ENTRYPOINT`/`CMD`:
dirtywork always passes its own `--entrypoint` and `--user` explicitly at
`docker create` (`dirtywork/sandbox/docker.py`'s `DockerSandbox.start()`
passes `os.getuid()`/`os.getgid()` as `--user uid:gid` on the supported
platforms — macOS/Linux; non-posix falls back to `1000:1000` and Windows is
unsupported — the **host's** uid there, not the image's `worker` uid 1000 —
and the run volume is chowned to that uid), and every later `docker exec`
inherits that user, so `USER
worker` here is defence in depth for anyone who runs the image by hand, not
something dirtywork itself relies on. Never bake `ENV GIT_DIR` or
`ENV GIT_WORK_TREE`: since 1.0 the worker container finds its repository
through the gitfile `/work/.git` (#61), and an image-level `GIT_DIR` would
re-create the bug that fix removed. A custom `--image` is **never** checked against
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
FROM ghcr.io/jimboschneider/dirtywork-worker:0.11
USER root
ENV DOTNET_EnableWriteXorExecute=0
# until the 1.0 base image bakes these in — see the 0.10 defect note above;
# the four below stop the MSBuild/Roslyn build daemons that would otherwise
# outlive every `dotnet build` and be killed as strays (#61)
ENV DOTNET_CLI_USE_MSBUILD_SERVER=0 MSBUILDDISABLENODEREUSE=1 UseSharedCompilation=false DOTNET_NOLOGO=1
ENV NUGET_PACKAGES=/opt/nuget
COPY MyProject.csproj /tmp/restore/MyProject.csproj
RUN dotnet restore /tmp/restore/MyProject.csproj --packages /opt/nuget \
    && rm -rf /tmp/restore \
    && chmod -R a+rwX /opt/nuget
USER worker
```

    docker build -t my-worker:0.11 .
    dirtywork run --repo ~/repos/thing --image my-worker:0.11 "..."

Issue #63 also considered defaulting the image to redirect
`NUGET_PACKAGES`/`DOTNET_CLI_HOME` off `$HOME`; the owner declined it — a
baked `/opt` cache is read-only at run time (so a live restore couldn't
write it anyway), and redirecting to `/tmp` would just move the
conventional cache path rather than honour it. `--home-size` and the baked
`/opt` pattern above are the two supported answers instead. Note from the
#48 soak: `DOTNET_CLI_HOME` alone does **not** relocate everything under
`$HOME` — ASP.NET Data Protection keys, the X509 certificate store, and
NuGet's first-run migration marker all read `$HOME` directly, ignoring that
variable. dirtywork sidesteps this by fixing `HOME=/home/worker` itself
rather than relying on per-tool redirect variables.

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

### The .NET 8+10 image: landed in 0.11, not 1.0

This Dockerfile (SDK 8.0 + 10.0, `DOTNET_EnableWriteXorExecute=0`, the four
.NET stray-process variables) was planned as the 1.0 image (#59) and shipped
early as `:0.11` with the 0.11.0 release, following the same steps every new
minor takes: `DEFAULT_IMAGE` bumped, the `:0.10` literals swept here and in
`docs/machine-contract.md`, `publish-image.yml` pushing the tag on the
`v0.11.0` release with `PINNED_DIGEST = None`, the pin in 0.11.1, and the
built image verified with `--list-sdks` (both `8.0.x` and `10.0.x`) and
`env | grep DOTNET_EnableWriteXorExecute` (`=0`). 1.0 renames nothing about
the image; a `:1.0` tag is just the next minor's rebuild.
