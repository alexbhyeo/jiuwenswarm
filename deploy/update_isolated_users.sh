#!/usr/bin/env bash
# Recreate every isolated user's container from the just-rebuilt
# `jiuwenswarm-app` image, preserving its volume (data) and network
# (identity/routing) exactly - only the code inside changes.
#
# REQUIRED after `docker compose build app` if you want isolated users to
# actually run the new code: rebuilding the image does NOT touch containers
# already running from the old image layer, and every isolated user's
# container was started with a plain `docker run` (deploy/provision_user.sh),
# not docker-compose, so `docker compose up -d app` only recreates the
# default/primary instance. This is the isolated-user equivalent of that
# step - see deploy/update.md.
#
# Idempotent and safe to run anytime: a user whose container is already on
# the current image is simply recreated with no functional change.
#
# Usage: deploy/update_isolated_users.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Finding provisioned users ..."
USERNAMES="$(docker compose exec -T authgate python3 -c "
import psycopg2
conn = psycopg2.connect()
with conn, conn.cursor() as cur:
    cur.execute('SELECT username FROM app_users WHERE backend_base_url IS NOT NULL')
    for (username,) in cur.fetchall():
        print(username)
conn.close()
")"

if [ -z "$USERNAMES" ]; then
  echo "No isolated users provisioned - nothing to update."
  exit 0
fi

while IFS= read -r USERNAME; do
  [ -z "$USERNAME" ] && continue
  NETWORK_NAME="jiuwenswarm_user_${USERNAME}_net"
  VOLUME_NAME="jiuwenswarm_user_${USERNAME}_workspace"
  CONTAINER_NAME="jiuwenswarm-user-${USERNAME}"

  if ! docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
    echo "  ! ${CONTAINER_NAME} does not exist - skipping ${USERNAME} (was it ever provisioned on this host?)"
    continue
  fi

  echo "==> Recreating ${CONTAINER_NAME} from the current jiuwenswarm-app image ..."
  docker rm -f "${CONTAINER_NAME}" >/dev/null
  docker run -d \
    --name "${CONTAINER_NAME}" \
    --network "${NETWORK_NAME}" \
    --restart unless-stopped \
    -v "${VOLUME_NAME}:/home/app/.jiuwenswarm" \
    -e FRONTEND_HOST=0.0.0.0 \
    jiuwenswarm-app >/dev/null
  # Same name, same network: nginx resolves it again via Docker's embedded
  # DNS with no reconnect needed - nginx's own network membership never
  # changed, only the container at the other end was recreated.

  echo "==> Waiting for ${CONTAINER_NAME} to come back up ..."
  ready=0
  for _ in $(seq 1 30); do
    if docker exec "${CONTAINER_NAME}" curl -sf -o /dev/null http://localhost:5173/; then
      ready=1
      break
    fi
    sleep 2
  done
  if [ "$ready" -ne 1 ]; then
    echo "  ! ${CONTAINER_NAME} did not come back up in time - check 'docker logs ${CONTAINER_NAME}'" >&2
  else
    echo "  + ${USERNAME}: updated and healthy"
  fi
done <<< "$USERNAMES"

echo "==> Done."
