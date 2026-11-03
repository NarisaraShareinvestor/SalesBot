#!/usr/bin/env bash
# SalesBot — deploy / update on the shared VPS 187.127.218.42 (run as root from /opt/salesbot).
# Idempotent: safe to re-run. Touches ONLY salesbot-named things (container, vhost, cert, logs).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="salesbot.ohmai.me"
VHOST="/etc/nginx/sites-enabled/zz-${DOMAIN}.conf"
cd "$APP_DIR"

echo "==> SalesBot deploy in $APP_DIR"

# 1) runtime data dir (persists across rebuilds; NOT in git)
mkdir -p data
[ -f data/.env ]             || { echo "!! data/.env missing — create it from .env.production.example first"; exit 1; }
grep -q '^LOGIN_PASSWORD=.' data/.env || { echo "!! set LOGIN_PASSWORD in data/.env (LOGIN_USER defaults to admin)"; exit 1; }
# data/salesbot.db should be scp'd in beforehand; create empty if absent (app will init tables)
[ -f data/salesbot.db ]      || { echo "(no data/salesbot.db — starting with an empty DB)"; touch data/salesbot.db; }

# 2) build + (re)start ONLY the salesbot service — 127.0.0.1:8001
echo "==> docker compose up salesbot"
docker compose up -d --build salesbot

# 3) TLS first (webroot), then the vhost that references the cert
if [ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
  echo "==> requesting cert (DNS for $DOMAIN must point here, grey cloud in Cloudflare)"
  # temporary port-80-only block so this host's challenge is served from /var/www/html
  cat > "$VHOST" <<EOF
server { listen 80; listen [::]:80; server_name ${DOMAIN};
  location /.well-known/acme-challenge/ { root /var/www/html; } }
EOF
  nginx -t && systemctl reload nginx
  certbot certonly --webroot -w /var/www/html -d "$DOMAIN" --non-interactive --agree-tos \
    -m "${LE_EMAIL:-narisara.pa@shareinvestor.com}" \
    || { rm -f "$VHOST"; nginx -t && systemctl reload nginx; echo "!! certbot failed"; exit 1; }
fi

# 4) real vhost — roll back if nginx rejects it so the other sites never go down
echo "==> installing vhost"
BAK="/root/zz-${DOMAIN}.conf.bak"
rm -f "$BAK"; [ -f "$VHOST" ] && cp "$VHOST" "$BAK"
cp deploy/nginx-salesbot.conf "$VHOST"
if ! nginx -t; then
  echo "!! nginx -t failed — restoring previous vhost"
  if [ -f "$BAK" ]; then cp "$BAK" "$VHOST"; else rm -f "$VHOST"; fi
  nginx -t && systemctl reload nginx
  exit 1
fi
systemctl reload nginx

# 5) nightly DB backup at 02:30 Bangkok time (19:30 UTC), 14 days kept in /root/salesbot-backups
chmod +x deploy/backup.sh
CRON=/etc/cron.d/salesbot-backup
LINE="30 19 * * * root $APP_DIR/deploy/backup.sh >> /var/log/salesbot-backup.log 2>&1"
if [ "$(cat "$CRON" 2>/dev/null)" != "$LINE" ]; then
  echo "$LINE" > "$CRON"; chmod 644 "$CRON"; echo "==> backup cron installed ($CRON)"
fi

echo "==> health check"
sleep 3
curl -fsS -o /dev/null -w "local backend: HTTP %{http_code}\n" http://127.0.0.1:8001/ || true
echo "==> done. Visit: https://${DOMAIN}"
