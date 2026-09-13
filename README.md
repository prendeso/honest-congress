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

## Multiple comparisons

Sixteen detectors run against every member, so some of what gets flagged is what
running thousands of tests over hundreds of people produces. Six of them ask a
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

The other ten detectors measure a magnitude (`sector_concentration`,
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
GET  /api/anomalies/{id}           detail
POST /api/anomalies/admin/login    issue token; required for mutating routes
POST /api/anomalies/analyze        run full detector suite
POST /api/anomalies/regenerate     wipe + recompute all anomalies
GET  /api/insights                 dashboard hero stats
GET  /api/compliance/              members ranked by STOCK Act filing punctuality
GET  /api/compliance/{member_id}   one member's filing record
GET  /api/compliance/opacity/      members ranked by disclosure legibility
GET  /docs                         Swagger UI
```

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
