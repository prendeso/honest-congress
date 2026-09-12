# Architecture

How Honest Congress is put together, and why. For *what* was decided and why
it stands, see [docs/DECISIONS.md](docs/DECISIONS.md).

---

## Shape

```
official sources ──► ingestion ──► parsing ──► Postgres/SQLite ──► analysis ──► API + dashboard
```

Ingestion and analysis are batch jobs driven by `src/cli.py` (and a nightly
GitHub Actions workflow). The API only reads. That separation matters: read
endpoints used to trigger writes, which cost three COUNT queries per request
and — because nothing in that path committed — wrote nothing at all.

## Layers

### `src/ingestion/` — fetch and upsert

| Module | Source | Notes |
|---|---|---|
| `house.py` | House Clerk `{year}FD.xml` / `{year}PTR.xml` | Primary. Official bulk XML plus PDF links. |
| `house_clerk_historical.py` | House Clerk, 2004+ | Historical backfill. Overlaps `house.py`; a merge candidate. |
| `senate.py` | efdsearch.senate.gov AJAX | CSRF handshake then a DataTables JSON endpoint. Parses by column position, so it is fragile. Anti-bot protection is a live risk. |
| `congress_gov.py` | unitedstates.io `congress-legislators`, Congress.gov | Member roster. The GitHub dataset is primary and needs no key; Congress.gov is the fallback. |
| `quiverquant.py` | api.quiverquant.com | Trades. Slated for replacement — see D1/D2. |
| `quiverquant_extras.py` | QuiverQuant Tier-2 | Donations, lobbying, contracts. One request per ticker; wants batching. |
| `orchestrator.py` | — | Coordinates the above, downloads PDFs, dispatches parsing. The largest module in the tree and the least tested. |
| `_helpers.py`, `date_utils.py` | — | Pure functions extracted for testability. |

`ProPublicaClient` in `src/ingestion/__init__.py` is a backwards-compatibility
alias for `CongressGovClient`, not a ProPublica integration. ProPublica's
Congress API was deprecated.

### `src/parsing/` — extract structure from filings

`pdf_parser.py` and `ptr_parser.py` both try `pdfplumber.extract_tables()`
first and fall back to line-by-line regex. Table-first is the right instinct;
everything after it is brittle. Column identification sniffs header keywords
with hardcoded positional defaults, so a layout change silently misassigns
columns and the parse still records `parsed=True`. There is no confidence
score and no golden-file corpus. Treat parser output as lower-confidence than
ingested XML.

`fd_asset_parser.py` and `fd_income_parser.py` handle annual FD schedules and
run via `cli parse-fd`.

### `src/db/` — schema

Nine tables. SQLAlchemy 2.0 typed declarative (`Mapped[]`, `DeclarativeBase`),
Alembic for migrations. Postgres in production, SQLite locally and in tests;
`database.py` normalizes the `postgres://` URL scheme Railway and Heroku hand
out.

```
members ─┬─< disclosures ─┬─< assets
         │                ├─< transactions
         │                └─< liabilities
         └─< anomalies

campaign_donations / lobbying_disclosures / government_contracts   (trigger events)
```

Two things about this schema are load-bearing:

**Money is a range, never a point.** `amount_min` / `amount_max` and
`value_min` / `value_max` are `Numeric(15, 2)` pairs because STOCK Act filings
report bands ($1,001–$15,000), not figures. Collapsing a band to its midpoint
and presenting the result as fact is the single easiest way to make this
project dishonest. Filings also carry no share counts.

**`Member.disclosure_count` / `anomaly_count` are denormalized** so the members
API can sort and filter without a per-row subquery. They are maintained *only*
by `recalculate_member_counts()` in `db/utils.py`, which runs after every
ingest, analyze and purge. Nothing else may write them.

`Anomaly.severity` is free text normalized to `low` / `medium` / `high` by a
model-level `@validates` hook — on the model rather than in `persist_anomalies`
because `TradeAnalyzer` and `WealthAnalyzer` construct `Anomaly()` directly.
`(member_id, anomaly_type, title)` is a unique index, declared both on the model
and in migration `c3a7f1d92b04` so `create_all` and Alembic agree.

### `src/analysis/` — detectors

