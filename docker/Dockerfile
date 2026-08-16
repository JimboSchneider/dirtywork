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

# .NET SDK via Microsoft's apt package feed — the documented route for
# Debian 12 (bookworm): https://learn.microsoft.com/en-us/dotnet/core/install/linux-debian
# Use the ARM64-specific repository for bookworm
RUN wget -q https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
        -O /tmp/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends dotnet-sdk-8.0 \
    && rm -rf /var/lib/apt/lists/*

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
