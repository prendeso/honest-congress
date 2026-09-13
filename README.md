# Honest Congress

A Python application that ingests US congressional financial disclosures
from public sources, parses them, and detects anomalies that may indicate
inconsistencies in reported wealth or trading activity.

## Features

- **Multi-source ingestion** — House Clerk XML, Senate eFD, congress-legislators,
  Congress.gov, and committee assignments from congress-legislators. All
  public-domain sources; no vendor data and no API key required to run.
- **PDF parsing** — assets, transactions, and liabilities extracted from
  disclosure documents (pdfplumber).
- **Anomaly detection** — detectors covering wealth vs. salary growth, rapid
  asset appreciation, late PTR filings, large trades, sector concentration,
  trade clustering, volume spikes, and donor / lobbying / contract conflict
  windows. Four detectors are disabled by default because their output is not
  defensible — see [docs/DECISIONS.md](docs/DECISIONS.md).
- **Web dashboard** — server-rendered Jinja2 templates + Alpine.js for
  members, disclosures, trades, parsed data, and anomalies.
- **STOCK Act compliance scoring** — per-member late-filing rates computed
  purely from filing dates. No inference, no thresholds to argue with.
- **Disclosure opacity index** — how legible each member's filings are, which
  bounds what every other detector can see.
- **REST API** — FastAPI; OpenAPI docs at `/docs`.

## Quickstart (local)

Requires Python 3.11+.

```bash
git clone https://github.com/prendeso/honest-congress.git
cd honest-congress

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env                 # edit if you want non-default knobs

python -m src.cli init               # alembic upgrade head
python -m src.cli ingest -y 2024 2025
python -m src.cli download-pdfs      # fetch filing PDFs
python -m src.cli parse              # PDFs -> transactions / assets
python -m src.cli sync-committees    # committee assignments (free, no key)

# Tier-2 trigger events. Run these AFTER `parse`: the FEC and LDA ingesters
# start from the tickers members have actually traded, so with an empty
# transactions table they have nothing to look up.
python -m src.cli ingest-contracts   # USASpending (free, no key)
python -m src.cli ingest-donations   # FEC (free key: api.data.gov/signup)
python -m src.cli ingest-lobbying    # Senate LDA (key optional)
python -m src.cli ingest-bills       # Congress.gov (free key: api.congress.gov/sign-up)
python -m src.cli sync-industries    # SEC industry codes -> sector classification

python -m src.cli analyze            # detectors, then FDR correction, then ranks
python -m src.cli stats              # what ran, and against how much data
python -m src.cli serve              # http://localhost:8000
```

### Data sources

Everything is an official, public-domain source. There is no licensed vendor
anywhere in the pipeline, which is what makes the output redistributable.

