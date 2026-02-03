# Implementation Roadmap: Get Historical FD Data

## Quick Answer

**8 ways to get annual financial disclosures with historical data:**

1. **House Clerk Official** (2004-2024) - FREE, we use this
2. **OpenSecrets** (1990-2026) - FREE tier available ✅ RECOMMENDED
3. **ProPublica Datasets** (2004-2014) - $1-99 ✅ RECOMMENDED
4. **LegiStorm** (2007-2026) - $1000+/month (best if can afford)
5. **Senate eFD** (2012-2026) - FREE but needs Playwright
6. **SEC EDGAR** (1990-2026) - FREE, supplementary data
7. **Wayback Machine** (2000+) - FREE, historical snapshots
8. **Congress.gov API** - Not recommended (limited data)

---

## Detailed Breakdown

### **OPTION A: FREE + EASY (Start Here)**

#### Step 1: Get House Historical (2004-2023)
```bash
# Check House Clerk for 2004-2023 XML files
# URL: https://disclosures-clerk.house.gov/public_disc/financial-pdfs/
# Files should be named: 2004FD.xml, 2005FD.xml, ... 2023FD.xml

# If available, download and parse with existing pipeline
# Our PDF parser should work on these historical files
```

#### Step 2: Get Senate Data (1990-2026)
```bash
# Use OpenSecrets FREE tier
# URL: https://www.opensecrets.org/

# No API key needed for basic downloads
# Download personal financial data CSV
# Coverage: Both House and Senate
# Note: Data is already processed/cleaned
```

#### Step 3: Get Historical Fill (2004-2014)
```bash
# Buy ProPublica historical datasets
# URL: https://stores.propublica.org/
# Datasets: Congressional Trading Data, etc.
# Cost: $1-99 per dataset
# Format: CSV - ready to use
```

**Total Cost**: $10-15/month (QuiverQuant only)  
**Implementation Time**: 2-3 hours  
**Coverage**: 2004-2026 (House), 1990-2026 (Senate)

---

### **OPTION B: PAID + EASIEST (No Development)**

#### Use LegiStorm
```bash
# Subscribe to LegiStorm
# URL: https://www.legistorm.com/

# One subscription covers:
# - All House FD data (2007-2026)
# - All Senate FD data (2007-2026)
# - Pre-parsed, cleaned
# - CSV export available
# - Monthly updates

# No PDF parsing needed!
# No API scraping needed!
```

**Cost**: $1000-2000/month  
**Implementation Time**: <1 hour (just download CSV)  
**Coverage**: 2007-2026 (most complete)  
**Benefit**: Professional data, no bugs, no maintenance

---

### **OPTION C: HYBRID (Best Balance)**

**For House (2004-2023)**:
- Use House Clerk XML (free)
- Fallback: ProPublica ($1-99)

**For Senate (2012-2023)**:
- Use OpenSecrets free tier
- OR: Build Playwright scraper
- OR: Use LegiStorm ($500+/month)

**For Trades (2018-2026)**:
- Keep QuiverQuant ($10/month)

**Cost**: $10-510/month depending on Senate choice  
**Coverage**: 2004-2026 (good)

---

## Implementation Steps

### Phase 1: Get House Historical Data (FREE)

**1.1 Check House Clerk Historical Availability**
```python
# Test if 2004-2023 XML files exist
import requests

years = range(2004, 2024)
for year in years:
    url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.xml"
    r = requests.head(url, allow_redirects=True)
    status = "✅" if r.status_code == 200 else "❌"
    print(f"{year}: {status} ({r.status_code})")
```

**1.2 Download Available Years**
```bash
# If files exist, add to ingestion pipeline:
for year in {2004..2023}; do
    wget https://disclosures-clerk.house.gov/public_disc/financial-pdfs/${year}FD.xml
done
```

**1.3 Parse with Existing Pipeline**
```bash
python -m src.cli ingest-fd --year 2004 2005 2006 ... 2023
```

