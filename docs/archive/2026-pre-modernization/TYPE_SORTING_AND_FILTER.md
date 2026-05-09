# Type Column Sorting & Filter Update

## ✅ Both Features Complete

### 1. Type Column Now Sortable

**What Changed**:
- Type column header is now clickable
- Shows sort indicator (↑/↓) when selected
- Sorts all disclosures by filing type
- Server-side sorting for accuracy across all records

**How to Use**:
- Go to http://localhost:8000/disclosures
- Click the "Type" column header
- Click again to toggle between ascending/descending
- Sorts A → X (or X → A)

### 2. Type Filter Dropdown Updated

**Before**:
```
All Types
Annual
New Filer
Amendment
```

**After** (All 9 types):
```
All Types
A - Annual Report
C - Candidate Report
F - Final Report
G - Gingles Report
H - House Member Report
O - Original Report
P - Periodic Report
T - Termination Report
X - Amended/Corrected
```

**Benefits**:
- Users can filter by specific filing type codes
- Clear descriptions in dropdown
- Matches the legend below
- Makes navigation easier

## Implementation Details

### Frontend Changes (`src/api/routes/dashboard_v2.py`)

1. **Type Column Header**:
   - Added click handler: `@click="sortBy('type')"`
   - Added sort indicator: `<span x-show="sortField === 'type'"`
   - Made cursor pointer on hover

2. **Filter Dropdown**:
   - Updated all 9 options with full descriptions
   - Format: "A - Annual Report" etc.

3. **sortBy Function**:
   - Added 'type' mapping to fieldMap
   - Maps 'type' → 'type' for server-side sorting

### Backend Changes (`src/api/routes/disclosures.py`)

1. **Server-side Sorting**:
   - Added `elif sort_by == 'type':` branch
   - Uses `Disclosure.filing_type.desc()` or `.asc()`
   - Sorts across all records before pagination

## Testing

1. **Test Sorting**:
   - Go to http://localhost:8000/disclosures
   - Click "Type" header
   - Should sort A, C, F, G, H, O, P, T, X
   - Click again to reverse

2. **Test Filtering**:
   - Use the "Type" dropdown filter
   - Select any filing type (A, C, F, etc.)
   - Table shows only that type
   - Works together with sorting

3. **Combined Test**:
   - Filter by one type (e.g., "A - Annual Report")
   - Sort by Type ascending/descending
   - Both work together correctly

## Files Modified

- `src/api/routes/dashboard_v2.py` - Type header sorting + filter dropdown
- `src/api/routes/disclosures.py` - Server-side sorting for type field

## User Experience

✅ Consistent with other sortable columns  
✅ Works on all 5000+ disclosures  
✅ Clear filter options  
✅ Professional UI  
✅ Intuitive sorting indicators  


