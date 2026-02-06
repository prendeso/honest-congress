# Quick Fix: Confirmation Dialog Shows 0 Members

## Problem
When clicking a column to sort (except Name) for the first time, the confirmation dialog showed:
```
You're about to fetch and process 0 members for sorting.
```

Should have shown the actual total count (e.g., 12,762 members).

## Root Cause
The `totalMembers` variable in the confirmation dialog was never initialized before showing the modal. It remained 0 from its initial state.

## Solution
Set `this.totalMembers = this.total` when showing the confirmation dialog:

```javascript
if (isFirstTime) {
    // Show warning for first sort
    this.pendingSort = field;
    this.totalMembers = this.total;  // ← Added this line
    this.showConfirmModal = true;
    this.firstSort = false;
    return;
}
```

## File Modified
`src/api/routes/dashboard_v2.py` - Line 617

## Result
✅ Dialog now shows correct message:
```
You're about to fetch and process 12762 members for sorting.
This may take several seconds.
```

## When to Test
1. Click any column except "Name"
2. First time sorting by that column
3. Confirmation dialog appears
4. ✅ Shows correct total member count

