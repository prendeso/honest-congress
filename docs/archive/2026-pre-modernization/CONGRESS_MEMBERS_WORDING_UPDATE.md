# Congress Members Wording Update - Option 1 Implemented

## Changes Made

### ✅ Updated Congress Members Card (Landing Page)
**Before:**
- Subtitle: "House • Senate • Historical"

**After:**
- Subtitle: "Past & Present House • Senate"

### ✅ Updated Congress Members Card (Anomalies Dashboard)
**Before:**
- Subtitle: "House • Senate • Historical"

**After:**
- Subtitle: "Past & Present House • Senate"

### ✅ Updated Members Page Description
**Before:**
- "Members from House, Senate, and historical records with advanced filtering options"

**After:**
- "All members of Congress past and present from House, Senate, and historical records"

## Why This Wording Is Better

1. **More Explicit**: "Past & Present" immediately clarifies that the data includes retired members
2. **Clearer**: Avoids confusing term "Historical" which some might interpret as only historical data
3. **More Concise**: Shorter subtitle that still conveys all necessary information
4. **Consistent**: Aligns with the "(Retired)" labels shown on member names throughout the application
5. **User Friendly**: Users immediately understand they'll see both active and retired members

## Data Included

The "Congress Members" section now explicitly indicates it includes:
- ✅ **Current/Active members** from House and Senate
- ✅ **Retired/Past members** from House and Senate
- ✅ **All relevant data** regardless of current status

## Files Modified
- `src/api/routes/dashboard_v2.py`
  - Line ~45: Updated landing page card subtitle
  - Line ~937: Updated anomalies dashboard stats bar card
  - Line ~1802: Updated members page description

## Status
✅ **Complete** - All Congress Members references updated to clearly indicate "Past & Present"

