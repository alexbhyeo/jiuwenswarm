#!/usr/bin/env bash
# Reconnect nginx to every provisioned user's dedicated network
# (jiuwenswarm_user_<username>_net).
#
# REQUIRED after any redeploy/recreate of the nginx service - e.g.
# `docker compose up -d --build nginx`, or anything else that replaces that
# container. Each per-user network is attached to nginx imperatively by
# deploy/provision_user.sh via `docker network connect`, which is NOT
# tracked by docker-compose.yml (a static per-user network list there would
# mean editing it and redeploying for every new user - see the comment on
# the `nginx` service in docker-compose.yml). Compose only restores the
# networks actually declared there (frontend) when it recreates a
# container; every per-user connection is lost and has to be re-established,
# or every already-provisioned user's routing silently breaks until this
# runs.
#
# Idempotent and safe to run anytime, including when nothing changed or
# right after provision_user.sh (it'll just report "already connected").
#
# Usage: deploy/reconnect_user_networks.sh
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
  echo "No isolated users provisioned - nothing to reconnect."
  exit 0
fi

while IFS= read -r USERNAME; do
  [ -z "$USERNAME" ] && continue
  NETWORK_NAME="jiuwenswarm_user_${USERNAME}_net"
  if ! docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
    echo "  ! ${NETWORK_NAME} does not exist - skipping ${USERNAME} (was its container ever provisioned on this host?)"
    continue
  fi
  if docker network connect "${NETWORK_NAME}" jiuwenswarm-nginx 2>/dev/null; then
    echo "  + ${USERNAME}: connected nginx to ${NETWORK_NAME}"
  else
    echo "  = ${USERNAME}: nginx already connected to ${NETWORK_NAME}"
  fi
done <<< "$USERNAMES"

echo "==> Done."
