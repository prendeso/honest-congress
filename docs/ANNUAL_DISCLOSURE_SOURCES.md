# Annual Financial Disclosure Sources - Complete Guide

## Overview
Multiple sources available for Congressional financial disclosures with varying historical coverage.

---

## 1. **House Clerk Official - BEST FOR RECENT HOUSE DATA** ✅
- **URL**: https://disclosures-clerk.house.gov/
- **Data Type**: House FD reports (PDF + XML)
- **Historical**: 2004-2024 (✅ 20 years!)
- **Format**: PDF documents + XML index files
- **Cost**: Free
- **Access**: Direct download, XML API
- **Rate Limit**: Reasonable
- **Coverage**: House only
- **Status**: ✅ We already use this (519/707 parsed)
- **Next**: Can backfill 2004-2023 historical years

---

## 2. **Senate eFD Database - BEST FOR SENATE** ✅
- **URL**: https://efdsearch.senate.gov/
- **Data Type**: Senate FD reports
- **Historical**: 2012-2026 (✅ 14 years)
- **Format**: Web interface, PDF download
- **Cost**: Free
- **Access**: Web scraping (Playwright needed)
- **Rate Limit**: ⚠️ Anti-bot protection (503 errors)
- **Coverage**: Senate only
- **Status**: Blocked by anti-bot. Need Playwright scraper
- **Alternative**: Use OpenSecrets or LegiStorm for Senate

---

## 3. **OpenSecrets (Center for Responsive Politics)** ✅
- **URL**: https://www.opensecrets.org/
- **Data Type**: FD + campaign finance + conflict tracking
- **Historical**: 1990-2026 (✅ 36 years!)
- **Format**: CSV export, API access
- **Cost**: 
  - Free tier: Limited data, 50 requests/day
  - Paid API: $0-500+/month depending on tier
- **Access**: 
  - Free: Download CSV files manually
  - Paid: Programmatic API access
- **Rate Limit**: Free: 50/day | Paid: Varies
- **Coverage**: House + Senate
- **Status**: 🟡 FREE TIER AVAILABLE - Good starting point!
- **Benefit**: Already processes FD data from official sources

---

## 4. **LegiStorm - BEST FOR COMPREHENSIVE DATA** ✅
- **URL**: https://www.legistorm.com/
- **Data Type**: FD reports + employment records + other disclosures
- **Historical**: 2007-2026 (✅ 19 years)
- **Format**: Database + CSV export
- **Cost**: $500-2000+/month (subscription only)
- **Access**: Paid subscription with data download
- **Rate Limit**: N/A (subscription service)
- **Coverage**: House + Senate
- **Status**: 💰 PAID - Most complete data but expensive
- **Benefit**: Pre-parsed, cleaned data (no PDF parsing needed)

---

## 5. **ProPublica Data Store - BEST FOR CHEAP HISTORICAL** ✅
- **URL**: https://stores.propublica.org/
- **Data Type**: Historical FD datasets
- **Historical**: 2004-2014 (✅ 10 years)
- **Format**: CSV files ready to use
- **Cost**: $0-99 per dataset (one-time purchase)
- **Access**: Direct download
- **Rate Limit**: N/A
- **Coverage**: House + Senate
- **Status**: 🟢 CHEAP & EASY - Great for historical backfill
- **Benefit**: Pre-cleaned, no parsing needed

---

## 6. **SEC EDGAR - SUPPLEMENTARY DATA**
- **URL**: https://www.sec.gov/cgi-bin/browse-edgar
- **Data Type**: Officer/director stock holdings
- **Historical**: 1990-2026 (✅ 36 years)
- **Format**: XML, HTML, CSV
- **Cost**: Free
- **Access**: Direct download, FTP API
- **Rate Limit**: Good
- **Coverage**: Public company officers only
- **Status**: 🟡 SUPPLEMENTARY - For verification only
- **Benefit**: Cross-reference with congressional trades

---

## 7. **Archive.org (Wayback Machine) - OLDEST DATA**
- **URL**: https://web.archive.org/
- **Data Type**: Historical House Clerk pages (snapshots)
- **Historical**: 2000+ (✅ 26+ years)
- **Format**: HTML/PDF snapshots
- **Cost**: Free
- **Access**: Web scraping + Playwright
- **Rate Limit**: Respectful scraping only
- **Coverage**: Whatever was available at time
- **Status**: 🟡 DIFFICULT - Requires scraping snapshots
- **Benefit**: Pre-2004 data if needed

