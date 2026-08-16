# dirtywork/worker image

Built from `docker/Dockerfile`: Debian bookworm-slim, `USER worker` (uid
1000), git, bash, coreutils, findutils, python3, node, .NET SDK, ripgrep.
No `ENTRYPOINT`/`CMD` — every `docker create`/`run`/`exec` in dirtywork
passes its own explicit `--entrypoint` or absolute binary path.

## Build

    docker build -t dirtywork/worker:0.3 docker/

## Verify locally

    docker run --rm --entrypoint /usr/bin/git dirtywork/worker:0.3 --version
    docker run --rm --entrypoint /usr/bin/rg dirtywork/worker:0.3 --version
    docker run --rm --entrypoint /usr/bin/python3 dirtywork/worker:0.3 --version
    docker run --rm --entrypoint /usr/bin/dotnet dirtywork/worker:0.3 --version

## Publish and pin a digest

`dirtywork/sandbox/docker_args.py`'s `PINNED_DIGEST` (default `None`) is
the supply-chain guarantee for the *default* image: once set,
`resolve_image()` refuses to run any resolved image whose digest doesn't
match, regardless of what `--image` or a mutable tag currently points to.

1. Build and push:

       docker build -t dirtywork/worker:0.3 docker/
       docker push dirtywork/worker:0.3

2. Resolve the pushed digest:

       docker image inspect --format '{{json .RepoDigests}}' dirtywork/worker:0.3

   This prints a JSON array like `["dirtywork/worker@sha256:<64 hex chars>"]`.

3. Set `PINNED_DIGEST` in `dirtywork/sandbox/docker_args.py` to the
   `sha256:<...>` portion only (not the whole `name@sha256:...` string).

4. Commit the `PINNED_DIGEST` change as part of the release. Until it is
   set, `resolve_image()` performs no pin check — every resolved image is
   trusted by whatever `docker image inspect` currently reports for
   `--image`'s value.

## Updating the image

Rebuild, push, verify with the commands above, then repeat the pin
procedure. `--image` lets an operator override the default per run;
`PINNED_DIGEST` only constrains the maintained default.
