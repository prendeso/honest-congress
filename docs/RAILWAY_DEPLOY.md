# Deploying Honest Congress to Railway

Step-by-step guide. Skim the prerequisites, then walk through the steps in order. Estimated time: ~20 minutes for a first-time deploy.

## Prerequisites

- A **GitHub** account that owns (or can be granted access to) `prendeso/honest-congress`.
- A **Railway** account at <https://railway.com>. The free trial gives you $5 of usage credit, which covers a small instance running 24/7 for ~3 weeks.
- Optionally: a **Congress.gov API key** (free, <https://api.congress.gov/sign-up/>) and a **QuiverQuant API key** ($10/mo, <https://www.quiverquant.com>). Both unlock additional data sources but the app boots fine without them.

## What's already set up in the repo

You don't need to write any deploy config — it's all in the repo:

- `Dockerfile` — multi-stage, `python:3.12-slim` base, runs as non-root, includes `/health` healthcheck
- `railway.toml` — points Railway at the Dockerfile, runs `alembic upgrade head` before each deploy
- `.dockerignore` — keeps the build context lean
- `alembic/` — Postgres schema migrations

---

## Step 1 — Create the Railway project

1. Sign in to <https://railway.com>.
2. Click **New Project** → **Deploy from GitHub repo**.
3. If prompted, install the **Railway** GitHub App and grant it access to `prendeso/honest-congress` (you can scope to just this one repo).
4. Pick `prendeso/honest-congress` from the list. Railway creates a project and starts a first deploy attempt — this **will fail** because the database isn't attached yet. That's expected; ignore it for now.

## Step 2 — Attach a Postgres database

1. Inside the project, click **+ New** (top right) → **Database** → **Add PostgreSQL**.
2. Wait ~30 seconds for the database service to provision. You'll see a green check next to it.
3. Click your **app service** (the one named `honest-congress`) → **Variables** tab.
4. Click **+ New Variable** → **Add Reference** → choose the Postgres service → choose `DATABASE_URL`. This wires the database connection string into the app at deploy time.

Railway exports the URL as `postgres://user:pass@host:port/db`. Our `src/db/database.py` normalizes that to the SQLAlchemy + psycopg3 form automatically — no extra config needed.

## Step 3 — Set environment variables

Still in **Variables** on the app service, add the following. **Required** ones must be set before the next deploy or the app will refuse to start.

### Required

| Variable | Value | Why |
|---|---|---|
| `ENV` | `production` | Enables prod-mode validation in `src/config.py` |
| `ADMIN_PASSWORD` | A strong random string | Required by `src/config.get_settings()` when `ENV=production`. Used for `/admin` panel login. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ALLOWED_ORIGINS` | Your app's public URL, e.g. `https://honest-congress-production.up.railway.app` | CORS. After Railway assigns a domain (Step 5) come back and update this |

### Optional

| Variable | Default | What it does |
|---|---|---|
| `CONGRESS_GOV_API_KEY` | empty | Backup data source for member metadata |
| `QUIVERQUANT_API_KEY` | empty | Live trade data ingestion via `/api/anomalies/sync-trades` |
| `LATE_FILING_MIN_DAYS` | `60` | Days past the 45-day STOCK Act deadline before a late-PTR anomaly is flagged |
| `LATE_FILING_MIN_AMOUNT_USD` | `50000` | Minimum transaction size for late-filing flags |
| `WEALTH_GROWTH_THRESHOLD_PERCENT` | `200.0` | Wealth-vs-salary detector threshold |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for verbose logs |

`PORT` is injected automatically by Railway — don't set it yourself.

## Step 4 — Deploy

Click **Deploy** (or push any commit to `main` — Railway auto-deploys on push).

Watch the **Deployments** tab. You should see, in order:

1. **Build** (~2–3 min): Docker image build, including `pip install -r requirements.txt`
2. **Pre-deploy** (~10 s): `alembic upgrade head` runs. First time, it creates all 6 tables.
3. **Deploy** (~10 s): uvicorn starts on `$PORT`
4. **Active**: green dot — service is live

If the build fails: click the deployment → **View Logs**. Common issues are at the bottom of this doc.

## Step 5 — Get a public URL

1. Click your **app service** → **Settings** → **Networking** → **Generate Domain**.
2. Railway assigns something like `honest-congress-production.up.railway.app`. Copy it.
3. Go back to **Variables** and set `ALLOWED_ORIGINS=https://<that-domain>`. Save — Railway redeploys.

(Optional: under **Networking** you can also add a **Custom Domain** like `honestcongress.example.com`. Update DNS as instructed and put both your custom domain and the Railway domain into `ALLOWED_ORIGINS`, comma-separated.)

## Step 6 — Verify the deploy

In a browser:

```
https://<your-domain>/health        # should return {"status":"healthy","database":"connected"}
https://<your-domain>/health/live   # should return {"status":"alive"}
https://<your-domain>/              # the dashboard home page (will be empty, no data yet)
https://<your-domain>/admin         # admin panel — log in with ADMIN_PASSWORD
https://<your-domain>/docs          # Swagger UI
```

The `X-Request-ID` response header on every endpoint is a UUID4. If you supply your own via the request header, it'll be echoed back — useful for log correlation.

## Step 7 — Initial data ingestion

The app boots with an empty database. Two options:

### Option A: One-shot from the admin panel (easiest)

1. Visit `https://<your-domain>/admin`
2. Log in with the `ADMIN_PASSWORD` you set
3. Click **Start Full Refresh** — this calls `POST /api/anomalies/full-refresh` which:
   - Syncs all members from unitedstates.io
   - Syncs trades from QuiverQuant (skipped if no API key)
   - Wipes and regenerates anomalies
4. Watch the progress bar. First run takes 5–15 minutes depending on QuiverQuant rate limits.

### Option B: Set up the daily-update GitHub Action

The repo already has `.github/workflows/daily-update.yml` that runs the ingestion daily at 6am UTC. To make it land in the Railway database:

1. In your Railway Postgres service → **Connect** → copy the **Public URL** (the one starting with `postgres://...@<external-hostname>:<port>/...`). This is the externally-reachable connection string.
2. On GitHub, go to the repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
3. Add:
   - Name: `RAILWAY_DATABASE_URL`
   - Value: the public connection string from step 1
4. Optionally add `CONGRESS_GOV_API_KEY` and `QUIVERQUANT_API_KEY` as repo secrets too (the workflow references them).
5. Trigger the workflow manually the first time: **Actions** tab → **Daily Disclosure Update** → **Run workflow**. After that it runs daily.

## Step 8 — Operational checks

### Logs

Railway's **Logs** tab tails stdout. Each request emits one line like:

```
2026-05-09 14:22:01 - src.api.middleware - INFO - request method=GET path=/api/members status=200 elapsed_ms=42.3 request_id=4e3f1ab2c1d44a8...
```

`/health` hits are filtered out so the log is signal-heavy.

### Healthcheck

Railway pings `/health` on a schedule (configured in `railway.toml`). If the DB is down, `/health` returns 503 and Railway marks the deploy unhealthy.

### Restarting

**Settings** → **Restart**. Or push a no-op commit to `main` — Railway redeploys.

### Rolling back

**Deployments** tab → click any past green deploy → **Redeploy**.

---

## Costs

Rough estimate on Railway's pay-as-you-go pricing (~$5/mo per 500 MB RAM):

| Service | Resources | Approx. cost |
|---|---|---|
| App (uvicorn + 1 worker) | 512 MB RAM, ~0.1 vCPU | ~$5/mo |
| Postgres | 1 GB storage, 512 MB RAM | ~$5/mo |
| **Total** | | **~$10/mo** |

The $5 trial credit covers most of the first month.

---

## Troubleshooting

### `Invalid value for '--port': '$PORT' is not a valid integer`

This was a real bug in an earlier `railway.toml` and is fixed in current
versions. If you see it: `railway.toml` had a `startCommand` line that
Railway ran in exec form, so `$PORT` reached `uvicorn` as a literal string.
The fix is to **delete** the `startCommand` line entirely and let the
Dockerfile's `CMD ["sh", "-c", "exec uvicorn ... --port ${PORT}"]` handle
startup — the shell wrapper expands `$PORT` correctly.

### Build fails with `pip install` errors

Most often `lxml` or `pdfplumber` failing because system libs are missing. The `Dockerfile` already installs `libxml2-dev`, `libxslt1-dev`, `libpq-dev` in the build stage, so this shouldn't happen — but if you've forked and changed the Dockerfile, restore those `apt-get install` lines.

### App boots but `/health` returns 503

Database isn't reachable. Check:
1. The Postgres service is **Active** in Railway
2. The `DATABASE_URL` variable on the app service is set as a **reference** to the Postgres service (not a hardcoded string)
3. Migrations ran — check the deploy logs for `Running upgrade -> fa8667552e22, baseline schema`

### `RuntimeError: ADMIN_PASSWORD must be set when ENV=production`

Set `ADMIN_PASSWORD` in the app service **Variables**. The app intentionally refuses to start without one when `ENV=production` so the mutating admin endpoints don't silently 503.

### CORS errors in the browser console

`ALLOWED_ORIGINS` doesn't include the origin your browser is hitting from. Set it to the exact Railway domain (or comma-separated list). When `ALLOWED_ORIGINS=*`, credentials are disabled by spec — set it to a real domain for full functionality.

### "Document not found" on `/api/documents/...`

Expected on Railway. The filesystem is **ephemeral** — locally-stored PDFs don't survive redeploys. The `/api/disclosures` payload includes a `document_url` pointing at the original House Clerk / Senate eFD PDF, which clients should fetch directly. Persistent PDF storage on R2/S3 is on the roadmap.

### `/admin` page won't accept the password

- Confirm `ENV=production` is set (otherwise the app uses local-dev mode and the admin panel auto-authenticates from `localhost`, which won't apply on Railway).
- Confirm `ADMIN_PASSWORD` matches what you're typing — Railway's variable editor doesn't trim whitespace, so `password ` (trailing space) and `password` are different.

### Daily-update workflow runs but nothing changes in the deployed DB

`RAILWAY_DATABASE_URL` is wrong. It must be the **external** connection string (from Postgres service → **Connect** → **Public URL**), not the internal one Railway uses inside the project — GitHub Actions runs from outside Railway's private network.

---

## Reference: what the app expects

| Env var | Required | Source |
|---|---|---|
| `DATABASE_URL` | yes | Railway Postgres reference |
| `ENV` | yes (set to `production`) | manually |
| `ADMIN_PASSWORD` | yes (when `ENV=production`) | manually |
| `ALLOWED_ORIGINS` | recommended | manually, after Step 5 |
| `PORT` | auto-injected by Railway | — |
| `CONGRESS_GOV_API_KEY` | optional | <https://api.congress.gov/sign-up/> |
| `QUIVERQUANT_API_KEY` | optional | <https://www.quiverquant.com> |

Full list of tunable knobs lives in `src/config.py`.
