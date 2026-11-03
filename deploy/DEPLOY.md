# Deploy SalesBot → shared VPS (salesbot.ohmai.me)

SalesBot runs as **one Docker container** bound to `127.0.0.1:8001`, behind the VPS's
host **nginx + Let's Encrypt** (same pattern as ipo / workgraph / contentseo on this box).
Nothing else on the box is touched — every name is `salesbot*`.

- **Server:** `187.127.218.42` (the old Hostinger box `187.77.134.126` has expired)
- **SSH:** `ssh -i ~/.ssh/line_inbox_deploy root@187.127.218.42`
- **App dir:** `/opt/salesbot` · **container:** `salesbot` · **port:** `127.0.0.1:8001` → `:8000`
- **vhost:** `/etc/nginx/sites-enabled/zz-salesbot.ohmai.me.conf` (keep the `zz-` prefix)

> This box also runs live customer services (search-api :8788, the chatbots, …).
> Never restart/reload anything but `salesbot`, and never run a bare `docker compose down`.

---

## 1. Cloudflare DNS (do this first)

| Type | Name     | IPv4            | Proxy status            |
|------|----------|-----------------|-------------------------|
| A    | salesbot | 187.127.218.42  | **DNS only (grey)** ← important |

Keep it **grey** until the cert is issued (HTTP-01 fails behind the orange proxy).
Afterwards you can flip it to Proxied with SSL mode **Full**.

## 2. Get the code on the VPS
```bash
ssh -i ~/.ssh/line_inbox_deploy root@187.127.218.42
cd /opt && git clone https://github.com/NarisaraShareinvestor/SalesBot.git salesbot
mkdir -p /opt/salesbot/data
```

## 3. Bring the secrets + data (gitignored — NOT in the repo)
From your Mac:
```bash
cd ~/Projects/SalesBot
K="-i $HOME/.ssh/line_inbox_deploy"; H=root@187.127.218.42:/opt/salesbot/data
# fill the real values (OpenAI, LOGIN_PASSWORD) — see .env.production.example
scp $K .env                    $H/.env
scp $K backend/salesbot.db     $H/salesbot.db
```

## 4. Deploy
```bash
ssh -i ~/.ssh/line_inbox_deploy root@187.127.218.42 'cd /opt/salesbot && ./deploy/deploy.sh'
```
The script: builds + starts only `salesbot` → gets the cert via webroot (first time only) →
installs the vhost → `nginx -t` (rolls back the vhost if it fails) → reload.

Login: one shared account — `LOGIN_USER` (default `admin`) + `LOGIN_PASSWORD` in `data/.env`.
The app checks a signed session cookie on every `/api/*` (no nginx basic auth).

## 5. Verify
```bash
curl -I http://127.0.0.1:8001/      # on the VPS: HTTP 200
```
Then open **https://salesbot.ohmai.me**.

## Updating later
**Frontend only** (`frontend/` is bind-mounted → live after pull):
```bash
ssh -i ~/.ssh/line_inbox_deploy root@187.127.218.42 'cd /opt/salesbot && git pull'
```
**Backend** (baked into the image → rebuild only this service):
```bash
ssh -i ~/.ssh/line_inbox_deploy root@187.127.218.42 'cd /opt/salesbot && git pull && docker compose up -d --build salesbot'
```
Back up the DB before a schema change: `cp data/salesbot.db /root/salesbot.db.bak`.

## Notes
- Customer data (`salesbot.db`, `.env`, `*.json`) lives in `/opt/salesbot/data` — gitignored, never pushed.
- Container is capped at 512 MB (`mem_limit`) — the box is shared and RAM is tight.
- Re-import from fresh Excel: scp the two `.xlsx` into `Data/` and run
  `docker compose run --rm salesbot python import_data.py`.

## Backups
`deploy.sh` installs `/etc/cron.d/salesbot-backup` → `deploy/backup.sh` every night 02:30 Bangkok (19:30 UTC),
14 days kept in `/root/salesbot-backups` (log: `/var/log/salesbot-backup.log`).
Restore: `gunzip -c /root/salesbot-backups/salesbot-<date>.db.gz > /opt/salesbot/data/salesbot.db && docker compose restart salesbot`.
