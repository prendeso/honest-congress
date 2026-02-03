# Honest Congress - System Architecture

**Version**: 1.0  
**Last Updated**: February 1, 2026

---

## Overview

Honest Congress is a Congressional financial disclosure analyzer that detects anomalies in wealth accumulation, stock trading patterns, and asset appreciation among members of the U.S. Congress.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         HONEST CONGRESS ARCHITECTURE                        │
└─────────────────────────────────────────────────────────────────────────────┘

    ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
    │   DATA SOURCES   │     │   DATA SOURCES   │     │   DATA SOURCES   │
    │  (House Clerk)   │     │  (QuiverQuant)   │     │  (Congress.gov)  │
    │   FD XML/PDF     │     │   API ($10/mo)   │     │   Members CSV    │
    └────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
             │                        │                        │
             ▼                        ▼                        ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                         INGESTION LAYER                                 │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
    │  │   house.py  │  │ quiverquant │  │  members.py │  │ orchestrator│    │
    │  │  (FD XML)   │  │    .py      │  │ (Congress)  │  │    .py      │    │
    │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
    └─────────────────────────────────────────────────────────────────────────┘
             │                        │                        │
             ▼                        ▼                        ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                         PARSING LAYER                                   │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                     │
    │  │ pdf_parser  │  │ fd_asset    │  │ fd_income   │                     │
    │  │    .py      │  │  _parser.py │  │  _parser.py │                     │
    │  └─────────────┘  └─────────────┘  └─────────────┘                     │
    └─────────────────────────────────────────────────────────────────────────┘
             │                        │                        │
             ▼                        ▼                        ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                         DATABASE LAYER (SQLite)                         │
    │  ┌─────────┐ ┌─────────────┐ ┌────────────┐ ┌────────┐ ┌─────────┐     │
    │  │ Members │ │ Disclosures │ │Transactions│ │ Assets │ │Anomalies│     │
    │  │  (547)  │ │   (5,690)   │ │   (9,777)  │ │(6,308) │ │  (184)  │     │
    │  └─────────┘ └─────────────┘ └────────────┘ └────────┘ └─────────┘     │
    └─────────────────────────────────────────────────────────────────────────┘
             │                        │                        │
             ▼                        ▼                        ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                         ANALYSIS LAYER                                  │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
    │  │   wealth    │  │   trade     │  │  advanced   │  │  extended   │    │
    │  │ _analyzer   │  │ _analyzer   │  │ _anomaly    │  │ _anomaly    │    │
    │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
    │                                                                         │
    │  7 Anomaly Types:                                                       │
    │  1. Net Worth vs Salary    5. Committee Conflicts                       │
    │  2. Asset Appreciation     6. Loss Avoidance                            │
    │  3. Stock Outperformance   7. Multi-Factor Risk                         │
    │  4. Trade Timing                                                        │
    └─────────────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                         API LAYER (FastAPI)                             │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
    │  │  /members   │  │/disclosures │  │ /anomalies  │  │ /dashboard  │    │
    │  │    API      │  │    API      │  │    API      │  │   (HTML)    │    │
    │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
    └─────────────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                         PRESENTATION LAYER                              │
    │  ┌─────────────────────────────────────────────────────────────────┐   │
    │  │                    Web Dashboard (localhost:8001)                │   │
    │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐            │   │
    │  │  │ Members │  │Disclos- │  │ Trades  │  │Anomalies│            │   │
    │  │  │   Tab   │  │ures Tab │  │   Tab   │  │   Tab   │            │   │
    │  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘            │   │
    │  └─────────────────────────────────────────────────────────────────┘   │
    └─────────────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
