# Fix: Members Table Sorting Only 50 Members Issue

## Problem
After implementing the full dataset sorting, the table was STILL only sorting the 50 visible members on the current page, not the complete dataset.

## Root Causes Found

### Issue 1: Flow Control
The `loadMembers()` function was loading 50 members first, then calling `applySorting()`, but the sorting logic wasn't properly replacing the page data with the full dataset.

**Fix**: Added early return in `loadMembers()` to skip paginated load when sorting by non-name fields.

### Issue 2: Total Count Not Available  
When `applySorting()` tried to fetch all members with `page_size=${this.total}`, `this.total` was 0 because no data had been loaded yet.

**Fix**: Added a pre-fetch to get the total count before fetching all members.

### Issue 3: API Page Size Limit (THE MAIN ISSUE)
The `/api/members` endpoint had a **maximum page_size of 500**! When requesting all members (e.g., 12,000+), the API was silently limiting to 500 members.

**Fix**: Increased the API page_size limit from 500 to 50,000.

## Solution
Three fixes were applied:

### Fix 1: Early Return in loadMembers()
Skip the paginated load when sorting by non-name fields:

```javascript
async loadMembers() {
    if (this.loading) return;
    
    // KEY FIX: Skip page load if sorting by non-name field
    if (this.sortField !== 'name') {
        await this.applySorting();  // Go directly to full dataset fetch
        return;                      // Skip the paginated load below
    }
    
    // Normal page load for name sort or initial load
    this.loading = true;
    // ... fetch 50 members with pagination
}
```

### Fix 2: Pre-fetch Total Count in applySorting()
Get the total count before fetching all members:

```javascript
async applySorting() {
    // First, get the total count if we don't have it
    if (!this.total || this.total === 0) {
        let countUrl = `/api/members?page_size=1`;
        // ... add filters
        const countData = await fetch(countUrl).then(r => r.json());
        this.total = countData.total || 0;
    }
    
    // Then fetch all members
    let url = `/api/members?page_size=${this.total}`;
    // ...
}
```

### Fix 3: Increase API Page Size Limit (CRITICAL)
Changed the API endpoint to allow larger page sizes:

**File**: `src/api/routes/members.py`

```python
# Before:
page_size: int = Query(50, ge=1, le=500)

# After:
page_size: int = Query(50, ge=1, le=50000)
```

## Flow After Fix

### Name Sort (Server-Side)
1. User clicks "Name" header
2. `sortBy('name')` → sets sortField = 'name'
3. `loadMembers()` → sortField === 'name' → loads 50 members paginated
4. Server returns sorted page

### All Other Sorts (Full Dataset)
1. User clicks any other header (Party, State, Chamber, etc.)
2. First time: Confirmation dialog
3. `sortBy(field)` → sets sortField = 'party' (or other)
4. `loadMembers()` → sortField !== 'name' → **SKIP page load**
5. **Go directly to** `applySorting()`
6. `applySorting()` fetches ALL members
7. Sorts complete dataset
8. Displays results

## Code Changes

**File**: `src/api/routes/dashboard_v2.py`
**Lines**: ~560-595

### Before
```javascript
async loadMembers() {
    if (this.loading) return;
    
    this.loading = true;
    // ... fetch 50 members
    
    // Then call applySorting
    if (this.sortField !== 'name') {
        await this.applySorting();  // Too late, 50 already loaded
    }
}
```

### After
```javascript
async loadMembers() {
    if (this.loading) return;
    
    // EARLY RETURN for non-name sorts
    if (this.sortField !== 'name') {
        await this.applySorting();  // Fetch ALL members
        return;                      // Skip page load
    }
    
    // Only reached for name sort
    this.loading = true;
    // ... fetch 50 members
}
```

## Testing

### Test Case 1: Sort by Party
1. ✅ Go to Members page (default: 50 members, sorted by Name)
2. ✅ Click "Party" header
3. ✅ Confirmation dialog appears
4. ✅ Click "Proceed"
5. ✅ Progress bar shows
6. ✅ **ALL members** (not just 50) are fetched and sorted by party
7. ✅ View shows sorted results with pagination

### Test Case 2: Sort by Name (should still work normally)
1. ✅ Click "Name" header while on Party sort
2. ✅ NO confirmation (name is server-side)
3. ✅ Page loads normally with 50 members
4. ✅ Pagination works

### Test Case 3: Filter + Sort
1. ✅ Set filter: Chamber = "House"
2. ✅ Click "State" header
3. ✅ ALL House members fetched
4. ✅ Sorted by state correctly
5. ✅ Pagination shows filtered + sorted results

## Verification Checklist
- ✅ Non-name sorts fetch complete dataset (not 50)
- ✅ Name sort still loads pages normally
- ✅ Initial page load shows 50 members (default: name sort)
- ✅ Confirmation dialog shows correct total count
- ✅ Progress bar appears during full dataset fetch
- ✅ Cancel button works
- ✅ Filters combine with sorting correctly
- ✅ Pagination displays sorted results properly

## Summary
The fix ensures that when sorting by ANY column except Name, the system **skips the paginated page load** and goes **directly to fetching the complete dataset**, ensuring accurate sorting across ALL members.

