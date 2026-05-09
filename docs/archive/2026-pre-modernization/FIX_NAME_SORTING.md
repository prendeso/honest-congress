# Fix: Name Sorting Not Working (Updated)

## Problems Fixed

After implementing the full dataset sorting for non-name fields, three issues needed to be resolved:

1. **Sorting parameters weren't being passed to the API**
2. **When switching from other column sorts back to Name, page wasn't resetting**
3. **Confirmation dialog showed "0 members" instead of actual total count**

## Root Causes

### Issue 1: Missing API Parameters
The `loadMembers()` function was correctly loading paginated data but wasn't passing the `sort_by` and `sort_order` parameters to the API.

### Issue 2: Page Not Resetting
When switching from a full dataset sort back to Name, we could be on page 255 of 12,762 members, trying to load an empty page.

### Issue 3: Incorrect Total Count in Dialog
The confirmation dialog variable `totalMembers` was never set before showing the modal, so it displayed 0 instead of the actual count.

## Solutions Applied

### Fix 1: Add Sorting Parameters
```javascript
// Add sorting parameters for server-side sort (name only)
if (this.sortField === 'name') {
    url += `&sort_by=name&sort_order=${this.sortOrder}`;
}
```

### Fix 2: Reset to Page 1 When Switching to Name
```javascript
// If switching from non-name to name sort, reset to page 1
const switchingToName = (field === 'name' && this.sortField !== 'name');
if (switchingToName) {
    this.page = 1;
}
```

### Fix 3: Set Total Count in Confirmation Dialog
```javascript
if (isFirstTime) {
    // Show warning for first sort
    this.pendingSort = field;
    this.totalMembers = this.total;  // Show actual total count in dialog
    this.showConfirmModal = true;
    this.firstSort = false;
    return;
}
```

### Fix 4: Enhanced Logging
```javascript
console.log('[Members] Loaded:', this.members.length, 'total:', this.total);
console.log('[Members] First 3 members:', this.members.slice(0, 3).map(m => m.first_name + ' ' + m.last_name));
```

## Implementation Details

### File Modified
`src/api/routes/dashboard_v2.py` (Line ~582-585)

### Before
```javascript
let url = `/api/members?page=${this.page}&page_size=${this.pageSize}`;
if (this.search) url += `&search=${encodeURIComponent(this.search)}`;
if (this.partyFilter) url += `&party=${this.partyFilter}`;
// ... other filters
// NO SORTING PARAMETERS

const data = await fetch(url).then(r => r.json());
```

### After
```javascript
let url = `/api/members?page=${this.page}&page_size=${this.pageSize}`;
if (this.search) url += `&search=${encodeURIComponent(this.search)}`;
if (this.partyFilter) url += `&party=${this.partyFilter}`;
// ... other filters

// Add sorting parameters for server-side sort (name only)
if (this.sortField === 'name') {
    url += `&sort_by=name&sort_order=${this.sortOrder}`;
}

const data = await fetch(url).then(r => r.json());
```

## Flow After Fix

### Sorting by Name
1. User clicks "Name" column header
2. `sortBy('name')` → sets `sortField = 'name'`, toggles `sortOrder`
3. `loadMembers()` → `sortField === 'name'` → normal page load
4. API URL includes: `&sort_by=name&sort_order=asc` (or desc)
5. Server returns 50 members sorted by name
6. ✅ Sorted results displayed

### Sorting by Other Columns
1. User clicks any other column (Party, State, etc.)
2. First time: Confirmation dialog
3. `sortBy(field)` → sets `sortField = 'party'` (or other)
4. `loadMembers()` → `sortField !== 'name'` → calls `applySorting()`
5. `applySorting()` fetches ALL members
6. Client-side sorting applied
7. ✅ Sorted results displayed

## API Endpoint Support

The `/api/members` endpoint already supports these parameters:
- `sort_by`: name, party, state, chamber, district, status, disclosures, anomalies
- `sort_order`: asc, desc

For name sorting, we use:
- `sort_by=name`
- `sort_order=asc` or `desc`

The API handles name sorting specially by sorting on both first_name and last_name:
```python
if sort_by == "name":
    query = query.order_by(asc(Member.first_name), asc(Member.last_name))
```

## Testing

### Test Case 1: Initial Load
1. ✅ Page loads with 50 members sorted by name (default)
2. ✅ "Name" column shows ↑ arrow

### Test Case 2: Sort by Name Ascending
1. Click "Name" column
2. ✅ Members sorted A-Z
3. ✅ Arrow shows ↑

### Test Case 3: Sort by Name Descending
1. Click "Name" column again
2. ✅ Members sorted Z-A
3. ✅ Arrow shows ↓

### Test Case 4: Sort by Name with Filters
1. Set filter: Party = "Democrat"
2. Click "Name" column
3. ✅ Democrats sorted by name A-Z
4. ✅ Pagination works correctly

### Test Case 5: Switch from Other Sort to Name
1. Sort by "Party" (full dataset)
2. Click "Name" column
3. ✅ No confirmation needed
4. ✅ Back to paginated view (50 members)
5. ✅ Sorted by name

## Summary

**Issue**: Name sorting wasn't working because the API parameters weren't being passed.

**Fix**: Added `sort_by=name&sort_order=${this.sortOrder}` to the API URL when `sortField === 'name'`.

**Result**: Name sorting now works correctly with server-side sorting, maintaining fast paginated performance.

## Files Modified
- `src/api/routes/dashboard_v2.py` (1655 lines total)
  - Line ~582-586: Added sorting parameters for name sort
  - Line ~617: Set `totalMembers = this.total` in confirmation dialog
  - Line ~622-626: Reset page to 1 when switching to name sort
  - Line ~593-594: Enhanced logging to show first 3 members loaded