| Source | Feeds | Key |
|---|---|---|
| House Clerk bulk XML + PDFs | disclosures, transactions | none |
| `unitedstates/congress-legislators` | roster, committees, FEC candidate IDs | none |
| SEC `company_tickers.json` | company name -> ticker | none, but a contact email in `SEC_CONTACT_EMAIL` (SEC returns 403 without one) |
| USASpending | `government_contracts` | none |
| FEC | `campaign_donations` | free, [api.data.gov](https://api.data.gov/signup/) — 1,000 requests/hour |
| Senate LDA | `lobbying_disclosures` | optional, [lda.senate.gov](https://lda.senate.gov/api/register/) — raises ~15 req/min to ~120 |
| Congress.gov | `bills`, `bill_sponsorships`, `bill_committees` | free, [api.congress.gov](https://api.congress.gov/sign-up/) — 20,000 requests/hour |
| SEC EDGAR | `company_industries` | none, same `SEC_CONTACT_EMAIL` |

`ingest-donations` costs more requests than one FEC hour allows, so it caps
itself and resumes: rerunning skips the PACs already stored. Pass
`--max-requests` to cap it explicitly.

`ingest-bills` is what lets the analysis ask whether a member acted on an
issuer's industry in office, not just whether they traded it. Sponsorship costs
about one page per member; cosponsorship is roughly twenty times larger and no
detector reads it yet, so `--sponsored-only` is what the nightly job runs.
Committee referrals cost one request per bill, so they are fetched only for
bills that already matched a member's trading — on real data that narrowed
10,980 bills to 26.

`sync-industries` is what lets any of the sector-based detectors see past
mega-caps. Three of them — `sponsorship_conflict`, `bill_jurisdiction_conflict`
and `committee_jurisdiction_conflict` — join a bill or a committee remit to a
*sector*, and the answer used to come from a hand-written list of about 70
large-cap tickers. Measured on 300 unselected SEC-registered tickers, that list
classifies 4 of them; the industry codes classify 156. It is incremental, so
only new tickers cost a request.

`/health` returns 503 if the database is unreachable; `/health/live`
always returns 200 and is intended for liveness probes.

## Deploying to Railway

The repo ships with a `Dockerfile` and a `railway.toml` that Railway
auto-detects. See **[docs/RAILWAY_DEPLOY.md](docs/RAILWAY_DEPLOY.md)** for the
full step-by-step guide. Short version:

1. **Create a Railway project** from the GitHub repo, attach the **Postgres**
   plugin, and reference `DATABASE_URL` on the app service.
2. **Set env vars**: `ENV=production`, `ADMIN_PASSWORD=<strong random>`,
   `ALLOWED_ORIGINS=https://<your-railway-domain>`. Optional:
   `CONGRESS_GOV_API_KEY`.
3. **Push to `main`.** Railway builds the Docker image, runs
   `alembic upgrade head` as the pre-deploy step, then starts uvicorn on
   `$PORT`.
4. **Run the daily-update GitHub Action** by adding `RAILWAY_DATABASE_URL`
   (the external connection string from Railway) as a repo secret so the
   cron job lands ingestion rows in the deployed database.

## Architecture

```
honest-congress/
├── src/
│   ├── analysis/         anomaly detectors (wealth, trade, advanced, extended)
│   ├── api/
│   │   ├── main.py       FastAPI app + lifespan
│   │   ├── middleware.py request-ID + access logging
│   │   ├── routes/
│   │   │   ├── anomalies/  package: query/detect/admin
│   │   │   ├── members.py
│   │   │   ├── disclosures.py
│   │   │   ├── dashboard_v2.py  thin handlers returning TemplateResponse
│   │   │   └── ...
│   │   └── templating.py
│   ├── templates/        Jinja2 templates (one per page + base + partials)
│   ├── ingestion/        source-specific scrapers + orchestrator
│   ├── parsing/          PDF / XML extractors
│   ├── db/               SQLAlchemy models + engine + URL normalization
│   └── cli.py            init / ingest / analyze / serve / parse / ...
├── alembic/              migrations (autogenerated baseline)
├── tests/                pytest suite
├── Dockerfile, railway.toml, pyproject.toml, requirements.txt
└── docs/                 DECISIONS.md + the Railway deploy guide
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the deeper design dive.

## Anomaly types

| Type | Description |
|------|-------------|
| `excessive_wealth_growth` | Year-over-year net worth grew far above salary contribution |
| `wealth_vs_salary` | Lifetime wealth growth exceeds cumulative salary by >2x |
| `rapid_asset_appreciation` | Single asset grew >100% in one year (or >500% annualized) |
| `large_trade` | Single transaction over $1M |
| `late_filing` | PTR filed >60 days late on a >=$50k transaction |
| `sector_concentration` | >50% of trades in one regulated sector in a disclosure year |
| `high_trading_frequency` | >10 trades in a single month |
| `trade_clustering` | 5+ consecutive same-direction trades |
| `volume_spikes` | 2+ trades exceeding 3 standard deviations of typical size |
| `multi_factor_risk` | Member shows 3+ different anomaly types |
| `donor_conflict` | Trade shortly after a corporate donation |
| `lobbying_overlap` | Trade overlapping a lobbying filing window |
| `contract_front_run` | Trade ahead of a government contract award |
| `committee_jurisdiction_conflict` | Traded a sector overseen by a committee the member sits on |
| `cross_member_cluster` | Several members traded the same ticker, same direction, same window |

Thresholds for `late_filing` are tunable via `LATE_FILING_MIN_DAYS` and
`LATE_FILING_MIN_AMOUNT_USD` in the environment.

### Disabled detectors

`outperforming_trades`, `perfect_timing` and `loss_avoidance` are disabled by
default via `DISABLED_ANOMALY_TYPES`. Their output could not be supported by
the data available — `loss_avoidance`, for example, was arithmetically incapable
of returning anything other than 100%. [docs/DECISIONS.md](docs/DECISIONS.md)
records why each is off and what re-enabling requires. To remove rows written
before they were disabled:

```bash
python -m src.cli purge-disabled --dry-run   # preview
python -m src.cli purge-disabled             # apply
```

## Parse confidence

Every trade in this project comes out of a PDF, so the parser is the single
point of failure for the whole dataset. `parsed` has only ever meant the parser
ran without raising — a filing that yielded nothing was recorded identically to
one read cleanly.

Each filing now carries `parse_confidence` (0–1) and `parse_warnings`. The score
is a completeness ratio, not a weighted judgement:

```
confidence = (rows_parsed / rows_detected) × (fields_extracted / fields_expected)
```

A dropped row lowers it by arithmetic; a transaction missing its amount lowers
it by arithmetic. Three caps are asserted and marked as such in the code: a
scanned PDF and a PTR with no transactions both score 0, and a filing read
through the text fallback is capped at 0.7.

`GET /api/disclosures/?max_confidence=0.5` finds the filings that parsed badly —
the low end is the useful query. **Nothing is excluded from analysis on the
strength of the score.** A missing trade is already invisible, and dropping the
ones known to be shaky would compound that silently.

`python -m src.cli parse --min-confidence 0.8` re-reads the filings the parser
did worst on, and filings never scored at all.

### One House trade report in eight is a photograph

`has_text_layer` is recorded separately from the score, because a scan of a
paper form is a property of the document and not a failure of the parser.
**123 of the 966 House PTRs filed in 2024–25 — 12.7% — have no text layer at
all**, and a 7-digit document ID predicted it with 100% accuracy across the 200
sampled. Nothing in them is readable without OCR, which this project does not
do.

They score 0.0, which is honest, but counting them as parse failures overstates
the parser's failure rate roughly eightfold and hides the coverage statement
that matters. So `parse_quality_summary` reports `filings_that_yielded_nothing`
(had text, read nothing — a bug) apart from `filings_with_no_text_layer` (a
scan — a limit), `--min-confidence` re-parses skip the scans, and
`GET /api/disclosures/?has_text_layer=false` lists them.

### What building it found

pdfplumber sometimes collapses an entire table row into its first cell. Read by
column index that looks like an empty row, and it was dropped silently. Across
the six real filings in the test corpus that was **18 transactions lost against
16 kept**, with two filings parsing to nothing at all while recorded as parsed
successfully. The corpus now yields 34.

Scaling that corpus to **200 real PTRs** sampled across filers found three more
classes, and two of them were recording trades backwards:

- `"Best Buy Co., Inc. Common Stock S"` parsed as a **purchase**, because "Buy"
  is in the company name. The Transaction Type cell `"S (partial)"` parsed as a
  **purchase**, because a substring test found the "p" inside "partial". Both
  recorded a disclosed sale as a purchase, and direction is not cosmetic:
  `contract_front_run` only inspects purchases, and the cross-member cluster
  detector groups by it.
- 107 rows were counted as unread transactions that were nothing of the kind —
  a wrapped `Cap. Gains > $200?` header and the footnote block under each
  record — which marked clean filings as bad.
- 60 of the 200 filings had a transaction with no readable amount, because a
  wrapped amount band arrives with the asset name wrapped alongside it.

Over the same 200 filings, mean confidence went **0.854 → 0.902**, filings
scoring ≥0.99 went **108 → 178**, and filings below 0.8 went **23 → 2**, with
one net transaction removed: a phantom built from an asset-class code beside
the wrapped half of an amount.

## Multiple comparisons

Seventeen detectors run against every member, so some of what gets flagged is
what running thousands of tests over hundreds of people produces. Six of them ask a
*timing* question — donations, lobbying filings, contract awards, bill
sponsorship, committee referrals, cross-member clusters — and those have a
well-posed null: the same trades, the same events, no relationship between them.

`analyze` tests each (member, detector) pair against that null by circular-
shifting the member's whole trading calendar ~1,000 times and re-counting
coincidences, then applies Benjamini–Hochberg across every test in the run.
Findings carry `p_value` and `q_value`.

Shifting rather than resampling is deliberate: disclosed trades arrive in
same-day PTR batches, and a null that scattered them would make ordinary
clustering look extraordinary.

The other eleven detectors measure a magnitude (`sector_concentration`,
`late_filing`, …). There is no coincidence to destroy, so **they carry no
q-value at all** — `has_null_model: false` in the API. A null `q_value` means
*no null model exists*, never *passed one*. They keep `percentile_rank`, which
is the right tool for a magnitude.

`GET /api/anomalies/` hides findings that failed correction by default; pass
`include_below_fdr=true` to see them. Untested findings are always returned.
`python -m src.cli significance --alpha 0.1` re-runs the correction at a
different rate without re-detecting anything.

What surviving this means: the timing alignment is unlikely by chance. Not that
the member acted on anything, and not that the detector's threshold is
calibrated.

`GET /api/anomalies/types` serves what each detector looks for, what it
**cannot** show, and whether it carries a q-value at all — read from the same
declarations the detectors are registered in, so a detector that is disabled
cannot still be described to a reader. The dashboard renders that rather than
keeping its own copy; see D14 for what its own copy had drifted into saying.

## API endpoints

```
GET  /health                       readiness probe (DB ping)
GET  /health/live                  liveness probe
GET  /api/members                  paginated list with filters and sort
GET  /api/members/{id}             member detail + recent disclosures
GET  /api/disclosures              paginated list with filters
GET  /api/anomalies/               paginated anomalies (severity-ordered);
                                   ?min_percentile=N for the strongest findings
GET  /api/anomalies/summary        counts by type / severity / party / chamber
GET  /api/anomalies/types          what each detector looks for and cannot show
GET  /api/anomalies/{id}           detail
POST /api/anomalies/admin/login    issue token; required for mutating routes
POST /api/anomalies/analyze        run full detector suite
POST /api/anomalies/regenerate     wipe + recompute all anomalies
GET  /api/insights                 dashboard hero stats
GET  /api/compliance/              members ranked by STOCK Act filing punctuality
GET  /api/compliance/{member_id}   one member's filing record
GET  /api/compliance/opacity/      members ranked by disclosure legibility
GET  /api/compliance/opacity/{id}  one member's legibility breakdown
GET  /docs                         Swagger UI
```

Filing punctuality and disclosure legibility are also a page at `/compliance`.
They are the two least interpretive things here — one is the subtraction of two
dates that both appear on the filing, the other counts what could not be read —
and for a long time neither was reachable except through the API.

Every response carries an `X-Request-ID` header (echoed if you supply one,
otherwise auto-generated) so logs can be correlated.

## Starting over

`reset` deletes every row and rebuilds the schema from scratch. It is
irreversible, so it is a dry run unless you pass `--yes`, and it refuses
outright when `ENV=production` unless you also pass `--force-production`.

```bash
python -m src.cli reset                 # report what exists; change nothing
python -m src.cli reset --yes           # wipe + rebuild schema
python -m src.cli reset --yes --purge-pdfs   # also delete data/disclosures/
```

Downloaded PDFs are kept by default — re-fetching thousands of files is slow
and hard on House Clerk. After a reset, rebuild with the Quickstart sequence
above, in that order: the Tier-2 ingesters read the transactions table to
decide what to fetch, so running them before `parse` fetches nothing.

`stats` is the check that the rebuild worked. It names any detector whose
source table is empty, which is the difference between "found nothing" and
"never ran".

Most of the time you do not want `reset`. After a parser change, what you
actually want is to re-read the filings without destroying anything:

```bash
python -m src.cli purge-disabled            # drop findings from disabled detectors
python -m src.cli parse --min-confidence 1.0  # re-read every filing not read perfectly
python -m src.cli analyze                   # recompute against the corrected data
```

`purge-disabled` matters because disabling a detector only stops it *writing* —
findings it already persisted stay in the database and keep being served.

In CI, the **Rebuild** workflow (`.github/workflows/rebuild.yml`) runs that
sequence against the deployed database: `workflow_dispatch` only, guarded by a
`confirm` input, chunked by a `limit` because a runner is capped at six hours.
Re-dispatch it until the parse step reports nothing left. The nightly job
cannot do this — it never runs `parse`, since a runner starts from a fresh
checkout and the PDF corpus does not persist.

## Development

```bash
pip install -e ".[dev]"
pre-commit install     # ruff + format + trailing-whitespace + check-yaml

ruff check .           # lint
ruff format .          # format
mypy src               # types (non-strict, advisory)
pytest
pytest --cov=src       # coverage report
```

CI on every PR runs lint → typecheck → tests via
`.github/workflows/ci.yml`.

## License

MIT — see [LICENSE](LICENSE).
