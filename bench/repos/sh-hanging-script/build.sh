#!/usr/bin/env bash
# Writes a build confirmation for the given name.
set -euo pipefail
read -r name
echo "built for $name" > out.txt
