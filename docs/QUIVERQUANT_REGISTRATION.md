# Phase 7.4: QuiverQuant API Registration Guide

## Overview
QuiverQuant provides congressional stock trading data via a free API tier. We'll use this to get PTR (Periodic Transaction Report) data when the House Clerk XML is unavailable.

## Steps to Register & Get API Key

### 1. Go to QuiverQuant Website
- **URL**: https://www.quiverquant.com/
- Click on "Sign Up" or "Get Free API Key"

### 2. Create Account
- Email address
- Password
- Accept terms

### 3. Navigate to API Section
- Once logged in, go to: Dashboard → API → Documentation or "Get API Key"
- Copy your API key (looks like: `abc123def456...`)

### 4. Store API Key Securely
```bash
# Add to .env file
QUIVERQUANT_API_KEY=your_api_key_here
```

### 5. Test the API
```bash
# Once you have the key, we'll test it with:
python -m src.cli performance  # This will use QuiverQuant if available
```

## API Endpoints We'll Use

| Endpoint | Purpose |
|----------|---------|
| `/beta/live/housetrading` | House stock trades |
| `/beta/live/senatetrading` | Senate stock trades |
| `/beta/historical/` | Historical trade data |

## Free Tier Limits
- Typically 200-500 API calls per day
- Should be sufficient for our ingestion needs

## Next Steps
1. Register at https://www.quiverquant.com/
2. Get your API key
3. Add to `.env` file
4. Return here so I can build the `quiverquant.py` adapter

## Questions?
See https://www.quiverquant.com/docs/ for full API documentation

