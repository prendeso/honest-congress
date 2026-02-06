# Column Header Sorting Implementation

## Summary
Updated all data table pages to support sorting by clicking column headers instead of using separate sort buttons.

## Changes Made

### 1. Members Page (`/members`)
- **Removed**: Separate sort control buttons at the top of the table
- **Added**: Clickable column headers for:
  - **Name** - Sort by member name (primary sort field)
  - **Disclosures** - Sort by disclosure count
  - **Anomalies** - Sort by anomaly count
- **Features**:
  - Click column header to sort ascending/descending
  - Click again to toggle sort order
  - Up/down arrows indicate sort direction
  - Hover effect shows the column is clickable

### 2. Disclosures Page (`/disclosures`)
- **Added**: Sortable columns:
  - **Year** - Sort by filing year
  - **Filing Date** - Sort by filing date (default sort, descending)
- **Default**: Sorted by Filing Date (descending) on page load

### 3. Stock Trades Page (`/trades`)
- **Added**: Sortable columns:
  - **Year** - Sort by filing year
  - **Filing Date** - Sort by filing date (default sort, descending)
- **Default**: Sorted by Filing Date (descending) on page load

### 4. Parsed Documents Page (`/parsed`)
- **Added**: Sortable columns:
  - **Year** - Sort by filing year
  - **Assets** - Sort by asset count
  - **Transactions** - Sort by transaction count
  - **Liabilities** - Sort by liability count
  - **Filing Date** - Sort by filing date (default sort, descending)
- **Default**: Sorted by Filing Date (descending) on page load

## Technical Details

### HTML Changes
- Column headers with `cursor-pointer` class to indicate interactivity
- Hover effect with `hover:bg-gray-100` for visual feedback
- Alpine.js click handler: `@click="sortBy('fieldname')"`
- Visual indicator: `<span x-show="sortField === 'fieldname'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>`

### JavaScript Changes
- Each page now has:
  - `sortField`: Current sort column
  - `sortOrder`: 'asc' or 'desc'
  - `sortBy(field)`: Handler to toggle sort
  - `applySorting()`: Client-side sorting logic
- Sorting is applied after each data load (pagination)
- Sort state persists during pagination

## User Experience

### Members Page
1. Click "Name" header to sort by name
2. Click "Disclosures" header to sort by disclosure count
3. Click "Anomalies" header to sort by anomaly count
4. Click same header again to reverse sort order
5. Arrow indicator shows current sort direction

### Other Pages
1. Click any sortable column header to sort
2. Click again to reverse sort order
3. Up/down arrows show current sort direction
4. Default sort is by most recent filing date

## File Modified
- `src/api/routes/dashboard_v2.py` (1466 lines total)

## Testing
All pages are tested with:
- Basic sorting functionality
- Sort direction toggle
- Pagination with maintained sort
- Visual feedback (cursor pointer, hover, arrows)