Sixteen anomaly types across five modules. `persist_anomalies()` in
`src/analysis/__init__.py` is the chokepoint: it normalizes severity,
deduplicates, and drops any type listed in `DISABLED_ANOMALY_TYPES`.

| Module | Emits |
|---|---|
| `trade_analyzer.py` | `large_trade`, `late_filing`, `sector_concentration`, `high_trading_frequency` |
| `wealth_analyzer.py` | `excessive_wealth_growth` |
| `advanced_anomaly_detector.py` | `wealth_vs_salary`, `rapid_asset_appreciation`, `outperforming_trades`* |
| `extended_anomaly_detector.py` | `trade_clustering`, `volume_spikes`, `perfect_timing`*, `loss_avoidance`*, `multi_factor_risk` |
| `tier2_detectors.py` | `donor_conflict`, `lobbying_overlap`, `contract_front_run` |

`*` disabled by default (D3).

The split that matters: **detectors that count things work; detectors that
claimed to infer profit or intent did not.** `late_filing` is a statutory
deadline in date arithmetic. `volume_spikes` computes a real standard
deviation. The disabled three asserted returns, success rates and
probabilities they never computed.

`tier2_detectors.py` is the cleanest module and the only genuinely
differentiated idea here: trades joined to donation, lobbying and contract
events by date window. Its windows (90/30/30 days) are asserted rather than
calibrated, and there is no base-rate correction (D6).

`performance_analyzer.py` is the only module touching real prices (`yfinance`).
It is not wired into anomaly detection.

Two analyzers (`TradeAnalyzer.analyze_all_members`,
`WealthAnalyzer.analyze_all_members`) write `Anomaly()` rows directly instead
of going through `persist_anomalies`. The disabled-type guard is repeated in
both; consolidating them onto `persist_anomalies` would remove that duplication.

### `src/api/` — HTTP

`main.py` is thin: app setup, CORS from env, request-ID middleware, router
includes. Routers split by resource; `anomalies/` is further decomposed into
`_shared` (schemas), `query` (read), `detect` (run detectors), `admin`
(mutating, behind `require_admin`).

`dashboard_v2.py` is a misnomer — there is no v1. It was ~1,800 lines of HTML
inside Python f-strings before the templates were extracted; the name is all
that survives.

Admin auth issues process-local bearer tokens with an 8-hour TTL. That does not
survive a restart or span multiple workers — fine for a single-instance admin
panel, inadequate for the metered API the project is heading toward.

### `src/templates/` — dashboard

Seven Jinja pages, Tailwind and Alpine from CDN, no build step. Sorting,
filtering and pagination are server-side. Note that `base.html` uses `@apply`,
which is a build-time Tailwind feature and silently does nothing under the
play CDN — those utility classes are unstyled.

## Operations

`Dockerfile` is two-stage on `python:3.12-slim`, drops build dependencies from
the runtime image, runs as non-root uid 10001, and health-checks `/health`. Its
`CMD` uses `sh -c` deliberately so `$PORT` expands — exec form passes it as the
literal string and uvicorn rejects it.

Note the Dockerfile builds on 3.12 while CI and `target-version` are 3.11, so
nothing is tested on what ships.

`railway.toml` runs `alembic upgrade head` as a pre-deploy step.
`.github/workflows/ci.yml` runs ruff, mypy (advisory, `continue-on-error`) and
pytest on every PR. `daily-update.yml` runs migrate → ingest → ingest-trades →
analyze nightly.

`/health` pings the database and returns 503 if it is unreachable;
`/health/live` always returns 200 for liveness probes.

## Known weak points

Ranked by how much they would cost to be wrong about:

1. **Senate coverage** depends on QuiverQuant, which is being removed (D2). The
   `senate.py` scraper parses by column position and faces anti-bot protection.
2. **`orchestrator.py`** is the largest module and the least tested — the
   fetch → match → dedupe → store pipeline is barely covered.
3. **Parser confidence.** A structurally wrong parse records `parsed=True`.
4. **Asset identity** is matched on `description.lower().strip()`, so a wording
   change between filings invents a new asset and a phantom appreciation event.
5. **Liabilities are ingested and never read.** `wealth_analyzer` computes gross
   assets and calls it net worth.
6. **No base rates or multiple-comparisons control** across ~8,800 detector runs
   (D6).
