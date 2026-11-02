#!/usr/bin/env bash
# Build effectcraft-cli locally (from the existing sibling checkout) and copy
# the release binary into this directory's build context, so Dockerfile's
# `COPY` has something to pick up. Run this BEFORE `docker compose build
# effects-worker` / `docker compose up -d --build effects-worker`.
#
# Zero changes are made to the effectcraft repo itself (no Dockerfile added
# there, no files written outside its own target/ build directory) - see
# deploy/architecture.md and the design doc this implements
# (.agents/agent-docs/designs/proposals/D-20261101-2128-director-effectcraft-integration/design.md).
#
# Usage: deploy/effects-worker/build.sh [path-to-effectcraft-repo]
# Defaults to a sibling checkout at ../effectcraft relative to this repo's
# own parent directory (i.e. both repos checked out side by side).
set -euo pipefail
cd "$(dirname "$0")/../.."  # repo root

EFFECTCRAFT_REPO="${1:-../effectcraft}"
if [ ! -d "$EFFECTCRAFT_REPO" ]; then
  echo "error: effectcraft repo not found at '$EFFECTCRAFT_REPO' - pass its path as an argument" >&2
  exit 1
fi

echo "==> Building effectcraft-cli (release) in ${EFFECTCRAFT_REPO} ..."
( cd "$EFFECTCRAFT_REPO" && cargo build --release -p effectcraft-cli )

BIN_SRC="${EFFECTCRAFT_REPO}/target/release/effectcraft-cli"
if [ ! -f "$BIN_SRC" ]; then
  echo "error: build did not produce ${BIN_SRC}" >&2
  exit 1
fi

mkdir -p deploy/effects-worker/bin
cp "$BIN_SRC" deploy/effects-worker/bin/effectcraft-cli
chmod +x deploy/effects-worker/bin/effectcraft-cli
echo "==> Copied to deploy/effects-worker/bin/effectcraft-cli ($(du -h deploy/effects-worker/bin/effectcraft-cli | cut -f1))"
echo "==> Now run: docker compose build effects-worker"
