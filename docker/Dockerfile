# docker/Dockerfile
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        bash \
        coreutils \
        findutils \
        python3 \
        python3-venv \
        nodejs \
        npm \
        ripgrep \
        ca-certificates \
        wget \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# .NET SDK 8.0 via Microsoft's official installer script. Microsoft's apt feed
# for Debian 12 does not publish dotnet-sdk-8.0 for arm64 (verified 2026-08-16
# on Apple Silicon: "E: Unable to locate package dotnet-sdk-8.0"); the script
# installs the right build for arm64 or x64. libicu/libssl are the runtime deps.
RUN apt-get update && apt-get install -y --no-install-recommends libicu72 libssl3 \
    && wget -q https://dot.net/v1/dotnet-install.sh -O /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet \
    && ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet \
    && rm /tmp/dotnet-install.sh \
    && rm -rf /var/lib/apt/lists/*
ENV DOTNET_ROOT=/usr/share/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

RUN useradd -u 1000 -m worker

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
