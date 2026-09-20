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

## D15. Two more detectors are held, which is not the same as disabled

`DISABLED_ANOMALY_TYPES` carries six names for three different reasons (the
third is D16), and conflating them would be a mistake in either direction —
leaving these two off for ever, or turning them back on without looking.

The three in D3 are **condemned**: their arithmetic is indefensible and fixing
them needs share-level price history this project does not have.

`wealth_vs_salary` and `rapid_asset_appreciation` are **held**. Nothing is known
to be wrong with them. Both read their roster from
`members_with_annual_filings`, which gated on `Disclosure.filing_type == "FD"` —
a value **zero of 3,900 stored rows carry**. Only the Senate ingester ever wrote
it, as a fallback that stopped firing once real report titles were stored, and
the House never used it at all. So both walked an empty roster and found
nothing, silently, for months. `wealth_vs_salary` reading 0 findings looked like
a fact about Congress; it was a fact about one line.

That gate is fixed, and that is precisely the problem: they publish again, and
nobody has ever read what they say.

The bar for turning them back on is low and specific: **read a sample of their
output and say it is sound.** Not "prove them correct" — look at what they
accuse people of. Then delete
`tests/test_disabled_detectors.py::TestTheHeldDetectorsAreStillHeld`, restore
the `after > before` assertion in
`test_run_advanced_persists_nothing_while_both_detectors_are_held`, and say in
the commit message who looked.

The reason for the ceremony: this project has published four confident false
accusations against named members of Congress — three checkers caught during the
1,000-finding audit, and the wealth findings that had Craig Goldman gaining
$15,008,502.50 in a year against a real figure of $551,001, and Laura Gillen
flagged at $476,500.50 when her true figure was below the reporting threshold
entirely. Every one of them was produced by code that looked reasonable and had
passing tests. A fifth unread accuser is the same mistake in a new place.

## D16. A held detector is skipped, not run and thrown away

`excessive_wealth_growth` is the sixth name in `DISABLED_ANOMALY_TYPES`, and it
is held for a third reason: not condemned arithmetic (D3), not an unread
detector that never ran (D15), but a detector whose **input changed scale
underneath it**.

It was held first for the length of one operation. The parsers had just been
corrected and re-reading the stored filings took several dispatches, so the
corpus was MIXED — some filings read by the new parser, some by the old. This
detector compares consecutive annual filings and nothing in the analysis layer
reads `parse_confidence`, so it would have published the difference between two
PARSERS as a member's wealth doubling in a year, against the ~450 members with
two or more annual filings, with a NULL q-value that always passes the FDR
filter.

That operation is finished: 2,174 annual filings re-read, zero failures, mean
parse confidence 0.91, and 11 filings still yielding nothing where 422 did.
**The hold stands on new ground.** Two things replace the original rationale:

- **The bar it must clear gets lower as wealth rises.** It fires on
  `growth_percent > salary_growth_percent + 200`, where `salary_growth_percent`
  is `salary × years / prev_nw × 100` — a figure that *shrinks* as net worth
  grows. Correcting the parsers raised disclosed value about 2.7x, so the
  effective threshold slides toward the flat 200% and it fires **more** readily
  on the corrected corpus than on the understated one it was never reviewed
  against either.
- **343 Senate filings enter its population for the first time.** They stored
  zero assets until a Senate annual reader existed, so net worth was `None` and
  every one was skipped. They are comparable now, and nobody has seen what it
  says about them.

The release condition is D15's, unchanged: read a sample and say it is sound.

### Skipped, not discarded

D3 describes the gate as `persist_anomalies()`, "so no write path can bypass
it". That is the correctness half. It is not the whole gate, and the difference
is measured in hours.

A disabled type used to be **computed and then thrown away**. On one production
rebuild `outperforming_trades` took 17m15s to produce 23 findings that
`persist_anomalies` dropped, and `loss_avoidance` 17m17s for 18 more — 34
minutes of a 168-minute analysis step. Those two were gated at their call sites.
The three that read `Asset` and `Liability` rows were not, and they are the ones
the re-parse campaign made expensive: roughly twice the holdings per House
annual, plus 343 Senate annuals that now return data instead of nothing.

