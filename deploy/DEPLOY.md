# Deploy SalesBot → Hostinger VPS (salesbot.ohmai.me)

SalesBot runs as **one Docker container** bound to `127.0.0.1:8001`, behind the VPS's
existing host **nginx + Let's Encrypt** (same pattern as the other ohmai.me sites).
Nothing else on the box is touched.

- **Server:** Hostinger VPS `187.77.134.126` (where Hermes already runs) — **not** the EC2 54.x
- **Subdomain:** `salesbot.ohmai.me`
- **Internal port:** `127.0.0.1:8001` → container `:8000`

---

## 1. Cloudflare DNS (do this first)
Add an **A record**:

| Type | Name     | IPv4            | Proxy status            |
|------|----------|-----------------|-------------------------|
| A    | SalesBot | 187.77.134.126  | **DNS only (grey)** ← important |

> Keep it **grey** until the TLS cert is issued (Let's Encrypt HTTP-01 fails behind the
> orange proxy). After deploy succeeds you can flip it to **Proxied (orange)** and set
> the Cloudflare SSL mode to **Full**, matching the other subdomains.

## 2. Get the code on the VPS
```bash
ssh root@187.77.134.126
cd /opt              # or wherever the other apps live
git clone https://github.com/NarisaraShareinvestor/SalesBot.git
cd SalesBot
mkdir -p data
```

## 3. Bring the secrets + data (they are gitignored — NOT in the repo)
From your Mac (new terminal):
```bash
cd ~/Projects/SalesBot
# OpenAI key + settings
cp .env.production.example /tmp/salesbot.env   # then edit /tmp/salesbot.env with the real OPENAI_API_KEY
scp /tmp/salesbot.env       root@187.77.134.126:/opt/SalesBot/data/.env
# the already-populated database (411 customers + tasks)
scp backend/salesbot.db     root@187.77.134.126:/opt/SalesBot/data/salesbot.db
# optional: existing AE-email / SMTP maps
scp backend/ae_emails.json  root@187.77.134.126:/opt/SalesBot/data/ae_emails.json 2>/dev/null || true
scp backend/user_smtp.json  root@187.77.134.126:/opt/SalesBot/data/user_smtp.json 2>/dev/null || true
```

## 4. Deploy
```bash
ssh root@187.77.134.126
cd /opt/SalesBot
LE_EMAIL=narisara.pa@shareinvestor.com ./deploy/deploy.sh
```
The script: builds the image → starts the container on `127.0.0.1:8001` → installs the
nginx server block (only if absent) → `nginx -t` + reload → requests the Let's Encrypt cert.

## 5. Verify
```bash
curl -I http://127.0.0.1:8001/         # backend up (HTTP 200)
```
Then open **https://salesbot.ohmai.me** in a browser.

## Updating later
```bash
ssh root@187.77.134.126 'cd /opt/SalesBot && git pull && docker compose up -d --build'
```

## Notes
- Customer data (`salesbot.db`, `.env`, `*.json`) lives in `data/` on the VPS — gitignored, never pushed.
- The container listens on localhost only; the public entry point is nginx on 443.
- To re-import from fresh Excel instead of copying the db: scp the two `.xlsx` into `Data/`
  and run `docker compose run --rm salesbot python import_data.py` (cwd is already `backend/`).
