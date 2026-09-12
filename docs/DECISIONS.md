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

**Status:** accepted. Migration in progress — see D2.

## D2. Replace vendor feeds with official sources

| Need | Current | Replacement | Status |
|---|---|---|---|
| House trades | House Clerk PTR XML | — already official | done |
| Member roster | unitedstates.io congress-legislators | — already official | done |
| Committee assignments | none (detector had an empty table) | congress-legislators `committee-membership-current.yaml` | **done** |
| Senate trades | QuiverQuant | Senate eFD | not started |
| Campaign donations | QuiverQuant `/bulk/corporatedonors` | FEC API | not started |
| Lobbying | QuiverQuant `/live/lobbying` | Senate LDA API | not started |
| Gov contracts | QuiverQuant `/live/govcontractsall` | USASpending API | not started |
| Benchmark prices | `yfinance` | licensed vendor, or drop — see D4 | deferred |

The Tier-2 detector logic in `src/analysis/tier2_detectors.py` does not change;
only its feed does.

**QuiverQuant** is currently the only working Senate path, the only trade
source, and the sole feed for the Tier-2 detectors. Its terms appear to limit
use to personal, non-commercial purposes and to prohibit redistribution without
an executed agreement — confirm directly before shipping anything paid.

**`yfinance`** scrapes Yahoo Finance through an unofficial library against
Yahoo's terms. Acceptable for a hobby project, not for a paid endpoint.

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
thresholds; percentile rank is currently an annotation on the output, not the
trigger. Formal FDR control needs per-detector p-values, which none of them
currently produce.

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