honest-congress/
├── src/
│   ├── analysis/                 # Anomaly detection modules
│   │   ├── advanced_anomaly_detector.py   # Net worth, asset, stock anomalies
│   │   ├── extended_anomaly_detector.py   # Timing, conflicts, risk scoring
│   │   ├── trade_analyzer.py              # Trade pattern analysis
│   │   ├── wealth_analyzer.py             # Wealth growth analysis
│   │   └── performance_analyzer.py        # Benchmark comparison
│   │
│   ├── api/                      # FastAPI web server
│   │   ├── main.py               # API entry point
│   │   └── routes/               # API endpoints
│   │       ├── anomalies.py      # /api/anomalies
│   │       ├── dashboard.py      # Web dashboard HTML
│   │       ├── disclosures.py    # /api/disclosures
│   │       ├── members.py        # /api/members
│   │       └── performance.py    # /api/performance
│   │
│   ├── db/                       # Database layer
│   │   ├── database.py           # SQLAlchemy session
│   │   └── models.py             # Data models
│   │
│   ├── ingestion/                # Data collection
│   │   ├── house.py              # House Clerk FD XML
│   │   ├── members.py            # Congress members CSV
│   │   ├── quiverquant.py        # QuiverQuant API trades
│   │   └── orchestrator.py       # Ingestion coordinator
│   │
│   ├── parsing/                  # Data extraction
│   │   ├── pdf_parser.py         # PDF parsing
│   │   ├── fd_asset_parser.py    # Asset extraction
│   │   ├── fd_income_parser.py   # Income extraction
│   │   └── committee_conflict_mapper.py  # Committee mapping
│   │
│   ├── cli.py                    # Command-line interface
│   └── config.py                 # Configuration
│
├── scripts/                      # Utility scripts
│   └── run_complete_anomaly_detection.py
│
├── data/                         # Data storage
│   ├── congress.db               # SQLite database
│   └── pdfs/                     # Downloaded PDFs
│
├── docs/                         # Documentation
│   └── QUIVERQUANT_REGISTRATION.md
│
├── ARCHITECTURE.md               # This file
├── IMPLEMENTATION_PLAN.md        # Implementation roadmap
├── README.md                     # Quick start guide
└── requirements.txt              # Python dependencies
```

---

## Data Models

### Core Entities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATABASE SCHEMA                                   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────┐       ┌─────────────────┐       ┌─────────────────┐
│   Member    │       │   Disclosure    │       │   Transaction   │
├─────────────┤       ├─────────────────┤       ├─────────────────┤
│ id (PK)     │──┐    │ id (PK)         │──┐    │ id (PK)         │
│ bioguide_id │  │    │ member_id (FK)  │◀─┘    │ disclosure_id   │
│ first_name  │  │    │ filing_type     │       │ member_id (FK)  │
│ last_name   │  │    │ filing_year     │       │ ticker          │
│ party       │  │    │ filing_date     │       │ type (buy/sell) │
│ chamber     │  │    │ document_id     │       │ amount          │
│ state       │  │    │ parsed          │       │ transaction_date│
│ district    │  │    │ source          │       │ source          │
│ in_office   │  │    └─────────────────┘       └─────────────────┘
└─────────────┘  │
                 │    ┌─────────────────┐       ┌─────────────────┐
                 │    │      Asset      │       │    Anomaly      │
                 │    ├─────────────────┤       ├─────────────────┤
                 │    │ id (PK)         │       │ id (PK)         │
                 └───▶│ disclosure_id   │       │ member_id (FK)  │◀─┘
                      │ asset_type      │       │ disclosure_id   │
                      │ description     │       │ anomaly_type    │
                      │ value_min       │       │ severity        │
                      │ value_max       │       │ title           │
                      │ income_min      │       │ description     │
                      │ income_max      │       │ computed_value  │
                      └─────────────────┘       │ detected_at     │
                                               └─────────────────┘
```

### Data Counts (Current)

| Table | Records | Description |
|-------|---------|-------------|
| Members | 547 | Current Congress members |
| Disclosures | 5,690 | FD + PTR filings (2015-2026) |
| Transactions | 9,777 | Stock trades (House + Senate) |
| Assets | 6,308 | From parsed disclosures |
| Anomalies | 184 | Detected anomalies |

---

## Anomaly Detection System

### 7 Anomaly Types

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ANOMALY DETECTION PIPELINE                          │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────────────┐
                    │        ADVANCED DETECTOR            │
                    │   (advanced_anomaly_detector.py)    │
                    └─────────────────────────────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          ▼                         ▼                         ▼
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│   ANOMALY 1     │       │   ANOMALY 2     │       │   ANOMALY 3     │
│ Net Worth vs    │       │    Asset        │       │    Stock        │
│    Salary       │       │ Appreciation    │       │ Outperformance  │
├─────────────────┤       ├─────────────────┤       ├─────────────────┤
│ Compares wealth │       │ Tracks assets   │       │ Compares trades │
│ growth to total │       │ year-over-year  │       │ to S&P 500      │
│ possible salary │       │ for >100%/year  │       │ benchmark       │
│                 │       │                 │       │                 │
│ Threshold: 1.5x │       │ Threshold: 100% │       │ Threshold: 50%  │
│ salary growth   │       │ annual growth   │       │ excess return   │
└─────────────────┘       └─────────────────┘       └─────────────────┘

                    ┌─────────────────────────────────────┐
                    │        EXTENDED DETECTOR            │
                    │   (extended_anomaly_detector.py)    │
                    └─────────────────────────────────────┘
                                    │
    ┌───────────────┬───────────────┼───────────────┬───────────────┐
    ▼               ▼               ▼               ▼               ▼
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐
│ANOMALY 4│   │ANOMALY 5│   │ANOMALY 6│   │ANOMALY 7│   │  RISK   │
│  Trade  │   │Committee│   │  Loss   │   │ Multi-  │   │ SCORING │
│ Timing  │   │Conflicts│   │Avoidance│   │ Factor  │   │  SYSTEM │
├─────────┤   ├─────────┤   ├─────────┤   ├─────────┤   ├─────────┤
│ Perfect │   │ Trading │   │ 80%+    │   │ 3+ diff │   │ CRITICAL│
│ timing, │   │ in over-│   │ success │   │ anomaly │   │ HIGH    │
│ consecu-│   │ sight   │   │ rate on │   │ types = │   │ MEDIUM  │
│ tive    │   │ sectors │   │ trades  │   │ priority│   │ LOW     │
│ trades  │   │         │   │         │   │         │   │         │
└─────────┘   └─────────┘   └─────────┘   └─────────┘   └─────────┘
```

### Severity Levels

| Severity | Risk Score | Action Required |
|----------|------------|-----------------|
| CRITICAL | 6+ | Immediate investigation |
| HIGH | 4-5 | Detailed investigation |
| MEDIUM | 2-3 | Monitor and verify |
| LOW | 0-1 | Note for trends |

---

## Data Sources

### Active Sources

| Source | Type | Cost | Data |
|--------|------|------|------|
| **House Clerk XML** | FD Reports | Free | 4,742 disclosures |
| **QuiverQuant API** | Stock Trades | $10/mo | 9,777 trades |
| **congress-legislators** | Member Data | Free | 547 members |

### Data Flow

```
┌────────────────┐    ┌────────────────┐    ┌────────────────┐
│  House Clerk   │    │  QuiverQuant   │    │ Congress.gov   │
│   XML/PDF      │    │     API        │    │    CSV         │
└───────┬────────┘    └───────┬────────┘    └───────┬────────┘
        │                     │                     │
        ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│              Ingestion Layer (orchestrator.py)              │
