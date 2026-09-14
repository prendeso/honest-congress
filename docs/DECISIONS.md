# Decisions

Standing decisions about data sources, detectors, and architecture. This file
replaces the `PHASE_7_*` / `PHASE_8_*` / `PHASE_9_*` set and the four
overlapping source-inventory documents, which were conversation transcripts
rather than documentation — several ended by asking the reader to choose an
option, and the choice was never recorded.

---

## D1. Data must be redistributable

**Decision:** every source must be public domain or carry explicit
redistribution rights.

The project is intended as a public site and a paid API endpoint. That makes
licensing a blocking constraint rather than a cost question, and it settles the
fork that `PHASE_9_DECISION.md` left open. That fork was always framed as
build-vs-buy — $10/month against five hours of scraping — and re-litigated
across four documents on that axis. It was the wrong axis. For a commercial
product the official path is not a cost optimization; it is the only viable
architecture.

US federal government data is public domain with no redistribution limit, so
the official sources are also strictly better for this purpose than the vendor
feeds they replace.

**Status:** accepted, and the migration is finished — every row in D2 is now
done or deliberately dropped. No vendor feed remains.

## D2. Replace vendor feeds with official sources

| Need | Current | Replacement | Status |
|---|---|---|---|
| House trades | House Clerk PTR XML | — already official | done |
| Member roster | unitedstates.io congress-legislators | — already official | done |
| Committee assignments | none (detector had an empty table) | congress-legislators `committee-membership-current.yaml` | **done** |
| Campaign donations | FEC API (`src/ingestion/fec.py`) | — | **done** |
| Lobbying | Senate LDA API (`src/ingestion/lda.py`) | — | **done** |
| Gov contracts | USASpending (`src/ingestion/usaspending.py`) | — | **done** |
| Bills and sponsorship | Congress.gov (`src/ingestion/bills.py`) | — | **done** |
| Company → sector | SEC EDGAR industry codes (`src/ingestion/sec_industries.py`) | — | **done** |
| Senate trades | — (vendor removed) | Senate eFD (`src/ingestion/senate.py`) | **done** |
| Benchmark prices | — | dropped, see D10 | **dropped** |

The Tier-2 detector logic in `src/analysis/tier2_detectors.py` does not change;
only its feed does.

**QuiverQuant has been removed**, and its key rotated. Its terms appear to limit
use to personal, non-commercial purposes and to prohibit redistribution without
an executed agreement, which is incompatible with a public site or a paid
endpoint.

Two consequences worth stating plainly, because neither is fixed by deleting
the client:

- **Senate trades no longer depend on the vendor.** This entry used to say
  coverage was zero and that nothing had shown the scraper survives eFD's
  protections or that `ptr_parser` handles the Senate layout. Both questions are
  now answered, and the second was the wrong question: eFD serves **HTML**, not
  PDF, so `ptr_parser` never applies — `src/parsing/senate_html_parser.py` reads
  the filing's one named-column table, subclassing `PTRParser` so amount bands
  and "Sale (Partial)" are not implemented twice.

  Re-checked live against efdsearch.senate.gov on 2026-09-14, end to end: the
  prohibition-agreement POST establishes a session, a paginated search returns
  123 Senate trade reports for 2026, a document fetch returns the filing rather
  than the agreement page, and its table headers are exactly the ones
  `_COLUMN_ALIASES` expects. `sync_senate_disclosures` is called from
  `run_ingestion` for every year in the sweep, so this is wired in rather than
  merely reachable.

  What this does **not** claim is freshness. Senate filings become transactions
  on the same cadence as House ones — when something runs `cli parse` — and the
  House path's `Member.chamber == HOUSE` filter is unrelated to it.
- **The three Tier-2 tables now have ingesters** and all three detectors emit.
  `detection_summary()` — and `cli stats` — still names any detector whose
  source table is empty, so a silent zero stays distinguishable from a clean
  result.

**`yfinance`** scraped Yahoo Finance through an unofficial library against
Yahoo's terms. Acceptable for a hobby project, not for a paid endpoint. It is
gone, along with its only caller — see D10.