So every type in `DISABLED_ANOMALY_TYPES` is now skipped at the site that runs
it, via `detector_is_disabled` in `src/analysis/__init__.py`. This is
behaviour-preserving by construction — `persist_anomalies` refused to store
these types and `detect_red_flag_combinations` refused to count them, so nothing
downstream can tell the difference.

`tests/test_held_detectors_do_no_work.py` holds both halves. It asserts on the
SQL the session emitted rather than on a mock not being called, because the
claim is that **no asset row is read**, which is what costs the time. One test
there walks `src/analysis` and fails if a disabled type has no gate: adding a
seventh entry to the list gets you the persist-time gate for free, which is
exactly why a missing call-site gate goes unnoticed.

## D17. Schedule H is read; C, E, F are deferred; G and I are ruled out

The House annual form has nine schedules. This project read three of them — A
(assets), B (transactions) and D (liabilities) — and nothing had ever measured
what was in the other six.

Sampled properly rather than guessed: 70 House annual reports drawn at random
(seeded) from the Clerk's own 2024–25 index, population 802, of which 67 parsed.

| Schedule | | filings disclosing | rate |
|---|---|---:|---:|
| C | Earned income | 41/67 | 61% |
| E | Positions held | 36/67 | 54% |
| F | Agreements | 36/67 | 54% |
| **H** | **Travel payments** | **35/67** | **52%** |
| G | Gifts | 0/67 | **0%** |
| I | Payments to charity in lieu of honoraria | 0/67 | **0%** |

**G and I are ruled out on evidence, not on effort.** Both are present in all 67
filings and read exactly `None disclosed.` in all 67 — checked against the raw
schedule regions, not inferred from a failed parse. The reporting thresholds are
high enough that House members effectively never file either. Tables for them
would be empty tables.

**H is built.** 61 dated trips across the sample, so on the order of 700 across
the corpus, and the rows name who paid for what:

```
Norma Torres     American Israel Education Foundation   San Francisco – Tel Aviv
Pramila Jayapal  Center for Democracy in the Americas   Washington DC – Havana
Greg Casar       Center for Economic and Policy Research Austin – Bogotá
Greg Murphy      The Aspen Institute                    Raleigh – Bellagio, Italy
Andy Barr        Ripon Society                          Lexington – Vienna
```

47 distinct sponsors in 61 trips. Privately funded congressional travel is the
highest news value of the six and the most self-contained — the form states the
sponsor, the dates and the itinerary in a bounded grid, so nothing has to be
inferred.

**C and E are deferred, not rejected.** They are the natural join partners for
the committee-conflict detectors in `src/analysis/committee_conflicts.py` —
outside income and positions held, against the jurisdiction a member sits on —
but weak on their own, and that join is a detector decision with its own
evidence bar. F is pension-continuation boilerplate in nearly every filing
sampled.

### Three columns of Schedule H are deliberately not stored

The form ends with `Lodging?`, `Food?` and `Family?`. Those ticks are **drawn as
vector curves, not text**: page 6 of document `10074944` carries seven trips and
**zero characters to the right of x=400**, where all three columns sit.

Storing them would mean inferring a boolean from path geometry and serving it as
a disclosure — the "inferred rather than read" move that produced every parser
defect this log records. The reader matches those three headings so that
`Days at Own Exp.` has a right-hand edge, and drops them.

### Stored, not served

`travel_payments` has no API route, no template and no detector, and
`tests/test_travel_payments_are_stored.py::TestItIsNotPublishedYet` fails if one
appears. That is so publishing it is a decision somebody makes rather than
something that happens: the credentials this project leaked are still live, and
a detector over travel data would need the sample-reading bar D15 sets for any
accuser.


## D18. An LLM audit measures its own prompt unless a control says otherwise

**Decision:** no number from an adversarial review of this project's findings is
published without a neutral-prompt control on the same findings, and no defect
class is acted on until it has been checked against the primary document.

### What was run

1,000 findings were to be audited by one agent per finding, told to disprove it.
A local corpus was built from the House Clerk with the same code that would
publish — 539 members, 2,889 filings, 10,594 transactions, 596 findings. 174
verdicts returned before a session limit:

| verdict | n | share |
|---|---:|---:|
| overclaimed | 121 | 69.5% |
| refuted | 44 | 25.3% |
| **stands** | **9** | **5.2%** |

Eight of the nine survivors were `late_filing`, which is a subtraction of two
dates and alleges nothing. Read on its own that says everything requiring
interpretation is broken.

