#!/usr/bin/env bash
# SalesBot — deploy / update on the Hostinger VPS (run as root, from the repo root).
# Idempotent: safe to re-run for updates (git pull + rebuild). Touches ONLY SalesBot.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="salesbot.ohmai.me"
cd "$APP_DIR"

echo "==> SalesBot deploy in $APP_DIR"

# 1) runtime data dir (persists across rebuilds; NOT in git)
mkdir -p data
[ -f data/.env ]             || { echo "!! data/.env missing — create it from .env.production.example first"; exit 1; }
[ -f data/ae_emails.json ]   || echo '{}' > data/ae_emails.json
[ -f data/user_smtp.json ]   || echo '{}' > data/user_smtp.json
# data/salesbot.db should be scp'd in beforehand; create empty if absent (app will init tables)
[ -f data/salesbot.db ]      || { echo "(no data/salesbot.db — starting with an empty DB)"; touch data/salesbot.db; }

# 2) build + (re)start container — binds 127.0.0.1:8001 only
echo "==> docker compose up"
docker compose up -d --build

# 3) nginx server block (only writes if missing — never clobbers other sites)
NGINX_AVAIL="/etc/nginx/sites-available/${DOMAIN}"
NGINX_ENABLED="/etc/nginx/sites-enabled/${DOMAIN}"
if [ ! -f "$NGINX_AVAIL" ]; then
  echo "==> installing nginx server block"
  cp deploy/nginx-salesbot.conf "$NGINX_AVAIL"
  ln -sf "$NGINX_AVAIL" "$NGINX_ENABLED"
fi
echo "==> nginx -t && reload"
nginx -t && systemctl reload nginx

# 4) TLS — issue once (skips if a cert already exists)
if ! certbot certificates 2>/dev/null | grep -q "$DOMAIN"; then
  echo "==> requesting Let's Encrypt cert (DNS for $DOMAIN must resolve here, DNS-only/grey in Cloudflare)"
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect \
    -m "${LE_EMAIL:-admin@ohmai.me}" || echo "!! certbot failed — see note about Cloudflare grey-cloud below"
fi

echo "==> health check"
sleep 2
curl -fsS -o /dev/null -w "local backend: HTTP %{http_code}\n" http://127.0.0.1:8001/ || true
echo "==> done. Visit: https://${DOMAIN}"
