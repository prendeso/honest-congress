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
| `committees.py` | unitedstates.io `committee-membership` | Committee rosters, for the jurisdiction detectors. |
| `bills.py` | Congress.gov | Bills, sponsorships and committee referrals. Key in `CONGRESS_GOV_API_KEY`. |
| `fec.py` | api.open.fec.gov | Campaign donations. Key in `FEC_API_KEY` (api.data.gov, 1,000/hour). |
| `lda.py` | lda.senate.gov | Lobbying disclosures. Works anonymously (~15/min); `LDA_API_KEY` raises it to 120/min. |
| `usaspending.py` | api.usaspending.gov | Federal contract award *actions*. No key, no registration. |
| `sec_tickers.py` | SEC `company_tickers.json` | Company name → ticker. Every source above publishes names; only this turns them into something a trade can join to. |
| `sec_industries.py` | SEC EDGAR browse | SIC codes per ticker, feeding the sector taxonomy. |
| `rate_limit.py` | — | `RateLimiter` (count per rolling window, so bursts are free) and `ThrottledClient` (retries, `Retry-After`, per-run request budget). Shared by the four keyed clients. |
| `orchestrator.py` | — | Coordinates the above, downloads PDFs, dispatches parsing. The largest module in the tree and the least tested. |
| `_helpers.py`, `date_utils.py`, `base.py` | — | Pure functions and the shared ingester base, extracted for testability. |

Every source in that table is official and public domain. The vendor feed the
project started on is gone (D2), and the Tier-2 tables it used to fill are now
filled by FEC, the Senate LDA and USASpending directly.

Ingestion for those three is **demand-driven**: it starts from the tickers
members have actually traded and asks each source about those, rather than
walking the source's own universe. Asking USASpending for every federal award
would be millions of rows, almost none of them tradeable.

`ProPublicaClient` in `src/ingestion/__init__.py` is a backwards-compatibility
alias for `CongressGovClient`, not a ProPublica integration. ProPublica's
Congress API was deprecated.

### `src/parsing/` — extract structure from filings

`pdf_parser.py` and `ptr_parser.py` both try `pdfplumber.extract_tables()`
first and fall back to line-by-line regex. Table-first is the right instinct;
everything after it is brittle. Column identification sniffs header keywords
with hardcoded positional defaults, so a layout change can misassign columns.

`confidence.py` is what stops that being silent. Every parse now returns a
completeness ratio — rows read over rows that looked like records, times fields
extracted over fields required — stored on the filing as `parse_confidence`
with named `parse_warnings` beside it. `parsed` still means only that the
parser ran without raising; the score is what says whether it worked.

`has_text_layer` is recorded separately, because 12.7% of House PTRs are scans
of paper forms and a scan is a property of the document rather than a failure
of the parser (D13).

`tests/fixtures/ptr/` holds golden fixtures captured from real filings —
extracted tables and text, never the PDFs — and `tests/test_ptr_parser_golden.py`
pins the transactions, the direction of each one, and the survival of company
names that contain transaction keywords. Treat parser output as
lower-confidence than ingested XML, and read the score before trusting a
filing.

`fd_asset_parser.py` and `fd_income_parser.py` handle annual FD schedules and
run via `cli parse-fd`.

### `src/db/` — schema

Fourteen tables. SQLAlchemy 2.0 typed declarative (`Mapped[]`, `DeclarativeBase`),
Alembic for migrations. Postgres in production, SQLite locally and in tests;
`database.py` normalizes the `postgres://` URL scheme Railway and Heroku hand
out.

```
members ─┬─< disclosures ─┬─< assets
         │                ├─< transactions
         │                └─< liabilities
         └─< anomalies

members ──< committee_assignments

campaign_donations / lobbying_disclosures / government_contracts   (trigger events)
bills ─┬─< bill_sponsorships                                       (trigger events)
       └─< bill_committees
company_industries                                                 (ticker → SIC → sector)
```

The four Tier-2 event tables all carry `(source, external_id)`. The natural key
cannot do that job: Boeing's PAC gave the same committee $5,000 twice on
2024-12-31 — primary and general — and a natural key merged two real donations
into one.

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

Seventeen live anomaly types across eight modules. `persist_anomalies()` in
`src/analysis/__init__.py` is the chokepoint: it normalizes severity,
deduplicates, and drops any type listed in `DISABLED_ANOMALY_TYPES`.

| Module | Emits |
|---|---|
| `trade_analyzer.py` | `large_trade`, `late_filing`, `sector_concentration`, `high_trading_frequency` |
| `wealth_analyzer.py` | `excessive_wealth_growth` |
| `advanced_anomaly_detector.py` | `wealth_vs_salary`, `rapid_asset_appreciation`, `outperforming_trades`* |
| `extended_anomaly_detector.py` | `trade_clustering`, `volume_spikes`, `perfect_timing`*, `loss_avoidance`*, `multi_factor_risk` |
| `tier2_detectors.py` | `donor_conflict`, `lobbying_overlap`, `contract_front_run` |
| `legislation.py` | `sponsorship_conflict`, `bill_jurisdiction_conflict` |
| `committee_conflicts.py` | `committee_jurisdiction_conflict` |
| `clustering.py` | `cross_member_cluster` |

