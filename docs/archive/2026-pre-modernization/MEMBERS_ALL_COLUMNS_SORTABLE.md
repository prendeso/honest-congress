# Members Table - All Columns Sortable & Filter Implementation

## Overview
The Congressional Members table now supports **sorting on ALL 8 column headers** and comprehensive filtering aligned with those columns.

---

## Sortable Columns (8 of 8)

### ✅ 1. Name
- **Sort Type**: Alphabetical (server-side)
- **Behavior**: Sorts by full name (first + last)
- **Performance**: Fast, handled by backend

### ✅ 2. Party
- **Sort Type**: Alphabetical (client-side, full dataset)
- **Values**: D (Democrat), R (Republican), I (Independent)
- **Behavior**: Fetches all members, then sorts (shows confirmation first time)

### ✅ 3. State  
- **Sort Type**: Alphabetical (client-side, full dataset)
- **Values**: State codes (CA, NY, TX, etc.)
- **Behavior**: Fetches all members, then sorts (shows confirmation first time)

### ✅ 4. Chamber
- **Sort Type**: Alphabetical (client-side, full dataset)
- **Values**: House, Senate
- **Behavior**: Fetches all members, then sorts (shows confirmation first time)

### ✅ 5. District
- **Sort Type**: Numeric (client-side, full dataset)
- **Values**: District numbers, -1 for N/A (senators)
- **Behavior**: Fetches all members, then sorts (shows confirmation first time)

### ✅ 6. Disclosures
- **Sort Type**: Numeric (client-side, full dataset)
- **Values**: Count of disclosure filings
- **Behavior**: Shows confirmation dialog, fetches all members, then sorts
- **Note**: May take several seconds for large datasets

### ✅ 7. Anomalies
- **Sort Type**: Numeric (client-side, full dataset)
- **Values**: Count of detected anomalies
- **Behavior**: Shows confirmation dialog, fetches all members, then sorts
- **Note**: May take several seconds for large datasets

### ✅ 8. Status
- **Sort Type**: Boolean (client-side, full dataset)
- **Values**: In Office, Retired
- **Behavior**: Fetches all members, then sorts (shows confirmation first time)
- **Default**: In Office members first when descending

---

## Filters (7 filters total)

### 1. **Search by Name**
- **Type**: Text input
- **Behavior**: Real-time search with 300ms debounce
- **Searches**: First name and last name
- **Server-side**: Yes

### 2. **Party**
- **Type**: Dropdown
- **Options**: All Parties, Democrat (D), Republican (R), Independent (I)
- **Server-side**: Yes

### 3. **State** ⭐ NEW
- **Type**: Text input (2 character max)
- **Behavior**: Real-time search with 300ms debounce
- **Auto-converts**: To uppercase
- **Examples**: CA, NY, TX, FL, etc.
- **Server-side**: Yes

### 4. **Chamber**
- **Type**: Dropdown
- **Options**: All Chambers, House, Senate
- **Server-side**: Yes

### 5. **Status**
- **Type**: Dropdown
- **Options**: All Members, In Office, Retired
- **Server-side**: Yes

### 6. **Min Disclosures**
- **Type**: Dropdown
- **Options**: All, ≥1, ≥5, ≥10, ≥20
- **Client-side**: Yes (applied before sorting)

### 7. **Min Anomalies**
- **Type**: Dropdown
- **Options**: All, ≥1, ≥5, ≥10, ≥20
- **Client-side**: Yes (applied before sorting)

### 8. **Reset Filters Button**
- Clears all 7 filters
- Resets sort to Name (ascending)
- Resets to page 1
- Reloads data

---

## Technical Implementation

### Sort Performance Strategy

**Server-Side Sort:**
- **Name only** - Fast, handled by backend API

**Full Dataset Sorts (All Other Columns):**
- Party, State, Chamber, District, Disclosures, Anomalies, Status
- Requires API call to fetch all members
- Shows confirmation dialog first time
- Shows progress bar with cancel button
- Can process thousands of members
- Typical: 2-5 seconds

**Why Full Dataset?**
- Ensures accurate sorting across ALL members, not just the 50 on current page
- Users see truly sorted results (e.g., all members sorted by party, not just current page)
- Progress bar provides feedback during operation
- Cancel button allows user to abort if needed

### Sort Logic by Column

