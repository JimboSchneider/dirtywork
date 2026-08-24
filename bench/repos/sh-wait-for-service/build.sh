#!/usr/bin/env bash
# Wait for the local health endpoint before building, then write the build
# confirmation for the given name.
set -euo pipefail

name="anon"
while [ $# -gt 0 ]; do
  case "$1" in
    --name)
      name="$2"
      shift 2
      ;;
    --name=*)
      name="${1#--name=}"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

wait_for_service() {
  until curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; do
    sleep 2
  done
}

wait_for_service

echo "built for $name" > out.txt