### The control says otherwise

60 of those findings — stratified by type, so the hostile run's verdicts on this
exact subset were 3 stands / 41 overclaimed / 16 refuted, matching the whole —
were re-run with a neutral prompt. Same findings, same corpus, same schema. Only
the framing differs: the first says "hostile investigative reporter trying to
DISCREDIT", "find the hole", "write the takedown", and "use `overclaimed`
freely; it is the most common real defect". The second asks for arithmetic and
wording to be checked with equal weight and says a finding that checks out
should be reported as standing as plainly as a flawed one is reported as flawed.

| | stands | overclaimed | refuted |
|---|---:|---:|---:|
| hostile | 3 (5%) | 41 (68%) | 16 (27%) |
| neutral | **42 (70%)** | 12 (20%) | 6 (10%) |

**Exact agreement: 14 of 60 (23%)**, across three categories. Thirty-two
findings the hostile run called overclaimed, the neutral run called sound; seven
it refuted outright, the neutral run upheld.

So `5.2% of this project's findings survive scrutiny` is a fact about the
prompt. It is withdrawn, and nothing like it goes out without the paired control
beside it.

### What survived both, and what that was worth

Four findings both runs refute. Three were real defects and are fixed:

| finding | defect | fixed by |
|---|---|---|
| Chip Roy, sector concentration | every energy trade was his wife's | #98, #99 |
| Bill Hagerty, volume spikes | four rows stored correctly as `Dependent Child` and counted as his | #99 |
| Blake Moore, trade clustering | "30 in a row" was the Clerk's alphabetical row order | #99 |

The fourth was false. See below.

The audit's real yield is one defect found four ways — nothing asked whose trade
it was — plus a noun (`stock trades` over municipal bonds) and a grade
(`severity` as a literal). All are fixed, and none of them needed the 5.2%.

### The duplicate-row class does not exist

Thirteen of the 24 fatal refutations claimed rows counted two to four times, and
the neutral run independently made the same claim about David Taylor. **Six
filings were checked line by line against the Clerk's PDF and the parser is
right in all six:**

| filing | claim | printed | stored |
|---|---|---:|---:|
| Carol Miller 20025203 | "5 sales stored twice" | 10 | 10 |
| Cleo Fields 20031017 | "6 of 36 are double-reads" | 38 | 36 |
| David Taylor 20033495 | "11 rows are really 5" | 11 | 11 |
| Earl Carter 20025499 | duplicate Ameris rows | 4 | 4 |
| Katherine Clark 20024795 | duplicate NYCB rows | 2 | 2 |
| Josh Gottheimer 20024612 | duplicate MSFT rows | 15 | 15 |

The House form legitimately prints near-identical lines — the same asset, the
same day, different amount bands, or a partial sale split across lots. When the
weak text path reads one of a pair it truncates the description and drops the
ticker, so a truncated row sits beside a full one and **looks** like a
photocopy. It is not.

Both prompt framings share that false positive, and so did a prefix-matching
measurement written here before the documents were opened: it counted 242
"duplicate" rows across 48% of findings and would have deleted real disclosed
trades. `restatements.py` refuses to collapse rows within a single filing for
exactly this reason, and that rule stands.

**A verification wave has to be built to catch this specific error**, or three
more agents will confirm it three more times. Agreement between independent
reviewers is not evidence when they share a failure mode.

### Standing rules

- A defect class is checked against the primary document before any code
  changes. Five of six duplicate claims were refuted by counting lines in a PDF.
- A number from an adversarial run is published only beside a neutral control on
  the same items.
- Corpus re-runs, not the test suite, catch what a fix breaks. The first version
  of the attribution fix passed its own tests while erasing 85 real late
  filings; the corpus run found it in one command.

## D19. The unread-owner default stays, because it is not unread

**Decision:** `_normalize_owner`'s final `return "Self"` is kept. "self" is
matched explicitly above it so the catch-all no longer carries a value the
parsers produce, and that is the whole change.

### Why it looked like a defect

D18's audit turned on one shape: the parser turning *"could not read this"* into
the affirmative claim that the member owns it. #98 fixed that for the House
owner codes and deliberately left one branch alone — an owner string nobody
recognises still becomes `"Self"`. Same shape, obviously next.

### What the measurement said

