# dirtywork worker image (ghcr.io/jimboschneider/dirtywork-worker)

Built from `docker/Dockerfile`: Debian bookworm-slim, `USER worker` (uid
1000), git, bash, coreutils, findutils, python3, node, .NET SDK, ripgrep.
.NET SDK 8.0 is installed with Microsoft's official `dotnet-install.sh`
(channel 8.0) because the apt feed lacks arm64 packages for Debian 12.
No `ENTRYPOINT`/`CMD` — every `docker create`/`run`/`exec` in dirtywork
passes its own explicit `--entrypoint` or absolute binary path.

## Build

    docker build -t ghcr.io/jimboschneider/dirtywork-worker:0.4 docker/

## Verify locally

    docker run --rm --entrypoint /usr/bin/git ghcr.io/jimboschneider/dirtywork-worker:0.4 --version
    docker run --rm --entrypoint /usr/bin/rg ghcr.io/jimboschneider/dirtywork-worker:0.4 --version
    docker run --rm --entrypoint /usr/bin/python3 ghcr.io/jimboschneider/dirtywork-worker:0.4 --version
    docker run --rm --entrypoint /usr/bin/dotnet ghcr.io/jimboschneider/dirtywork-worker:0.4 --version

## Publishing (automated)

`.github/workflows/publish-image.yml` builds and pushes this image to
GitHub Container Registry (`ghcr.io/jimboschneider/dirtywork-worker`) on
every GitHub release (`release: types: [published]` — the same trigger
`publish.yml` uses for the PyPI package). It builds both `linux/amd64` and
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

`dirtywork/sandbox/docker_args.py`'s `PINNED_DIGEST` (default `None`) is
the supply-chain guarantee for the default image: once set,
`resolve_image()` refuses to run any resolved image whose digest doesn't
match, regardless of what `--image` or a mutable tag currently points to.

0.4.0 ships with `PINNED_DIGEST = None`: there is no prior publish to pin
against on the very first release, so `resolve_image()` performs no pin
check and trusts whatever `docker image inspect` currently reports for
`ghcr.io/jimboschneider/dirtywork-worker:0.4`. 0.4.1 (and every release
after it) pins — once `publish-image.yml` has run at least once, take the
digest from its job summary (or resolve it yourself below) and commit it
as `PINNED_DIGEST` ahead of the next release.

1. Resolve the published digest:

       docker pull ghcr.io/jimboschneider/dirtywork-worker:0.4
       docker image inspect --format '{{json .RepoDigests}}' ghcr.io/jimboschneider/dirtywork-worker:0.4

   This prints a JSON array like
   `["ghcr.io/jimboschneider/dirtywork-worker@sha256:<64 hex chars>"]`.

2. Set `PINNED_DIGEST` in `dirtywork/sandbox/docker_args.py` to the
   `sha256:<...>` portion only (not the whole `name@sha256:...` string).

3. Commit the `PINNED_DIGEST` change as part of the release.

## Manual build and push (fallback)

`publish-image.yml` is the normal path; a manual push is only needed to
recover from a broken automated run:

    docker build -t ghcr.io/jimboschneider/dirtywork-worker:0.4 docker/
    docker push ghcr.io/jimboschneider/dirtywork-worker:0.4

then resolve/pin the digest as above.

## Updating the image

Rebuild (normally by cutting a release, which drives `publish-image.yml`),
verify with the commands above, then repeat the pin procedure. `--image`
lets an operator override the default per run; `PINNED_DIGEST` only
constrains the maintained default.
