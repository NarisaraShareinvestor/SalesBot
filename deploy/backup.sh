#!/usr/bin/env bash
# SalesBot — nightly SQLite backup (installed as /etc/cron.d/salesbot-backup by deploy.sh).
# Uses SQLite's online backup API inside the container, so it's consistent even while the app writes.
# Keeps 14 days in /root/salesbot-backups.  Restore: gunzip a file → data/salesbot.db, then restart the container.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIR=/root/salesbot-backups
KEEP_DAYS=14
mkdir -p "$DIR"; chmod 700 "$DIR"

docker exec salesbot python -c "
import sqlite3
src = sqlite3.connect('/data/salesbot.db'); dst = sqlite3.connect('/data/.backup-tmp.db')
src.backup(dst); dst.close(); src.close()"

OUT="$DIR/salesbot-$(date +%Y%m%d-%H%M).db"
mv "$APP_DIR/data/.backup-tmp.db" "$OUT"
gzip -f "$OUT"
find "$DIR" -name 'salesbot-*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date '+%F %T') backup ok: $OUT.gz ($(du -h "$OUT.gz" | cut -f1))"