---

## 8. **Congress.gov API - NOT RECOMMENDED**
- **URL**: https://api.congress.gov/
- **Data Type**: Very limited FD references
- **Historical**: Limited
- **Format**: JSON API
- **Cost**: Free (API key required)
- **Access**: API
- **Rate Limit**: Reasonable
- **Coverage**: References only
- **Status**: ❌ AVOID - Minimal FD data

---

## Recommended Strategy

### **IMMEDIATE (Free/Cheap): Fill Historical Gap**

**For 2004-2023 House Data**:
```
1. Try House Clerk XML for 2004-2023 (free, probably available)
2. If not available, use ProPublica datasets 2004-2014 ($1-99)
3. Fill any gaps with Wayback Machine (free)
```

**For 2012-2023 Senate Data**:
```
1. Use OpenSecrets free tier (limited but useful)
2. OR buy LegiStorm one-time dump (~$500-2000)
3. OR use Playwright scraper for Senate eFD (free but risky with 503s)
```

### **RECOMMENDED COMBINATION**:

| Years | House | Senate | Method |
|-------|-------|--------|--------|
| 2004-2023 | House Clerk XML | OpenSecrets | Free |
| 2024-2026 | House Clerk XML | QuiverQuant + Playwright | Free + $10/mo |

**Total Cost**: $10/month (just for QuiverQuant)  
**Coverage**: 2004-2026 (22 years of data)  
**Effort**: Medium (Playwright scraper for Senate)

---

## Alternative: Paid Solution

**If budget available ($1000/month)**:
- Use **LegiStorm** exclusively
  - Pre-parsed, cleaned data
  - No PDF parsing needed
  - 2007-2026 coverage
  - One simple CSV export
  - **Cost**: ~$1000-2000/month
  - **Effort**: Minimal (just download CSV)

---

## Action Items

### Option A: FREE ROUTE (Recommended)
1. ✅ Download House Clerk XML for 2004-2023
2. ✅ Use ProPublica datasets for 2004-2014 (backup/$1-99)
3. ✅ Build Playwright scraper for Senate eFD (Phase 9)
4. ✅ Keep QuiverQuant for trades ($10/mo)
5. **Cost**: $10/month + 5-6 hours development

### Option B: PAID ROUTE (Easier)
1. ✅ Subscribe to LegiStorm ($1000-2000/month)
2. ✅ Download CSV exports monthly
3. ✅ Stop using House Clerk XML (LegiStorm has same data)
4. ✅ Keep QuiverQuant for real-time trades ($10/mo)
5. **Cost**: $1000+ /month, zero development

### Option C: HYBRID ROUTE
1. ✅ Use ProPublica datasets 2004-2014 ($1-99)
2. ✅ Use House Clerk XML 2015-2024 (free)
3. ✅ Use OpenSecrets free tier (reference)
4. ✅ Use LegiStorm for recent Senate data (if budget allows)
5. ✅ Keep QuiverQuant ($10/mo)
6. **Cost**: $10-1000/month depending on Senate choice

---

## Summary: Ways to Get Historical FD Data

| Source | Years | House | Senate | Cost | Effort |
|--------|-------|-------|--------|------|--------|
| House Clerk XML | 2004-2024 | ✅ | ❌ | Free | Low |
| OpenSecrets | 1990-2026 | ✅ | ✅ | Free | Low |
| ProPublica | 2004-2014 | ✅ | ✅ | $1-99 | Low |
| LegiStorm | 2007-2026 | ✅ | ✅ | $1000+ | None |
| Senate eFD | 2012-2026 | ❌ | ✅ | Free | High* |
| SEC EDGAR | 1990-2026 | ✅ | ✅ | Free | Medium |
| Wayback Machine | 2000-2026 | ✅ | ✅ | Free | High |

*High effort = Need Playwright scraper + anti-bot workarounds

---

## Next Steps

**Recommend**: Try FREE route first
1. Download House Clerk XML 2004-2024
2. Parse with existing pipeline
3. Fill Senate gap with OpenSecrets free tier
4. If comfortable, add Playwright scraper later
5. Total investment: 5-10 hours development, $10/month

Would you like me to implement any of these sources?

