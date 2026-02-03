# Honest Congress

A Python application that analyzes congressional financial disclosures to detect anomalies and potential inconsistencies in reported wealth and investments.

## Features

- **Data Ingestion**: Pulls financial disclosures from House Clerk XML and QuiverQuant API
- **Member Metadata**: 547 current Congress members from congress-legislators
- **PDF Parsing**: Extracts assets, transactions from disclosure documents
- **Anomaly Detection**: 7 detection types including:
  - Net worth vs salary comparison
  - Asset appreciation tracking
  - Stock performance vs benchmarks
  - Trade timing analysis
  - Committee conflict detection
  - Loss avoidance patterns
  - Multi-factor risk scoring
- **Web Dashboard**: View members, disclosures, trades, and anomalies
- **REST API**: FastAPI-based API for all data

## Current Status

| Component | Records |
|-----------|---------|
| Members | 547 |
| Disclosures | 5,690 |
| Stock Trades | 9,777 |
| Assets | 6,308 |
| **Anomalies Detected** | **188** |

## Quick Start

### Prerequisites

- Python 3.10+
- QuiverQuant API key ($10/month) - Optional for trade data

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/honest-congress.git
cd honest-congress

# Create virtual environment
python -m venv venv
.\venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
copy .env.example .env
# Edit .env with your QuiverQuant API key (optional)
```

### Run the Application

```bash
# Initialize database and ingest data
python -m src.cli init
python -m src.cli ingest -y 2024 2025

# Run anomaly detection
python -m src.cli analyze

# Start web dashboard
python -m src.cli serve --port 8001
```

Open http://localhost:8001 in your browser.

## Dashboard

The web dashboard has 4 tabs:

1. **Members** - Browse 547 Congress members with search and filter
2. **Disclosures** - View financial disclosure filings
3. **Stock Trades** - 9,777 congressional stock trades
4. **Anomalies** - 188 detected anomalies with detailed explanations

### Anomaly Types

| Type | Description |
|------|-------------|
| **Wealth vs Salary** | Net worth grew faster than salary could explain |
| **Asset Appreciation** | Single asset grew >100% in one year |
| **Stock Outperformance** | Trading returns beat S&P 500 significantly |
| **Trade Timing** | Perfect timing, consecutive trades, volume spikes |
| **Committee Conflict** | Trading in sectors member oversees |
| **Loss Avoidance** | 80%+ success rate (statistically improbable) |
| **Multi-Factor Risk** | Member has 3+ different anomaly types |

## API Endpoints

```
GET /api/members           List members
GET /api/disclosures       List disclosures
GET /api/anomalies         List anomalies
GET /api/anomalies/summary Anomaly statistics
GET /docs                  API documentation
```

## Data Sources

| Source | Data | Cost |
|--------|------|------|
| House Clerk XML | FD Reports | Free |
| QuiverQuant API | Stock Trades | $10/mo |
| congress-legislators | Members | Free |

## Project Structure

```
honest-congress/
├── src/
│   ├── analysis/     # Anomaly detection (7 types)
│   ├── api/          # FastAPI web server
│   ├── db/           # Database models
│   ├── ingestion/    # Data collection
│   ├── parsing/      # PDF/XML parsing
│   └── cli.py        # Command-line interface
├── data/             # SQLite database
├── ARCHITECTURE.md   # System architecture
├── IMPLEMENTATION_PLAN.md  # Roadmap
└── README.md         # This file
```

## Documentation

- **ARCHITECTURE.md** - System architecture, data models, diagrams
- **IMPLEMENTATION_PLAN.md** - Implementation status and pending items
- **/docs** (on running server) - Swagger API documentation

## Commands

```bash
# Activate virtual environment
.\venv\Scripts\activate

# Ingest data
python -m src.cli ingest -y 2024 2025
python -m src.cli ingest-trades

# Parse disclosures
python -m src.cli parse --limit 100

# Run analysis
python -m src.cli analyze

# Run all 7 anomaly types
python scripts/run_complete_anomaly_detection.py

# Start dashboard
python -m src.cli serve --port 8001
```

## Documentation

For detailed information, see:

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design, data flow, and technical overview
- **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)** - Current status, tech stack, and deployment guide
- **[FUTURE_IMPROVEMENTS.md](FUTURE_IMPROVEMENTS.md)** - Roadmap for phases 2-7
- **[FIXES_APPLIED.md](FIXES_APPLIED.md)** - Recent fixes and improvements
- **[docs/](docs/)** - Additional reference materials

## License

MIT License - See LICENSE file