`*` disabled by default (D3) — dropped by `persist_anomalies`, never written,
never served, and not described anywhere a reader can see (D14).

The split that matters: **detectors that count things work; detectors that
claimed to infer profit or intent did not.** `late_filing` is a statutory
deadline in date arithmetic. `volume_spikes` computes a real standard
deviation. The disabled three asserted returns, success rates and
probabilities they never computed, and the price-based performance analyzer
that made the same class of claim on a live endpoint was deleted outright
(D10) — which is why nothing in the tree imports `yfinance` any more.

The event-window detectors — trades joined to donations, lobbying filings,
contract awards, sponsored bills and committee referrals by date window — are
the differentiated idea here. Their windows (90/30/30/30 days) are asserted
rather than calibrated, and they are the six types a null model exists for.

Three modules support the detectors rather than being ones:

- **`significance.py`** builds that null. For each (member, detector) pair it
  circular-shifts the member's whole trading calendar ~1,000 times, re-counts
  coincidences, and applies Benjamini–Hochberg across every test in the run.
  `NO_NULL_MODEL` lists the eleven magnitude detectors explicitly, so their
  absence from the correction is a decision on the record rather than an
  oversight (D11).
- **`baselines.py`** ranks each finding against others of its own type,
  reports which detectors could not run because their source table is empty,
  and summarises parse quality — a detector that silently returns zero is
  otherwise indistinguishable from one that ran and found nothing.
- **`sectors.py`** is the single sector taxonomy: a curated ticker map, CRS
  policy areas, and SIC prefixes from `company_industries`, in that precedence.
- **`catalog.py`** is what each detector means, in the form a reader gets it.
  Served at `/api/anomalies/types` and rendered by the dashboard, derived from
  `NO_NULL_MODEL` and the disabled-types setting so the site cannot describe a
  detector it does not run (D14).

`compliance.py` and `opacity.py` are the two least interpretive things the
project computes — filing lateness, and what share of a member's filings are
missing a ticker or an amount. Neither claims anything about intent, and
`opacity.py` bounds what every other detector can see: a member whose filings
cannot be read will look clean under all of them for reasons that have nothing
to do with their conduct.

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

Eight Jinja pages, Tailwind and Alpine from CDN, no build step. Sorting,
filtering and pagination are server-side.

The pages hold no copy of anything the API knows. The anomalies page renders
its legend, its type filter and every finding card from `/api/anomalies/types`,
after its own hand-kept copy drifted into advertising three deleted detectors
to visitors beside named members of Congress (D14). `tests/test_templates_read_served_fields.py`
fails when a page binds a field the API does not serve — the failure mode is
otherwise invisible, because a missing field is `undefined`, `|| 0` makes it a
zero, and the page renders a confident answer nobody computed.

## Operations

`Dockerfile` is two-stage on `python:3.11-slim`, drops build dependencies from
the runtime image, runs as non-root uid 10001, and health-checks `/health`. Its
`CMD` uses `sh -c` deliberately so `$PORT` expands — exec form passes it as the
literal string and uvicorn rejects it.

`railway.toml` runs `alembic upgrade head` as a pre-deploy step.
`.github/workflows/ci.yml` runs ruff, mypy (advisory, `continue-on-error`) and
pytest on every PR. `daily-update.yml` runs migrate → ingest → ingest-trades →
analyze nightly.

`/health` pings the database and returns 503 if it is unreachable;
`/health/live` always returns 200 for liveness probes.

## Known weak points

Ranked by how much they would cost to be wrong about:

1. **Senate coverage.** With the vendor feed gone, Senate trades come only from
   the `senate.py` scraper, which parses by column position and faces anti-bot
   protection. House coverage is official bulk XML and is not at risk this way.
2. **One House PTR in eight is a photograph.** 12.7% of 2024–25 House trade
   reports are scans with no text layer, so they are absent from every trade
   detector. A member who files on paper looks clean, and that is a limit of
   the record rather than a finding about them (D13).
3. **`orchestrator.py`** is the largest module and the least tested — the
   fetch → match → dedupe → store pipeline is barely covered.
4. **Asset identity** is matched on `description.lower().strip()`, so a wording
   change between filings invents a new asset and a phantom appreciation event.
5. **Liabilities are ingested and never read.** `wealth_analyzer` computes gross
   assets and calls it net worth.
6. **The event windows are asserted, not calibrated.** The FDR correction (D11)
   says a coincidence is unlikely by chance; it says nothing about whether 30
   days was the right window to have looked in.