## D3. Three detectors are disabled, not merely untuned

Disabled by default via `DISABLED_ANOMALY_TYPES`, enforced in
`persist_anomalies()` so no write path can bypass it:

| Detector | Why |
|---|---|
| `loss_avoidance` | Increments numerator and denominator on the same branch, so its rate is always exactly 1.0 and the `> 0.8` gate is always true. Every member with more than five buy-before-sell pairs was labelled HIGH severity. |
| `perfect_timing` | Never reads a price. Counts `(buy, sell)` date pairs in a nested loop and divides by `len(buys)`, so rates exceed 100%. Its description asserted a `<1%` chance probability that was never computed. |
| `outperforming_trades` | Calls `(sells - buys) / buys` a "return" with no position matching, against a hardcoded flat 10% benchmark. |

`committee_conflicts` has been **rebuilt rather than disabled**. The original
used no committee data at all (`SAMPLE_COMMITTEE_ASSIGNMENTS` was an empty dict)
and substring-matched tickers, so `"ba"` matched "Alibaba"; it also emitted its
findings as `sector_concentration`, colliding with `TradeAnalyzer`'s unrelated
detector of the same name.

`src/analysis/committee_conflicts.py` replaces it, joining real assignments from
`committee_assignments` to trades classified by `src/analysis/sectors.py`. It
emits `committee_jurisdiction_conflict`. Tickers are now matched exactly and
name keywords on word boundaries, so `"ba"` matches Boeing and nothing else.

`src/analysis/sectors.py` is also the single sector taxonomy, replacing four
tables that had drifted apart on both sector names and membership.

These attached ethics-investigation language to named public officials and ran
nightly.

Rows written before the gate existed are removed by `python -m src.cli
purge-disabled`.

## D4. Price data is deferred, not required

Only 3 of 16 anomaly types need prices, and they are the three in D3. Everything
else — including all three Tier-2 conflict detectors, STOCK Act late-filing,
large-trade, volume-spike, clustering, and the wealth-growth detectors, which
use the member's own disclosed asset values — runs on disclosure metadata alone.

Shipping without price data means shipping what no competitor sells
(conflict-of-interest event windows, compliance tracking) and skipping what
several already give away free (return calculations).

When it is wanted, this is end-of-day closes rather than real-time quotes — the
cheapest and least restricted category of market data, roughly $20–30/month.
The price-based performance analyzer that used to sit here has been deleted
rather than left waiting for that data; see D10 for why.

**A constraint that does not go away with better data:** disclosures report
amount *bands* and no share counts. Per-trade percentage return is computable
cleanly, since percentage return does not depend on position size. Any
portfolio-level "beat the market by X%" claim needs position weights that do not
exist in the filings — resting on band midpoints with up to 15x uncertainty on
the smallest band ($1,001–$15,000). Scope any rebuild to per-trade analysis.

## D5. Report ranges, not invented precision

Disclosures report value bands. Any point estimate derived from them is
fabricated precision, and every detector should publish the bounds it reasoned
over.

This was decided once already, in what is now
`docs/archive/2026-pre-modernization/VAGUE_LANGUAGE_IMPLEMENTATION.md`:

> **Before:** `Wealth growth of 234.5% exceeds salary-based expectation`
> **After:** `Wealth growth dramatically (200-500%) exceeds salary-based expectation`
> **Rationale:** Net worth calculations use min/max ranges, so exact figures are misleading

It is the only thing in the project that earns the word *honest*, and it is the
one position competitors do not occupy. It belongs at the centre of the product,
not in an archive folder.

## D6. Thresholds should be base rates, not assertions

Every threshold in the codebase is currently asserted rather than calibrated:
`>100%` appreciation, `>50%` sector concentration, `5+` consecutive trades, `3σ`
volume, the `90/30/30`-day Tier-2 windows.

Computing the distribution across all members and flagging percentile outliers
requires no new data and converts "arbitrary threshold" into "top 1% of
members". Related: 16 detectors across ~550 members is roughly 8,800 tests, with
no multiple-comparisons control — so some members are flagged spuriously by
construction.