Instrumented over **114 real House PTRs, 1,556 owner cells**:

| value | count |
|---|---:|
| `SP` | 706 |
| *(blank)* | 705 |
| `JT` | 123 |
| `DC` | 22 |

The unrecognised branch fires **zero times**. Blank is not unread — the House
form leaves the column empty for the filer, so blank is the document saying
"mine".

### Why changing it would have been a serious regression

`SenateHtmlParser` subclasses `PTRParser`, and the Senate's eFD spells the words
out: `Self`, `Spouse`. **`"Self"` matches none of the tests above the
catch-all** — not "spouse", not "joint", not "child" — so that `else` was the
only thing reading it.

Returning `None` there, the change that looked obviously right, would have made
every Senate member's own trade unattributed. `attribution.held_by_member` would
then drop each one from every count-based detector: not counting somebody else's
trades as the member's, but failing to count the member's own at all, **for an
entire chamber**. A worse version of the bug #98 and #99 fixed.

### What was actually done

`"self"` gets its own branch, so nothing the parsers emit depends on the
catch-all. No behaviour changes — every observed value resolves exactly as
before. `tests/test_who_the_filing_says_it_is.py` reads the function with `ast`
and fails if any observed value stops being named by a branch.

That test was wrong first: it reimplemented the branch logic and asked its own
copy whether a value fell through, which passed with the explicit `self` branch
deleted. A test that checks a replica checks nothing.

### The standing rule this earns

**Measure the branch before removing it.** A defect's *shape* recurring is a
reason to look, not a reason to act — the same three lines were a real bug in
one parser and load-bearing in the other, and only counting told them apart.

## D20. What the corrected pipeline actually publishes, checked against the filings

**Decision:** the number this project may quote about its own reliability is
**90% of findings stand under neutral review**, measured over every finding the
corrected detectors produce, with each reviewer required to check the primary
document. The 5.2% figure D18 withdrew is not comparable to it and must not be
cited beside it.

### The run

All **460** findings the current detectors produce over the local House corpus,
one reviewer each, neutral prompt, no sampling.

| verdict | n | share |
|---|---:|---:|
| stands | 414 | **90.0%** |
| overclaimed | 33 | 7.2% |
| refuted | 13 | 2.8% |
| cannot_tell | 0 | 0% |

Severity: 2 fatal, 22 serious, 242 minor, 194 none.

| detector | audited | stands |
|---|---:|---:|
| volume_spikes | 22 | 100% |
| late_filing | 174 | 94% |
| large_trade | 80 | 94% |
| trade_clustering | 41 | 88% |
| high_trading_frequency | 131 | 83% |
| sector_concentration | 10 | 70% |
| committee_jurisdiction_conflict | 2 | 50% |

**406 of the 460 reviewers (88%) downloaded a filing and counted its printed
transaction lines**, because the prompt made that mandatory before any claim of
duplicated rows. Not one duplicate-row claim survived it. D18's false class did
not recur.

### The like-for-like comparison

The only honest comparison holds the prompt constant. Both figures below are
from the *same* neutral prompt:

    old corpus, pre-fix     70% stand   (n=60)
    fixed corpus            90% stand   (n=460)

### What it found, and one was ours

* **A month was not a month.** Grouping ran by filing then month, so a member
  reporting one month across two PTRs produced two partial findings both
  titled "in <Month>". 128 member-months are split across filings, carrying
  2,668 rows. Fixed.
* **The count did not say whose it was.** #99 stopped counting a spouse's
  trades and left the wording; Rep. Donalds' filing prints 48 transactions
  against a published 23. Right number, unverifiable sentence — the same
  defect #100 fixed in that exact sentence, reintroduced one commit earlier by
  the fix for #99. Fixed.

### The open defect it surfaced: the parser drops printed rows

Comparing printed transaction lines against stored rows over 79 local filings:

    printed   2,062
    stored    1,941
    missing     121   (5.9%), in 13 of the 79 filings

Worst cases lose 29, 27 and 21 rows from single filings. It is the *mirror
image* of the class D18 refuted: the parser does not double-read, it
under-reads. Every published count may therefore be low.

Under-counting is the safe direction for an accusation, and it is still wrong:
it breaks the one property this project has been building all along — that a
reader can check a published number against the document and have it
reconcile. **Not yet fixed. It is the next parser change.**
