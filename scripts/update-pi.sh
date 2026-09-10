#!/bin/sh
# Run from a trusted local checkout. No git pull and no inbound deployment port.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
exec 9>/run/dobby-update/update.lock
flock -n 9 || exit 0
docker compose -f compose.yaml -f compose.registry.yaml pull dobby
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build --pull never dobby
