#!/usr/bin/env bash
# Provision a new, fully isolated user: a dedicated container + volume +
# Docker network (completely separate Director assets, chat history,
# config.yaml/.env, MCP connector credentials from every other user, and
# unreachable from every other user's backend too - not just application
# data, the network path itself), plus a login row in authgate's Postgres
# table pointing session routing at it.
#
# The provisioned container is a completely unmodified jiuwenswarm instance
# - no auth code, no Postgres access. nginx is the one thing dynamically
# joined to its network (not "app"), since nginx is what proxies directly to
# whichever backend authgate's session lookup resolves to. See
# deploy/architecture.md.
#
# Run this on the host (needs Docker access) from the repo root where
# docker-compose.yml lives - not from inside any container. Re-run it with a
# new username to add the Nth user; nothing here needs a code change or a
# redeploy of nginx/app/authgate.
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
# One network per user, with exactly two members (nginx + this user's
# container) by the end of this script. User backends can never reach each
# other - not just application-level isolation, they're not even on the
# same network - and nothing but nginx can ever reach this user's backend.
docker network create "${NETWORK_NAME}" >/dev/null

echo "==> Connecting nginx to ${NETWORK_NAME} ..."
# Works on an already-running container, no restart needed. NOT tracked by
# docker-compose.yml (a static per-user network list there would mean
# editing it and redeploying for every new user) - which means after any
# future redeploy/recreate of the nginx service, this connection has to be
# re-established. That's what deploy/reconnect_user_networks.sh is for; see
# its header and deploy/architecture.md.
docker network connect "${NETWORK_NAME}" jiuwenswarm-nginx

echo "==> Creating volume ${VOLUME_NAME} ..."
docker volume create "${VOLUME_NAME}" >/dev/null

echo "==> Starting container ${CONTAINER_NAME} on network ${NETWORK_NAME} ..."
# Deliberately NOT given Docker socket access or any `ports:`-style host
# mapping: reachable only from nginx, over this one dedicated network -
# never from the host, the public internet, or any other user's container.
# Note it is literally the same image as "app", unmodified - isolation is
# entirely about which network/volume/env vars it gets, not different code.
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
  if docker exec "${CONTAINER_NAME}" curl -sf -o /dev/null http://localhost:5173/; then
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
# Password goes through an env var, never passed as a CLI arg that would
# show up in `docker compose exec` logs/history.
docker compose exec -T \
  -e PROVISION_USERNAME="${USERNAME}" \
  -e PROVISION_PASSWORD="${PASSWORD}" \
  -e PROVISION_BACKEND_URL="${BACKEND_URL}" \
  authgate python3 -c "
import os
from app import create_user_cli
create_user_cli(
    os.environ['PROVISION_USERNAME'],
    os.environ['PROVISION_PASSWORD'],
    os.environ['PROVISION_BACKEND_URL'],
)
"

echo "==> Done. ${USERNAME} can log in at the normal login page now, served by ${CONTAINER_NAME}."
echo "    No model/API-key credentials are configured for this account - chat"
echo "    won't work until credentials are set for it specifically"
echo "    (docker exec ${CONTAINER_NAME} ... or restart it with the right env vars)."
echo
echo "    To remove this user later: docker rm -f ${CONTAINER_NAME} && \\"
echo "      docker network disconnect ${NETWORK_NAME} jiuwenswarm-nginx && \\"
echo "      docker network rm ${NETWORK_NAME} && docker volume rm ${VOLUME_NAME} && \\"
echo "      delete their app_users row (docker compose exec authgate python3 -c \"...\")."
echo "    (the disconnect step is required first - nginx is still attached to"
echo "    ${NETWORK_NAME}, and Docker refuses to remove a network with active endpoints)"