```javascript
// Text fields (party, state, chamber)
members.sort((a, b) => 
    (a.field || '').localeCompare(b.field || '') * sortMultiplier
);

// Numeric fields (district, disclosures, anomalies)
members.sort((a, b) => 
    ((a.field || 0) - (b.field || 0)) * sortMultiplier
);

// Boolean fields (status)
members.sort((a, b) => {
    const statusA = a.in_office ? 1 : 0;
    const statusB = b.in_office ? 1 : 0;
    return (statusB - statusA) * sortMultiplier;
});

// Name (composite)
members.sort((a, b) => {
    const nameA = `${a.first_name} ${a.last_name}`.trim();
    const nameB = `${b.first_name} ${b.last_name}`.trim();
    return nameA.localeCompare(nameB) * sortMultiplier;
});
```

### Filter Flow

1. **Server-side filters** (Name, Party, State, Chamber, Status)
   - Applied in API call
   - Reduces dataset before pagination
   - Fast and efficient

2. **Client-side filters** (Min Disclosures, Min Anomalies)
   - Applied after fetching data
   - Works on current page or full dataset
   - Combined with AND logic

3. **Combined filters**
   - All filters work together
   - Applied in sequence: server → client → sort
   - Maintains state during pagination

---

## User Experience

### Visual Indicators
- **Cursor**: Changes to pointer on sortable columns
- **Hover**: Light gray background on header hover
- **Active Sort**: Arrow indicator (↑ ↓) shows direction
- **Loading**: Spinner and progress bar for full dataset sorts

### Interaction Flow

**Server-Side Sort (Name Only):**
1. Click "Name" column header
2. ✨ Instant sort
3. Click again to reverse

**Full Dataset Sort (All Other Columns) - First Time:**
1. Click any column header (Party, State, Chamber, District, Disclosures, Anomalies, Status)
2. ⚠️ Warning dialog: "You're about to fetch and process X members for sorting. This may take several seconds."
3. Click "Proceed" or "Cancel"
4. Progress bar with member count
5. ✨ Results displayed sorted across ALL members
6. Subsequent clicks on same/other columns skip warning (unless page refresh)

**Full Dataset Sort - Subsequent Clicks:**
1. Click column header
2. Progress bar shows
3. ✨ Results displayed
4. No confirmation needed (user already confirmed)

**Filtering:**
1. Type or select filter values
2. Data updates after 300ms (debounce)
3. All filters combine (AND logic)
4. Use "Reset Filters" to clear all

---

## State Management

### JavaScript State Variables
```javascript
{
    search: '',               // Name search text
    partyFilter: '',          // D, R, I, or empty
    stateFilter: '',          // 2-char state code or empty
    chamberFilter: '',        // house, senate, or empty
    statusFilter: '',         // true, false, or empty
    disclosureMinFilter: '', // 1, 5, 10, 20, or empty
    anomalyMinFilter: '',    // 1, 5, 10, 20, or empty
    
    sortField: 'name',       // Current sort column
    sortOrder: 'asc',        // asc or desc
    
    page: 1,                 // Current page number
    pageSize: 50,            // Members per page
    
    loading: false,          // Loading indicator
    showConfirmModal: false, // Confirmation dialog
    firstSort: true,         // First time sorting flag
}
```

---

## Examples

### Example 1: Find California Democrats with 10+ Disclosures
1. Party: Select "Democrat"
2. State: Type "CA"
3. Min Disclosures: Select "≥ 10"
4. Click "Disclosures" header to see highest first

### Example 2: Sort House Members by State
1. Chamber: Select "House"
2. Click "State" header
3. ⚠️ Confirm dialog appears (first time)
4. Click "Proceed"
5. Wait for progress
6. ✨ See all House members sorted alphabetically by state

### Example 3: Find Most Active Anomaly Members
1. Min Anomalies: Select "≥ 1"
2. Click "Anomalies" header
3. ⚠️ Confirm dialog appears
4. Click "Proceed"
5. Wait for progress
6. ✨ See members with most anomalies first

---

## File Modified
- `src/api/routes/dashboard_v2.py` (1625 lines)

## Testing Checklist
- ✅ All 8 columns show sort cursor and hover
- ✅ All 8 columns sort correctly (asc/desc toggle)
- ✅ Name sorts instantly (server-side)
- ✅ All other columns (Party, State, Chamber, District, Disclosures, Anomalies, Status) show confirmation first time
- ✅ All non-name sorts fetch complete dataset (not just current page)
- ✅ Progress bar shows during full dataset sort
- ✅ Cancel button stops sort operation
- ✅ State filter accepts 2-char codes (auto-uppercase)
- ✅ All filters combine correctly (AND logic)
- ✅ Reset button clears all filters and sorting
- ✅ Pagination maintains sort/filter state
- ✅ Arrow indicators show active sort correctly

