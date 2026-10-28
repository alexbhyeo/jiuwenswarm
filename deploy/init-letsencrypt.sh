#!/usr/bin/env bash
# One-time bootstrap: bring up postgres+app+nginx over plain HTTP, obtain a
# Let's Encrypt certificate via the HTTP-01 webroot challenge, then switch
# nginx to the HTTPS config and start the certbot renewal loop.
#
# Requires port 80 (and 443, once switched over) to be reachable from the
# public internet for the ACME challenge to succeed.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "error: .env not found (copy .env.example to .env and fill in secrets first)" >&2
  exit 1
fi

# Pull just the two values this script needs directly out of .env, without
# sourcing it as shell: docker-compose's own env vars (e.g. POSTGRES_PASSWORD)
# may contain characters (&, *, ...) that bash would misinterpret on `source`.
DOMAIN="$(grep -E '^DOMAIN=' .env | tail -1 | cut -d= -f2-)"
EMAIL="$(grep -E '^LETSENCRYPT_EMAIL=' .env | tail -1 | cut -d= -f2-)"
DOMAIN="${DOMAIN:-happypeaceland.net}"
if [ -z "$EMAIL" ]; then
  echo "error: LETSENCRYPT_EMAIL not set in .env" >&2
  exit 1
fi

mkdir -p deploy/nginx/certbot-www deploy/nginx/letsencrypt

echo "==> Starting postgres, app, nginx (HTTP-only config) ..."
docker compose up -d postgres app nginx

echo "==> Waiting for nginx to accept connections on port 80 ..."
for _ in $(seq 1 30); do
  if curl -s -o /dev/null "http://localhost/"; then
    break
  fi
  sleep 2
done

echo "==> Requesting a certificate for ${DOMAIN} ..."
docker compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d "${DOMAIN}" \
  --email "${EMAIL}" --agree-tos --non-interactive

echo "==> Switching nginx to the HTTPS config ..."
cp deploy/nginx/app-ssl.conf deploy/nginx/conf.d/app.conf
docker compose exec nginx nginx -s reload

echo "==> Starting the certbot renewal loop ..."
docker compose up -d certbot

echo "==> Done. https://${DOMAIN} should now be serving over TLS."
