# Congressional Trading Data Sources - Comparison

**Date**: February 1, 2026  
**Analysis**: Free vs Paid alternatives for PTR (stock trade) data

---

## The Situation

QuiverQuant is **$10/month** (not free as we initially thought).

**Question**: Is it worth it, or should we use free alternatives?

---

## Free Alternatives for Congressional Trading Data

### 1. ✅ House Clerk Official Website (FREE)
**URL**: https://disclosures-clerk.house.gov/

**Method**: Scrape HTML with Playwright/Selenium  
**Data**: PTR trades + annual disclosures  
**Coverage**: House members only  
**Effort**: High (build scraper)  
**Status**: Official source, no rate limits

**Pros**:
- ✅ Official government source
- ✅ Always available
- ✅ No API key needed
- ✅ Free forever

**Cons**:
- ❌ JavaScript-rendered (need Playwright)
- ❌ Takes ~2-4 hours to build scraper
- ❌ More complex to maintain

---

### 2. ⏳ Senate eFD Website (FREE)
**URL**: https://efdsearch.senate.gov/

**Method**: Scrape HTML with Playwright/Selenium  
**Data**: Senate member disclosures  
**Coverage**: Senate only  
**Effort**: High (bypass anti-bot)  
**Status**: Official source

**Pros**:
- ✅ Official government source
- ✅ Senate-specific data
- ✅ Free

**Cons**:
- ❌ Anti-bot protection (hard to scrape)
- ❌ May require advanced bypass techniques
- ❌ High maintenance risk

---

### 3. ✅ SEC EDGAR API (FREE)
**URL**: https://www.sec.gov/cgi-bin/browse-edgar

**Method**: REST API  
**Data**: Officer/insider trades (similar to PTR)  
**Coverage**: All companies with insiders  
**Effort**: Low (easy API)  
**Status**: Official SEC source

**Pros**:
- ✅ Free, no API key
- ✅ Official SEC data
- ✅ Easy REST API
- ✅ Good rate limits
- ✅ Well documented

**Cons**:
- ❌ Not Congress-specific
- ❌ Different format than PTR
- ⚠️ Includes corporate insiders, not just Congress

---

### 4. ❌ HouseStockWatcher/SenatestockWatcher (DEFUNCT)
**Status**: Appears to be down/defunct  
These sites that previously scraped and aggregated the data are no longer accessible.

---

### 5. ⏠ GitHub Data Dumps (UNRELIABLE)
Various GitHub repos claim to have historical congressional trading data, but:
- Outdated (last updated 2023 or earlier)
- No longer maintained
- Gaps in coverage

---

## Paid Alternatives

### 1. QuiverQuant (PAID - $10/month)
**Cost**: $10/month  
**Data**: House + Senate trades, pre-parsed and cleaned  
**Effort**: Very Low (API ready to use)  
**Status**: Active, well-maintained

**Pros**:
- ✅ Clean, parsed data
- ✅ House + Senate combined
- ✅ Real-time updates
- ✅ Easy REST API
- ✅ Active support

**Cons**:
- ❌ $10/month ($120/year)
- ❌ Proprietary data
- ❌ Not official source

---

### 2. OpenSecrets (PAID - varies)
**Cost**: $500-$5,000+/year depending on tier  
**Status**: More expensive, probably overkill for our needs

---

## Recommendation: Cost-Benefit Analysis

### Option A: Use QuiverQuant ($10/month)
**Cost**: $10/month = **$120/year**  
**Setup Time**: 5 minutes  
**Maintenance**: 0 hours  
**Data Quality**: Excellent  
**Coverage**: House + Senate

**Total Cost of Ownership**: $120/year + 5 minutes setup

---

### Option B: Build House Clerk Scraper (FREE)
**Cost**: $0/year  
**Setup Time**: 2-4 hours (build scraper)  
**Maintenance**: 1-2 hours/month (when House changes site structure)  
**Data Quality**: Good (official source)  
**Coverage**: House only (no Senate)

**Total Cost of Ownership**: $0/year + 3-4 hours initial + 1-2 hours/month ongoing

---

### Option C: Use SEC EDGAR + House Scraper (FREE)
**Cost**: $0/year  
**Setup Time**: 3-5 hours (build both)  
**Maintenance**: 1-2 hours/month  
**Data Quality**: Good (official source)  
**Coverage**: Mixed (SEC insider trades + House PTR)  
**Issue**: Not apples-to-apples (different data types)

**Total Cost of Ownership**: $0/year + 4-5 hours initial + 1-2 hours/month ongoing

---

## My Recommendation

### **GO WITH QUIVERQUANT ($10/month)**

**Why**:
1. **$10/month is minimal cost** - equivalent to 1 coffee
2. **$120/year is tiny** for the data value provided
3. **Your time is worth more** - 3-4 hours to build scraper = $75-150+ in your time
4. **Maintenance burden** - Free option requires ongoing work when sites change
5. **Reliability** - Paid service is actively maintained
6. **Coverage** - House + Senate combined (not available in free options)
7. **We already built the QuiverQuant adapter** - It's ready to use

### Alternative If Budget is Absolutely Constrained

If you absolutely cannot spend $10/month:

**Phase 9 (already planned)** uses Playwright to scrape House Clerk + Senate eFD - this is free but requires:
- 3-5 hours to build scrapers
- 1-2 hours/month maintenance
- Risk of breaking when sites update
- Senate data is harder to get (anti-bot)

---

## Decision Matrix

| Criteria | QuiverQuant | House Scraper | SEC EDGAR |
|----------|-------------|---------------|-----------|
| Cost | $10/mo | Free | Free |
| Setup Time | 5 min | 3-4 hrs | 3-5 hrs |
| Maintenance | None | 1-2 hrs/mo | 1-2 hrs/mo |
| House Coverage | ✅ | ✅ | ⚠️ Mixed |
| Senate Coverage | ✅ | ❌ | ❌ |
| Data Quality | Excellent | Good | Good |
| Reliability | High | Medium | High |
| **Total 1st Year Cost** | **$120** | **$0 + 10 hrs** | **$0 + 12 hrs** |
| **Time Value @ $50/hr** | $120 | $500+ | $600+ |
| **Real Cost (time included)** | **$120** | **$500+** | **$600+** |

---

## Bottom Line

**QuiverQuant at $10/month is the best value.**

Your time is worth more than $120/year. Maintenance costs alone on a free scraper solution will exceed that.

### Next Steps:

**Option 1**: Pay $10/month for QuiverQuant  
- ✅ Phase 8 implementation is done
- ✅ Just need the API key
- ✅ Proceed immediately

**Option 2**: Skip paid and implement Phase 9 (Playwright scrapers)  
- ✅ Free
- ⏳ Takes 3-5 hours to build
- ❌ Ongoing maintenance required
- ⚠️ Senate data is harder to get

---

## What Do You Want to Do?

1. **Pay $10/month for QuiverQuant** → Get API key, finish Phase 8 in 5 min
2. **Skip and do Phase 9 now** → Free but takes 3-5 hours
3. **Something else** → Let me know

I recommend **Option 1** - the ROI is excellent.

