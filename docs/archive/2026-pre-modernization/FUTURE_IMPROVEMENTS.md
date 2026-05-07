# Future Improvements & Roadmap

**Last Updated**: February 2, 2026

---

## Phase 2: Enhanced UI & Admin Tools

### Completed ✅
- [x] Maintenance panel with admin login (password-protected)
- [x] Cleanup invalid anomalies (where value = threshold)
- [x] Run analysis button (re-detect all anomalies)
- [x] Regenerate button (full anomaly rebuild)
- [x] Admin authentication via X-Admin-Token header
- [x] Session-based admin tokens (8-hour expiry)

### In Progress 🔄
- [ ] Additional admin maintenance tasks
- [ ] Batch operations on anomalies
- [ ] Data export functionality
- [ ] Advanced filtering options

---

## Phase 3: Data Quality & Completeness

### Data Source Expansion
- [ ] Add Senate EFD API integration (100 Senators)
- [ ] Implement incremental sync (only new disclosures)
- [ ] Add historical data backfill (2020-2025)
- [ ] Cache API responses to reduce rate limiting

### Parsing Improvements
- [ ] Handle Form 3 (initial filings) in addition to current forms
- [ ] Improve PDF extraction accuracy (currently ~85%)
- [ ] Add committee assignment parsing
- [ ] Track trading volume by sector

---

## Phase 4: Analysis Enhancements

### Anomaly Detection Expansions
- [ ] **Sector Rotation Anomalies**: Trading patterns that follow committee assignments
- [ ] **Insider Trading Red Flags**: Trades preceding major company news
- [ ] **Coordinated Trading**: Unusual patterns across member groups
- [ ] **Family Member Trading**: Track spouses/dependents
- [ ] **Regulatory Arbitrage**: Trades before/after voted legislation

### Severity Scoring Improvements
- [ ] ML-based severity calculation (replace threshold-based)
- [ ] Historical baseline comparison
- [ ] Peer group benchmarking
- [ ] Volatility-adjusted anomaly detection

---

## Phase 5: Reporting & Compliance

### Reports
- [ ] PDF export of anomalies with evidence
- [ ] Quarterly compliance reports
- [ ] Member-specific audit trails
- [ ] Sector trend analysis

### Integrations
- [ ] EDGAR filing integration (corporate disclosures)
- [ ] Stock price API (for timing analysis)
- [ ] Campaign contribution tracking
- [ ] Committee assignment feed

---

## Phase 6: Performance & Scalability

### Infrastructure
- [ ] PostgreSQL migration (from SQLite)
- [ ] Redis caching for API responses
- [ ] Async task queue for background analysis
- [ ] Docker containerization

### Optimization
- [ ] Index database queries
- [ ] Implement materialized views
- [ ] API response pagination
- [ ] Query result caching

---

## Phase 7: Advanced Features

### Machine Learning
- [ ] Anomaly clustering
- [ ] Predictive anomaly detection
- [ ] Member risk scoring
- [ ] Temporal pattern analysis

### Visualization
- [ ] Interactive timeline of trades
- [ ] Network graph of trading relationships
- [ ] Sector concentration charts
- [ ] Wealth trajectory graphs

---

## Known Limitations & TODOs

### Current Issues
- [ ] NVDA/AAPL mix-up on large trades (partially fixed, needs full validation)
- [ ] Multiple years' large trades collapse to single anomaly (FIXED)
- [ ] Dashboard slow on initial load with 5000+ anomalies
- [ ] PDF extraction misses some transaction details

### Data Gaps
- [ ] Senate PTR data incomplete (missing 2020-2022)
- [ ] Historical member data sparse (pre-2010)
- [ ] Some PDF scans don't convert to text cleanly
- [ ] Committee assignments not fully tracked

---

## Developer Notes

### Setup Reminders
1. Set `ADMIN_PASSWORD` in `.env` for maintenance panel
2. Use `.env.example` as reference (don't commit `.env`)
3. Database auto-initializes on first run
4. Run `python start_server.py` to start web UI

### Testing Checklist Before Deploy
- [ ] No hardcoded API keys or passwords
- [ ] All .env variables documented in .env.example
- [ ] Database migrations tested
- [ ] API endpoints responding correctly
- [ ] Dashboard loads without errors
- [ ] Admin functions accessible with password

---

## Community Contributions Welcome

If you'd like to contribute:
1. Pick an item from this roadmap
2. Create an issue describing your approach
3. Submit a pull request with tests
4. Help us improve congressional transparency!

---

## References

- [STOCK Act (2012)](https://www.congress.gov/bill/112th-congress/senate-bill/2038)
- [Official House Disclosures](https://disclosures-clerk.house.gov/)
- [Official Senate Disclosures](https://efdsearch.senate.gov/)
- [Congress.gov API](https://api.congress.gov/)

