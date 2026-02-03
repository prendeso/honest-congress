# How to Get Annual Financial Disclosure Data - COMPLETE GUIDE

## Question Answered
**"How else can we get annual financial disclosure?, including historical data"**

## Quick Answer: 8 Sources + 3 Recommendations

---

## The 8 Sources (Detailed)

### 1. **House Clerk Official** ✅ (We already use)
- **Historical**: 2004-2024 (20 years!)
- **URL**: https://disclosures-clerk.house.gov/
- **Format**: PDF documents + XML index
- **Cost**: Free
- **Effort**: Low (already in our pipeline)
- **Status**: Can backfill 2004-2023 historical years
- **Action**: Download XML for 2004-2023 and parse

### 2. **OpenSecrets (CRP)** ✅ FREE TIER AVAILABLE
- **Historical**: 1990-2026 (36 years!)
- **URL**: https://www.opensecrets.org/
- **Format**: CSV download, API available
- **Cost**: Free tier + Paid tiers ($0-500+/month)
- **Effort**: Low (just download CSV)
- **Coverage**: House + Senate
- **Data Quality**: Pre-processed, clean
- **Action**: Start with FREE tier downloads

### 3. **ProPublica Datasets** ✅ CHEAP HISTORICAL
- **Historical**: 2004-2014 (10 years)
- **URL**: https://stores.propublica.org/
- **Format**: CSV files ready-to-use
- **Cost**: $1-99 per dataset (one-time)
- **Effort**: Low (CSV import)
- **Coverage**: House + Senate
- **Data Quality**: Pre-cleaned, verified
- **Action**: Buy and import for 2004-2014 backfill

### 4. **LegiStorm** ✅ BEST IF BUDGET
- **Historical**: 2007-2026 (19 years)
- **URL**: https://www.legistorm.com/
- **Format**: Database + CSV export
- **Cost**: $1000-2000+/month
- **Effort**: Zero (just download CSV)
- **Coverage**: House + Senate, fully parsed
- **Data Quality**: Professional, maintained
- **Action**: Consider if budget available

### 5. **Senate eFD Database** ✅ FREE BUT HARD
- **Historical**: 2012-2026 (14 years)
- **URL**: https://efdsearch.senate.gov/
- **Format**: Web interface, PDF download
- **Cost**: Free
- **Effort**: High (needs Playwright scraper, anti-bot)
- **Coverage**: Senate only
- **Data Quality**: Official source
- **Action**: Build scraper as Phase 9

### 6. **SEC EDGAR** ✅ SUPPLEMENTARY
- **Historical**: 1990-2026 (36 years)
- **URL**: https://www.sec.gov/cgi-bin/browse-edgar
- **Format**: XML, HTML, CSV
- **Cost**: Free
- **Effort**: Medium (parsing required)
- **Coverage**: Public company officers only
- **Data Quality**: Official SEC data
- **Action**: Use for verification only

### 7. **Archive.org Wayback Machine** ✅ PRE-2004
- **Historical**: 2000+ (26+ years)
- **URL**: https://web.archive.org/
- **Format**: HTML/PDF snapshots
- **Cost**: Free
- **Effort**: Very high (snapshot scraping)
- **Coverage**: Historical snapshots
- **Data Quality**: Variable (what was captured)
- **Action**: Last resort for pre-2004

### 8. **Congress.gov API** ❌ NOT RECOMMENDED
- **Historical**: Limited
- **URL**: https://api.congress.gov/
- **Format**: JSON API
- **Cost**: Free (API key needed)
- **Effort**: Medium
- **Coverage**: References only
- **Data Quality**: Minimal FD data
- **Action**: Skip this source

---

## 3 Recommended Strategies

### **STRATEGY 1: FREE + EASY** ✅ RECOMMENDED
**Best for**: Teams with no budget, want complete coverage

**What**:
1. House Clerk XML 2004-2023 (free)
2. OpenSecrets CSV (free tier)
3. ProPublica CSV 2004-2014 ($1-99)
4. QuiverQuant API trades ($10/month)

**Coverage**: House 2004-2026, Senate 1990-2026, Trades 2018-2026  
**Cost**: $120-129/year  
**Effort**: 2-3 hours setup  
**Maintenance**: Low  

**Timeline**:
- Week 1: Download sources
- Week 2: Create ingestion pipelines
- Week 3: Backfill historical data

---

### **STRATEGY 2: PAID + ZERO WORK** ✅ IF BUDGET
**Best for**: Teams with budget, want simplicity

**What**:
1. LegiStorm subscription ($1000-2000/month)
2. QuiverQuant API ($10/month)

**Coverage**: House 2007-2026, Senate 2007-2026, Trades 2018-2026  
**Cost**: $12000-24000+/year  
**Effort**: <1 hour setup  
**Maintenance**: Zero  

**Benefit**: Pre-parsed data, no bugs, no work  

