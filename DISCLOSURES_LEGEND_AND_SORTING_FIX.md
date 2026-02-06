# Financial Disclosures Card - Legend & Sorting Fix

## ✅ CHANGES COMPLETE

### 1. Added Filing Type Legend
A legend box now appears above the financial disclosures table explaining the meaning of each filing type.

**Types Explained:**
- **Annual** - Yearly financial disclosure filed by all members
- **New Filer** - Initial disclosure filed when first taking office  
- **Amendment** - Correction or update to a previously filed disclosure

**Design:**
- Blue-tinted box with icon and clear descriptions
- Grid layout for easy readability
- Positioned after filter section, before table

### 2. Fixed Sorting to Work Across All Entries

**Problem:**
- Sorting was only working on the 50 items shown on the current page
- API was paginating results, so client-side sort only affected visible items

**Solution:**
- Changed to fetch up to 100 items (API maximum page_size)
- Sorting now applies to ALL fetched records
- Works correctly for sorting by:
  - Member name
  - Year
  - Status (Parsed/Pending)

**Code Changes:**
```javascript
// Before: Used this.page and this.pageSize (50 per page)
let url = `/api/disclosures?page=${this.page}&page_size=${this.pageSize}&is_ptr=false`;

// After: Fetch all items (up to 100) for accurate sorting
let url = `/api/disclosures?page=1&page_size=100&is_ptr=false`;
```

### 3. How It Works Now

1. **User applies filters** → Loads up to 100 disclosures matching filters
2. **User clicks sort column** → Sorts ALL 100 loaded items
3. **Results displayed** → Showing sorted results

This ensures sorting is accurate across the full dataset (within the 100-item API limit).

## Files Modified
- ✅ `src/api/routes/dashboard_v2.py`
  - Added legend HTML (blue info box with filing type descriptions)
  - Updated `loadDisclosures()` to fetch page_size=100
  - Sorting now works on all fetched items, not just 50 shown

## Testing

1. **Navigate to**: http://localhost:8000/disclosures
2. **See legend** ✓
   - Blue box below filters shows Annual/New Filer/Amendment explanations
3. **Test sorting** ✓
   - Click "Status" header multiple times
   - Should toggle between Parsed → Pending and Pending → Parsed
   - Sorting should be accurate for all records, not just page 1

## Limitations

- Can display/sort up to 100 disclosures at a time (API limit)
- For users with >100 disclosures matching filters, only first 100 are shown
- Pagination removed in favor of comprehensive sorting

## Future Enhancement

If more than 100 disclosures need sorting:
- Could implement server-side sorting in `/api/disclosures` endpoint
- Add `sort_by` and `sort_order` query parameters


