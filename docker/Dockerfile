# docker/Dockerfile
# node:22-bookworm-slim is Debian 12 (bookworm-slim) plus Node 22 LTS, npm and
# corepack from the official image, installed under /usr/local/bin -- on the
# PATH dirtywork passes to every exec. Debian's own Node package is 18,
# end-of-life 2025-04-30 (nodejs/Release schedule.json); Vite 8 requires Node
# 20.19+ / 22.12+ and current Vitest 22.12+, so a worker on the old image could
# not run a real Vite project's tests. 22 is maintenance LTS until 2027-04-30.
FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        bash \
        coreutils \
        findutils \
        python3 \
        python3-venv \
        ripgrep \
        jq \
        uuid-runtime \
        shellcheck \
        curl \
        ca-certificates \
        wget \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# .NET SDKs 8.0 and 10.0, both via Microsoft's official installer script.
# Microsoft's apt feed for Debian 12 does not publish dotnet-sdk for arm64
# (verified 2026-08-16 on Apple Silicon: "E: Unable to locate package
# dotnet-sdk-8.0"); the script installs the right build for arm64 or x64.
# Both channels install into the same --install-dir: dotnet-install.sh lays
# SDKs out per-version under sdk/, so 8.0.x and 10.0.x coexist and share the
# one /usr/bin/dotnet symlink below. 8.0 stays because the sandbox runs
# --network none and an SDK-10-only image can't build net8.0 projects
# offline (its 8.0 targeting packs aren't bundled with SDK 10, so restore
# fails with NETSDK1145 when it can't reach NuGet); 10.0 is added because
# it's the current LTS and is what the invoicr bench project targets (#59).
# libicu/libssl are the runtime deps. Measured cost of the second SDK on the
# 2026-08-24 arm64 build: 360 MB -> 591 MB (+221 MiB), +~40 s build.
RUN apt-get update && apt-get install -y --no-install-recommends libicu72 libssl3 \
    && wget -q https://dot.net/v1/dotnet-install.sh -O /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet \
    && bash /tmp/dotnet-install.sh --channel 10.0 --install-dir /usr/share/dotnet \
    && ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet \
    && rm /tmp/dotnet-install.sh \
    && rm -rf /var/lib/apt/lists/*
# DOTNET_EnableWriteXorExecute=0: dirtywork's bash tool runs every command
# under `ulimit -f 524288` (256 MiB, dirtywork/sandbox/docker.py). With W^X
# enabled (the default) the .NET 8 runtime trips that RLIMIT_FSIZE at
# startup -- even at an 8 GiB limit, consistent with the size it gives its
# double-mapping file -- so on the published :0.10 image every .NET 8
# process (dotnet restore/build/test, a built app) died with "File size
# limit exceeded" (SIGXFSZ, exit 153); the .NET 10 runtime does not trip
# it. Verified 2026-08-24: with the variable set, new/build/run of net8.0
# and net10.0 apps all pass under the limit. W^X is runtime-level hardening
# that adds little inside a read-only, cap-dropped, no-new-privileges
# container.
# no build daemon may outlive a bash call (#61)
ENV DOTNET_ROOT=/usr/share/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    DOTNET_EnableWriteXorExecute=0 \
    DOTNET_CLI_USE_MSBUILD_SERVER=0 \
    MSBUILDDISABLENODEREUSE=1 \
    UseSharedCompilation=false \
    DOTNET_NOLOGO=1

# The official image ships a `node` user at uid/gid 1000. Remove it so
# `useradd -u 1000 -m worker` keeps giving `worker` the uid this image has
# always had; nothing in the sandbox refers to the `node` user.
RUN userdel -r node && useradd -u 1000 -m worker

USER worker

# No ENTRYPOINT and no CMD: every `docker create`/`docker run` dirtywork
# issues passes an explicit --entrypoint (/bin/cat for the tether,
# /bin/chown for the prep container, /bin/sh for compound export steps) and
# every `docker exec` names its binary by absolute path — nothing in this
# image's own launch configuration is ever trusted to pick the running
# program (spec §3: "Entrypoint and PATH are always explicit").
#
# No WORKDIR either: /work is always a docker volume mounted fresh at
# container-create time. Baking a WORKDIR into the image at that same path
# would reproduce the verified bug where a container-level workdir over the
# volume resets its ownership to root:root, persistently. Every command
# sets -w /work itself via `docker exec` (dirtywork/sandbox/docker_args.py's
# exec_argv), which is verified harmless.