---

### **STRATEGY 3: HYBRID** ✅ FLEXIBLE
**Best for**: Teams wanting balance

**What**:
1. House: Free House Clerk + ProPublica ($1-99)
2. Senate: OpenSecrets free + optional LegiStorm ($500-1000/month)
3. Trades: QuiverQuant ($10/month)

**Coverage**: House 2004-2026, Senate varies, Trades 2018-2026  
**Cost**: $120-12000+/year (depending on Senate choice)  
**Effort**: 1-2 hours setup  
**Maintenance**: Low-Medium  

**Flexibility**: Upgrade individual components as needed

---

## Current Data Gap

**What We Have**:
- House FD: 2024-2025 only (707 reports)
- Senate FD: None
- Trades: 2018-2026 (9,777 trades)

**What We're Missing**:
- House FD: 2004-2023 (20 years = ~10,000 reports)
- Senate FD: 2012-2026 (14 years = ~5,000 reports)

**Potential Addition**:
- ~15,000 additional disclosure records
- 20+ years of historical data

---

## Implementation Priority

### Phase 1: IMMEDIATE (Week 1-2)
```
1. ✅ Test House Clerk for 2004-2023 XML
2. ✅ Download OpenSecrets (free)
3. ✅ Buy ProPublica ($1-99)
4. ✅ Create ingestion pipelines
```

### Phase 2: BACKFILL (Week 3-4)
```
1. ✅ Import House 2004-2023
2. ✅ Import Senate from OpenSecrets
3. ✅ Run anomaly detection on 20+ years
4. ✅ Verify data quality
```

### Phase 3: OPTIONAL (Later)
```
1. ⏳ Build Senate eFD scraper (Phase 9)
2. ⏳ Add daily updates
3. ⏳ Upgrade to LegiStorm (if budget)
```

---

## Cost Breakdown

### Option 1: FREE
```
House Clerk:  Free
OpenSecrets:  Free
ProPublica:   $1-99 (one-time)
QuiverQuant:  $10/month
────────────────────
Annual:       $120-129
```

### Option 2: PAID
```
LegiStorm:    $1000-2000/month
QuiverQuant:  $10/month
────────────────────
Annual:       $12000-24000+
```

### Option 3: HYBRID
```
House Free:   Free
OpenSecrets:  Free
ProPublica:   $1-99
LegiStorm:    $0-1000/month (optional for Senate)
QuiverQuant:  $10/month
────────────────────
Annual:       $120-12000+ (varies)
```

---

## Data Quality Comparison

| Source | Cleaned | Current | Coverage | Cost |
|--------|---------|---------|----------|------|
| House Clerk | No | Yes | 2004-2024 | Free |
| OpenSecrets | Yes | Yes | 1990-2026 | Free |
| ProPublica | Yes | Old | 2004-2014 | $1-99 |
| LegiStorm | Yes | Yes | 2007-2026 | $1000+/mo |
| Senate eFD | No | Yes | 2012-2026 | Free |
| SEC EDGAR | No | Yes | 1990-2026 | Free |

---

## Next Steps

### IF CHOOSING FREE OPTION:
1. I'll check if House Clerk has 2004-2023 XML
2. Create OpenSecrets CSV downloader
3. Create ProPublica CSV importer
4. Backfill 20 years of historical data

### IF CHOOSING PAID OPTION:
1. Show you LegiStorm setup
2. Create CSV import pipeline
3. Configure monthly updates
4. Zero manual work afterwards

### IF CHOOSING HYBRID:
1. Combine free sources for House/Senate
2. Optionally upgrade Senate to LegiStorm
3. Scale up as budget allows

---

## Commands Reference

**Check House Clerk availability**:
```bash
python scripts/check_house_clerk_historical.py
```

**Download OpenSecrets data**:
```bash
# Visit: https://www.opensecrets.org/downloads/
# Download CSV files manually (no API key needed)
```

**Import ProPublica data**:
```bash
python -m src.cli ingest-propublica --year 2004 2005 ... 2014
```

**Backfill House FD**:
```bash
python -m src.cli ingest-house-clerk --year 2004 2005 ... 2023
```

---

## Summary

**8 Sources Available**:
1. House Clerk (free, 2004-2024)
2. OpenSecrets (free, 1990-2026) ✅
3. ProPublica (cheap, 2004-2014) ✅
4. LegiStorm (paid, 2007-2026) ✅
5. Senate eFD (free, 2012-2026)
6. SEC EDGAR (free, 1990-2026)
7. Wayback Machine (free, 2000+)
8. Congress.gov API (limited)

**3 Recommended Paths**:
- **Free**: $120/year + 2-3 hrs
- **Paid**: $12000+/year + <1 hr
- **Hybrid**: $120-12000+/year + 1-2 hrs

**Best Value**: Start with FREE option (Option 1)

---

**Ready to implement? Which option do you choose?**

