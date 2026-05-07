# Anomalies Dashboard - ReferenceError Fixed

## Problem
When opening the `/anomalies` page, users were getting:
```
Uncaught ReferenceError: dashboard is not defined
[Alpine] dashboard() @ VM2886:3
```

And multiple follow-up errors for undefined variables like `stats`, `init`, `searchQuery`, etc.

## Root Cause
Alpine.js was loading and initializing BEFORE the `window.dashboard` function was fully defined. The order of script execution was wrong.

## Solution
The correct order is:
1. Define `window.dashboard` function FIRST (synchronous inline script)
2. Load Alpine.js SECOND with `defer` attribute (waits for DOM + all scripts)

### Correct Script Order:
```html
<script src="https://cdn.tailwindcss.com"></script>
<script>
    // Define dashboard function FIRST, BEFORE Alpine loads
    window.dashboard = function() {
        return {
            stats: { ... },
            // ... all properties and methods
        };
    };
</script>
<script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
```

## Why This Works
- The inline `<script>` with `window.dashboard` runs **synchronously** when the parser reaches it
- Alpine.js with `defer` attribute waits until:
  1. The entire HTML document is parsed
  2. All inline scripts have executed
- When Alpine finally runs, `window.dashboard` is already defined and ready

## Files Modified
- `src/api/routes/dashboard_v2.py`
  - Line ~314: Tailwind CSS
  - Line ~315-886: Inline script defining `window.dashboard` function
  - Line ~887: Alpine.js with `defer` attribute

## Result
✅ Anomalies page now loads correctly
✅ Dashboard function initializes properly
✅ All Alpine.js data bindings work
✅ No more ReferenceError messages

