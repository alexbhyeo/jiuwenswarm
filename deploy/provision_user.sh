#!/usr/bin/env bash
# Provision a new, fully isolated user: a dedicated container + volume +
# Docker network (completely separate Director assets, chat history,
# config.yaml/.env, MCP connector credentials from every other user, and
# unreachable from every other user's backend too - not just application
# data, the network path itself), plus a login row in Postgres pointing the
# primary app's session routing at it.
#
# Run this on the host (needs Docker access) from the repo root where
# docker-compose.yml lives - not from inside any container. Re-run it with a
# new username to add the Nth user; nothing here needs a code change or a
# redeploy of the primary `app` service.
#
# Usage: deploy/provision_user.sh <username> <password>
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ne 2 ]; then
  echo "usage: $0 <username> <password>" >&2
  exit 1
fi

USERNAME="$1"
PASSWORD="$2"

if ! [[ "$USERNAME" =~ ^[a-z0-9-]+$ ]]; then
  echo "error: username must be lowercase letters/digits/hyphens only (used as a container/volume/network name)" >&2
  exit 1
fi

NETWORK_NAME="jiuwenswarm_user_${USERNAME}_net"
VOLUME_NAME="jiuwenswarm_user_${USERNAME}_workspace"
CONTAINER_NAME="jiuwenswarm-user-${USERNAME}"
BACKEND_URL="http://${CONTAINER_NAME}:5173"

if docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "error: container ${CONTAINER_NAME} already exists - pick a different username or remove it first" >&2
  exit 1
fi

echo "==> Creating dedicated network ${NETWORK_NAME} ..."
# One network per user, with exactly two members (app + this user's
# container) by the end of this script. User backends can never reach each
# other - not just application-level isolation, they're not even on the
# same network - and nothing but `app` can ever reach this user's backend.
docker network create "${NETWORK_NAME}" >/dev/null

echo "==> Connecting the primary app container to ${NETWORK_NAME} ..."
# Works on an already-running container, no restart needed. NOT tracked by
# docker-compose.yml (a static per-user network list there would mean
# editing it and redeploying for every new user) - which means after any
# future redeploy/recreate of the `app` service, this connection has to be
# re-established. That's what deploy/reconnect_user_networks.sh is for; see
# its header and deploy/architecture.md.
docker network connect "${NETWORK_NAME}" jiuwenswarm-app

echo "==> Creating volume ${VOLUME_NAME} ..."
docker volume create "${VOLUME_NAME}" >/dev/null

echo "==> Starting container ${CONTAINER_NAME} on network ${NETWORK_NAME} ..."
# Deliberately NOT given Docker socket access or any `ports:`-style host
# mapping: reachable only from `app`, over this one dedicated network - never
# from the host, the public internet, or any other user's container. PGHOST
# stays unset, so its own login gate stays disabled; it's never reached
# except through the primary's already-authenticated hop.
docker run -d \
  --name "${CONTAINER_NAME}" \
  --network "${NETWORK_NAME}" \
  --restart unless-stopped \
  -v "${VOLUME_NAME}:/home/app/.jiuwenswarm" \
  -e FRONTEND_HOST=0.0.0.0 \
  jiuwenswarm-app >/dev/null

echo "==> Waiting for ${CONTAINER_NAME} to come up ..."
ready=0
for _ in $(seq 1 30); do
  if docker exec "${CONTAINER_NAME}" curl -sf http://localhost:5173/login >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "error: ${CONTAINER_NAME} did not become ready in time - check 'docker logs ${CONTAINER_NAME}'" >&2
  exit 1
fi

echo "==> Creating login row for ${USERNAME} (backend_base_url=${BACKEND_URL}) ..."
# Password goes through an env var, never interpolated into the Python
# source, so shell-special characters in it (the usual case for a real
# password) can't be misread as shell syntax.
docker compose exec -T \
  -e PROVISION_USERNAME="${USERNAME}" \
  -e PROVISION_PASSWORD="${PASSWORD}" \
  -e PROVISION_BACKEND_URL="${BACKEND_URL}" \
  app python3 -c "
import os
from jiuwenswarm.channels.web import simple_auth
simple_auth.create_user(
    os.environ['PROVISION_USERNAME'],
    os.environ['PROVISION_PASSWORD'],
    backend_base_url=os.environ['PROVISION_BACKEND_URL'],
)
print('created')
"

echo "==> Done. ${USERNAME} can log in at the normal login page now, served by ${CONTAINER_NAME}."
echo "    No model/API-key credentials are configured for this account - Director Mode"
echo "    generation and chat won't work until credentials are set for it specifically"
echo "    (docker exec ${CONTAINER_NAME} ... or restart it with the right env vars)."
echo
echo "    To remove this user later: docker rm -f ${CONTAINER_NAME} && \\"
echo "      docker network rm ${NETWORK_NAME} && docker volume rm ${VOLUME_NAME} && \\"
echo "      delete their app_users row (docker compose exec app python3 -c \"...\")."