**Status:** partially implemented.

`src/analysis/baselines.py` ranks every finding against others of **its own
type** — never across types, since dollars and days share no scale — and stores
the result on `Anomaly.percentile_rank`. `GET /api/anomalies/?min_percentile=95`
asks for the strongest findings in a way the raw thresholds cannot support.
Populations under 10 findings are left unranked rather than given a number that
would make the largest of three "the 100th percentile".

`detection_summary()` (also `python -m src.cli stats`) reports findings
alongside the number of detector-member tests that produced them, so a long list
is not mistaken for a long list of wrongdoing.

**Still outstanding:** the detectors continue to *fire* on their asserted
thresholds; percentile rank is an annotation on the output, not the trigger.
Formal FDR control is now implemented for the six detectors that admit a null
model — see D11.

## D7. Compliance scoring is the flagship

`src/analysis/compliance.py` computes per-member STOCK Act late-filing rates —
`GET /api/compliance/`, or `python -m src.cli compliance`.

It is the least interpretive thing the project does: subtraction between two
dates that both appear on the filing. It asserts nothing about intent, timing,
profit or conflict, so there is no threshold to argue with and no methodology to
defend. That is the point. Every other detector here can be dismissed as
speculative; this one cannot.

It also fills a real gap — the existing trackers publish trades, not compliance.

Two deliberate choices. The deadline is 45 days after the transaction, not the
30-day awareness rule: awareness dates are not disclosed, so 45 is the only
deadline the public record supports. And unlike the `late_filing` detector —
which filters to materially large trades to keep the anomaly table readable — a
compliance *rate* counts every covered transaction, or it is not a rate.

## D8. Co-movement beats individual timing

`src/analysis/clustering.py` finds tickers that several members traded the same
way inside a short window, emitting `cross_member_cluster`.

It exists because individual timing is the question this data cannot answer:
disclosures give amount bands and no share counts, and without price history
there is no return to measure — which is precisely why `perfect_timing` and
`loss_avoidance` had to be disabled (D3). Co-movement needs none of that. Dates,
tickers and directions are all disclosed exactly.

It is also the better question. One member buying a defense stock is
unremarkable; six buying it the same week is worth showing a reader.

The filter is **concentration**, not popularity: what share of everyone who ever
traded that ticker did so inside the window. Four of a ticker's forty traders
overlapping is the base rate; six of its six is a burst. An earlier draft
suppressed on raw popularity instead and was wrong in an instructive way — in
any population the largest, most interesting clusters also involve the most
members, so that rule penalised exactly what it should surface.

Findings state that filing dates are not trade dates and that disclosure lags
vary, so this is co-movement in *reported* activity, not evidence of
coordination.

## D9. Publish how legible the filings are

`src/analysis/opacity.py` scores each member on what share of their filings are
missing a ticker, describe a holding as "various", carry an unreadable amount,
or failed to parse at all. `GET /api/compliance/opacity/`.

It measures the filings, not the filer. House and Senate systems accept free
text, scanned documents and handwriting, so a high score often reflects the
filing system rather than any choice by the member, and the response says so.

It earns its place because it **bounds every other number here**. A member whose
filings cannot be read will show few findings for reasons that have nothing to
do with their trading, and a reader who sees an empty flag list should know
whether that means "nothing found" or "nothing readable". Publishing the second
number alongside the first is the difference between a transparency tool and a
scoreboard.

This is D5 turned on the project itself: honest about the limits of its own
inputs.

## D10. No returns without prices, and no prices we have

`src/analysis/performance_analyzer.py` is deleted, along with
`/api/performance` and `cli performance`. It claimed to identify "statistically
significant outperformers" and published an `alpha_vs_sp500` per member.

It could not have done either. STOCK Act filings disclose **amount bands and no
share counts**, which is why `outperforming_trades`, `perfect_timing` and
`loss_avoidance` are disabled (D3). This module made the same mistake with more
machinery:

* **Realized gains never touched a price.** `gain = sell_value - buy_value`,
  where both are band midpoints. Selling a $15,001-50,000 position bought at
  $1,001-15,000 reported a $24,500 "gain" -- the distance between two bands. A
  member who sold at a loss could show a large one.
* **`shares = amount / price` invented a quantity** the filing does not
  disclose, and unrealized gains multiplied that invention by today's price.
* **FIFO matching was quantity-blind**, popping one buy per sale regardless of
  size.
* No statistical test existed anywhere in the file.

It was deleted rather than disabled because the three detectors run inside a
suite whose output is persisted, so gating them was the minimal safe
intervention; a standalone endpoint returning 503 has no partial value. The code
is in git history if licensed price data ever arrives -- and if it does, the
right rebuild is per-trade, not portfolio-level alpha, which this data cannot
support at any price.

Removing it took the last `yfinance` caller with it, so that dependency is gone
too. `numpy` is now declared directly, for the permutation null in D11.

The dashboard also carried filter options and descriptions for the three
disabled detectors -- including "statistically improbable and suggests insider
knowledge" -- for rows that can never appear. Those are gone for the same
reason.

## D11. Correct for looking everywhere

Sixteen detectors run against every member, so some of what gets flagged is what
running thousands of tests over hundreds of people produces. `percentile_rank`
(D6) says where a finding sits among its peers; it says nothing about whether
the finding is real.

FDR control could not simply be added, because **no detector produces a
p-value** -- every one is a threshold rule, so Benjamini-Hochberg had nothing to
rank. A null model had to exist first, and one only exists for some of them.

Six detectors ask a *timing* question, which has a well-posed null: the same
trades, the same events, no relationship between them.
`src/analysis/significance.py` tests each (member, detector) pair by circular-
shifting the member's whole trading calendar and re-counting coincidences, then
applies Benjamini-Hochberg across every test in the run.

**Shifting rather than resampling is the load-bearing choice.** Disclosed trades
arrive in same-day PTR batches; a null that resampled dates independently would
scatter those batches, make ordinary clustering look extraordinary, and
manufacture significance on exactly the data this project is built from.

The other ten detectors measure a magnitude and carry **no q-value at all**. A
null `q_value` means *no null model exists*, never *passed one* -- which is why
the API default is `q IS NULL OR q <= alpha` rather than a bare threshold, and
why every response says `has_null_model`.

Surviving this means the timing alignment is unlikely by chance. Not that the
member acted on anything.

## D12. Say how much of each filing was actually read

`parsed` only ever meant the parser ran without raising. A filing that yielded
nothing was recorded identically to one read cleanly, and every trade in this
project comes out of a PDF.

Each disclosure now carries `parse_confidence` and `parse_warnings`
(`src/parsing/confidence.py`). The score is a completeness ratio -- rows read
over rows that looked like records, times fields extracted over fields required
-- so a dropped row lowers it by arithmetic rather than by a rule somebody has
to remember to tune. Three caps are asserted and labelled as such in the code.

Building it found what it was built to find. pdfplumber collapses some table
rows into a single cell, and the parser dropped them: across the six real
filings in the test corpus, **18 transactions lost against 16 kept**, with two
filings parsing to nothing while recorded as parsed successfully. The corpus now
yields 34.

Nothing is excluded from analysis on the strength of the score. A missing trade
is already invisible, and dropping the ones known to be shaky would compound
that silently -- and would make a member whose PDFs parse badly look cleaner
than one whose parse well. This is D9 applied to the parser instead of the
filer.

## D13. A scan of a paper form is not a parse failure

Running the D12 score over 200 real PTRs sampled from the 966 the House Clerk
published for 2024 and 2025 turned up something the score alone reported
misleadingly: **123 of those 966 filings -- 12.7% -- are scans with no text
layer at all.** A 7-digit document ID predicted it with 100% accuracy across the
200 sampled.