---

### Phase 2: Get Senate Data (FREE)

**2.1 Use OpenSecrets Free Tier**
```bash
# Visit: https://www.opensecrets.org/
# Go to: Downloads > Personal Financial Data
# Download: House Members, Senate Members (CSV)
# No API key needed!
```

**2.2 Create OpenSecrets Ingestion**
```python
# Create new file: src/ingestion/opensecrets.py
# Parse CSV format from OpenSecrets
# Import into database
# Coverage: Full history available (1990+)
```

---

### Phase 3: Get Historical Fill (CHEAP)

**3.1 Buy ProPublica Datasets**
```bash
# Visit: https://stores.propublica.org/
# Search: "Congressional FD" or "Trading"
# Download CSV files
# Cost: $1-99 per dataset
```

**3.2 Import ProPublica Data**
```python
# Create: src/ingestion/propublica.py
# Parse CSV format
# Backfill 2004-2014 gap
```

---

### Phase 4 (Optional): Build Senate Scraper

**4.1 Create Playwright Scraper**
```python
# Create: src/ingestion/senate_efd_scraper.py
# Use Playwright to bypass anti-bot
# Scrape: https://efdsearch.senate.gov/
# Coverage: 2012-2026
# Rate: Respectful scraping (1-2 requests/sec)
```

**4.2 Add to Automation**
```python
# Add to orchestrator
# Run daily
# Update Senate FD data
```

---

## Timeline & Effort

| Option | Setup Time | Monthly Cost | Coverage | Maintenance |
|--------|-----------|--------------|----------|-------------|
| **A: Free** | 2-3 hrs | $10 | 2004+ | Low |
| **B: LegiStorm** | <1 hr | $1000+ | 2007+ | None |
| **C: Hybrid** | 1-2 hrs | $10-510 | 2004+ | Low |

---

## What We're Missing Currently

**Current State**:
- House FD: 2024-2025 only (707 reports, 519 parsed)
- Senate FD: None (use QuiverQuant trades instead)
- Trades: 2018-2026 (9,777 trades via QuiverQuant)

**What We Can Add**:
- House FD: 2004-2023 (20 additional years)
- Senate FD: 1990-2026 (36 years available)
- Trades: Already have 2018-2026

**Gap**: Senate annual disclosures (will use QuiverQuant trades + OpenSecrets)

---

## Recommended Action Plan

### Immediate (This Week)
1. ✅ Test House Clerk for 2004-2023 XML files
2. ✅ Download ProPublica datasets ($1-99)
3. ✅ Download OpenSecrets CSV (free)

### Short-term (1-2 Weeks)
1. ✅ Create OpenSecrets ingestion
2. ✅ Backfill House FD 2004-2023
3. ✅ Import ProPublica data

### Medium-term (Optional)
1. Build Senate eFD Playwright scraper
2. Add daily scraping to pipeline
3. Complete Senate 2012-2026 coverage

---

## Cost Comparison Summary

| Scenario | One-time | Monthly | Annual | Coverage |
|----------|----------|---------|--------|----------|
| Free (A) | $0-99 | $10 | $120 | 2004+ |
| LegiStorm (B) | $0 | $1000 | $12000 | 2007+ |
| Hybrid (C) | $0-99 | $10-510 | $150-6210 | 2004+ |

**Best Value**: Option A (Free) = $120/year for 20+ years of data

---

## Next Steps

Which would you like me to implement?

1. **Download & Parse House Clerk 2004-2023** (2-3 hours, free)
2. **Create OpenSecrets Ingestion** (2-3 hours, free)
3. **Buy & Import ProPublica Data** ($1-99, 1 hour)
4. **Build Senate eFD Scraper** (5-6 hours, free)
5. **All of the above** (Priority order: 1, 2, 3, 4)

Ready? Tell me which you prefer and I'll start implementation!