│                                                             │
│  1. Fetch XML index  →  2. Download PDFs  →  3. Import     │
│  4. Match members    →  5. Store records  →  6. Dedupe     │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                    SQLite Database                          │
│  Members(547) + Disclosures(5,690) + Transactions(9,777)   │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                  Analysis Layer                             │
│  Advanced Detector (3 types) + Extended Detector (4 types) │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                   Web Dashboard                             │
│  Members | Disclosures | Trades | Anomalies (detailed)     │
└─────────────────────────────────────────────────────────────┘
```

---

## API Endpoints

### Members API

```
GET /api/members                    List members with filters
GET /api/members/{id}               Get member details
GET /api/members/{id}/disclosures   Get member's disclosures
GET /api/members/{id}/anomalies     Get member's anomalies
```

### Disclosures API

```
GET /api/disclosures                List disclosures
GET /api/disclosures/{id}           Get disclosure details
GET /api/disclosures/{id}/assets    Get disclosure assets
```

### Anomalies API

```
GET /api/anomalies                  List anomalies with filters
GET /api/anomalies/summary          Get anomaly statistics
GET /api/anomalies/{id}             Get anomaly details
POST /api/anomalies/{id}/review     Mark anomaly reviewed
POST /api/anomalies/analyze         Trigger analysis
```

### Dashboard

```
GET /                               Web dashboard
GET /docs                           API documentation (Swagger)
```

---

## Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| **Language** | Python | 3.10+ |
| **Web Framework** | FastAPI | 0.100+ |
| **Database** | SQLite | 3.x |
| **ORM** | SQLAlchemy | 2.0+ |
| **PDF Parsing** | PyPDF2, pdfplumber | Latest |
| **HTTP Client** | httpx | Latest |
| **Market Data** | yfinance | Latest |
| **Frontend** | Tailwind CSS, Alpine.js | CDN |

---

## Deployment

### Local Development

```bash
# 1. Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1  # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your QuiverQuant API key

# 4. Initialize database
python -m src.cli init

# 5. Ingest data
python -m src.cli ingest -y 2024 2025
python -m src.cli ingest-trades

# 6. Run analysis
python -m src.cli analyze

# 7. Start server
python -m src.cli serve --port 8001
```

### Production Considerations

- [ ] Migrate to PostgreSQL for better concurrency
- [ ] Add authentication/authorization
- [ ] Set up cron job for daily data refresh
- [ ] Add logging and monitoring
- [ ] Deploy behind reverse proxy (nginx)

---

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| API Key Exposure | Store in `.env`, never commit |
| SQL Injection | SQLAlchemy ORM parameterization |
| XSS | No user-generated content |
| Rate Limiting | Respect QuiverQuant limits |

---

## Future Enhancements

1. **Real-time Alerts** - Email on new high-severity anomalies
2. **Legislative Correlation** - Match trades to committee votes
3. **Historical Analysis** - Import historical data beyond current window
4. **Export Features** - CSV/PDF report generation
5. **Public API** - Rate-limited public access to anomaly data

---

## Support

- **Documentation**: See `/docs` on running server
- **Issues**: GitHub Issues
- **Data Questions**: Refer to official House/Senate disclosure offices

