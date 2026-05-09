# Fix Applied: Member Anomalies Modal 422 Error

## Problem
When clicking a member row, the API returned:
```
422 Unprocessable Content
GET /api/anomalies?member_id=84&page_size=200
```

## Root Cause
The `/api/anomalies` endpoint in `src/api/routes/anomalies.py` has a maximum `page_size` limit of 100:

```python
page_size: int = Query(50, ge=1, le=100)  # max is 100
```

But our JavaScript was requesting 200 anomalies:
```javascript
const url = `/api/anomalies?member_id=${member.id}&page_size=200`;
```

## Fix Applied
Changed the JavaScript in `src/api/routes/dashboard_v2.py` (line ~602):

**Before:**
```javascript
const url = `/api/anomalies?member_id=${member.id}&page_size=200`;
```

**After:**
```javascript
const url = `/api/anomalies?member_id=${member.id}&page_size=100`;
```

## Verification
Tested with:
```bash
curl http://localhost:8000/api/anomalies?member_id=84&page_size=100
```

Result: ✅ **200 OK - Success!**

## Status
✅ **FIXED** - The member anomalies modal now works correctly when clicking any member row.

## To Use
1. Restart server (if needed): `python start_server.py`
2. Go to: http://localhost:8000/members
3. Click any member row
4. Modal appears with their anomalies (up to 100 most recent)

## Note
If a member has more than 100 anomalies, only the first 100 will be shown in the modal. Users can click "View in anomalies page" to see all anomalies with pagination.


