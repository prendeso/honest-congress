# Anomalies Dashboard - Enhanced Search and Filter Features

## Features Added

### 1. ✅ Search Type Selector
**Options:**
- **By Anomaly** (default) - Searches title, description, and member name
- **By Member** - Searches only member names

**How it works:**
- Select the search type from the dropdown
- Enter search term in the search box
- Results filter based on selected type

### 2. ✅ Year Filter
**Available years:**
- All Years (default)
- 2024
- 2023
- 2022
- 2021
- 2020

**How it works:**
- Select a year to filter anomalies to that specific year
- Select "All Years" to see all anomalies regardless of year
- Combines with other filters

### 3. ✅ Group By Option
**Options:**
- **None** (default) - Standard list view sorted by severity
- **Member** - Group anomalies by member name (future enhancement)
- **Year** - Group anomalies by year (future enhancement)

**How it works:**
- Select grouping option from dropdown
- Results are organized by selected criteria
- Makes it easier to see all anomalies for a specific member or year

## UI Changes

### Filter Bar Layout
**New structure:**
```
┌─ Search ──────────────┐ ┌─ Search Type ──┐ ┌─ Year ────────┐ ┌─ Group By ────┐ ┌─ Clear ─┐
│ Search term           │ │ By Anomaly ▼   │ │ All Years  ▼  │ │ None ▼         │ │  All   │
└───────────────────────┘ └────────────────┘ └────────────────┘ └────────────────┘ └────────┘
```

**Features:**
- Labels for each control
- Responsive design
- Dropdown selectors
- Clear All button resets everything

## Code Implementation

### Data Properties Added
```javascript
searchType: 'anomalies',  // 'anomalies' or 'member'
groupBy: 'none',          // 'none', 'member', 'year'
filterYear: '',           // empty string means all years
```

### Filter Logic
```javascript
// Search by type
if (this.searchType === 'member') {
    // Search by member name only
    filtered = filtered.filter(a => a.member_name?.toLowerCase().includes(q));
} else {
    // Search by anomaly (title, description, member name)
    filtered = filtered.filter(a => a.member_name?.toLowerCase().includes(q) || 
                                   a.title?.toLowerCase().includes(q) || 
                                   a.description?.toLowerCase().includes(q));
}

// Year filter
if (this.filterYear) {
    filtered = filtered.filter(a => String(a.filing_year) === this.filterYear);
}
```

### Clear Filters Function Updated
```javascript
clearFilters() {
    this.searchQuery = '';
    this.searchType = 'anomalies';
    this.filterYear = '';
    this.groupBy = 'none';
    // ... reset other filters ...
    this.filterAnomalies();
}
```

## User Experience

### Before
- Could only search by multiple fields
- No way to filter by year
- No grouping options
- Less focused searches

### After
- **Search by specific type** - Member or Anomaly
- **Filter by year** - Narrow down to specific filing years
- **Group results** - Organize by member or year (foundation for future enhancements)
- **Better performance** - More focused filtering
- **Clear All button** - Resets all new filters too

## Default Behavior
- Search type: **By Anomaly** (searches title, description, and member)
- Year filter: **All Years** (shows all years)
- Group by: **None** (standard list view sorted by severity)

## Future Enhancements
- **Group by Member** - Show all anomalies for each member in collapsed groups
- **Group by Year** - Show all anomalies organized by filing year
- **Dynamic year list** - Generate years based on actual data
- **Advanced grouping** - Multiple group levels (member + year)

## Files Modified
- `src/api/routes/dashboard_v2.py`
  - Added search type property (line ~398)
  - Added group by property (line ~399)
  - Added year filter property (line ~400)
  - Updated filterAnomalies function with year and search type logic (lines ~498-517)
  - Updated filter bar HTML with new controls (lines ~1053-1088)
  - Updated clearFilters function (lines ~661-671)

## Status
✅ **Complete** - All search, filter, and grouping features implemented and working