They score 0.0, and that is the honest score: nothing in them is readable. But
folding them into "filings the parser read nothing out of" makes the parser's
failure count roughly eight times the real one, and buries the fact actually
worth publishing -- about **one House trade report in eight is not in the
machine-readable dataset**, and no parser change will put it there. Only OCR
would, and this project does not do OCR.

So `has_text_layer` is recorded on every parse, separately from the score,
because it is a property of the document rather than of the parser. Three
things follow from it:

- `parse_quality_summary` reports `filings_that_yielded_nothing` (had text, read
  nothing -- a bug) and `filings_with_no_text_layer` (a scan -- a coverage
  limit) as different numbers.
- `parse_disclosures --min-confidence` skips the scans. They will score 0.0
  every time, and re-downloading one filing in eight every night to confirm it
  is waste. `--reparse` still reaches them, and filings whose text layer is
  unknown are still re-read, because nobody has checked those.
- The API exposes the flag, and `?has_text_layer=` filters on it, so "show me
  what the parser did badly on" and "show me what was never machine-readable"
  are separable questions.

Null means unknown -- parsed before this was recorded -- and is counted with the
parser's failures rather than excused as a scan. The safe reading of "nobody
looked" is not "it was fine".

## D14. The site explains itself from the detector registry, not from a copy

The dashboard kept its own description of every detector: a legend, a filter
list, and three JavaScript lookup tables. All four were hand-maintained, and
they drifted in the worst possible direction. Beside named members of Congress,
the page was still telling visitors:

> **Stock Outperformance** -- Trading returns significantly beat S&P 500 benchmark
> **Loss Avoidance** -- Statistically improbable success rate (80%+)
> **Trade Timing** -- Perfect timing ... buying before stock rises

All three detectors had been disabled for making exactly those claims: there are
no prices in this dataset (D10), `loss_avoidance` incremented its numerator and
denominator on the same branch so its rate was always 100%, and none of them ran
a statistical test of any kind. The page advertised them anyway.

The same tables omitted every detector that *does* carry a q-value --
`donor_conflict`, `lobbying_overlap`, `contract_front_run`,
`sponsorship_conflict`, `bill_jurisdiction_conflict`, `cross_member_cluster`.
The six strongest findings the system produces each rendered as "This anomaly
requires further investigation to understand its significance."

`src/analysis/catalog.py` is now the only copy, served at
`/api/anomalies/types` and rendered by the page. Three things are structural
rather than remembered:

- A detector in `disabled_anomaly_types` is not described, because the
  catalogue reads the same setting the query layer does.
- Whether a type is marked as carrying a q-value is derived from
  `NO_NULL_MODEL`, never asserted a second time.
- Every entry states `limits` -- what the detector *cannot* show -- next to
  what it measures, on the finding card as well as in the legend. That is the
  field that keeps a pattern in public filings from reading as an accusation,
  and it is the field a legend usually omits.

Each finding now also carries its own badge: a q-value, "did not survive
correction", or "not tested" where no null model exists (D11). Severity was
previously the only thing distinguishing one finding from another, and severity
is a threshold, not a test.

`tests/test_detector_catalog.py` fails if a detector is added, renamed or
disabled without the catalogue following -- including if any description
acquires the words return, profit, gain, outperform or alpha, or claims
significance for a detector that has no null model.

---

## Superseded

`PHASE_7_COMPLETION.md`, the eight `PHASE_8_*` files, `PHASE_9_DECISION.md`,
`COMPLETE_RECAP.md`, `THREE_QUESTIONS_ANSWERED.md`,
`ANNUAL_DISCLOSURE_SOURCES.md`, `COMPLETE_FD_GUIDE.md`,
`FD_IMPLEMENTATION_ROADMAP.md`, `RESOURCE_INDEX.md` and
`QUIVERQUANT_VS_ALTERNATIVES.md` are superseded by this file and available in
git history. `PHASE_8_IMPLEMENTATION.md` and `PHASE_8_READY.md` differed by 12
lines; `PHASE_8_ACTION_ITEMS.md` and `PHASE_8_USER_CHECKLIST.md` were the same
checklist twice.
