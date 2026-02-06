# Member Anomalies Modal - Quick Fix Guide

## Current Status
The member anomalies modal feature is **NOT YET WORKING** due to file edit complications.

## What Needs to Be Done

To make member rows clickable and show their anomalies, you need to make these changes to `src/api/routes/dashboard_v2.py`:

### 1. Add Modal State to membersPage() JavaScript Function

Find the `membersPage()` function (around line 499) and add these state variables:

```javascript
function membersPage() {{
    return {{
        members: [],
        total: 0,
        page: 1,
        pageSize: 50,
        // ... existing variables ...
        loading: false,
        
        // ADD THESE NEW VARIABLES:
        showAnomaliesModal: false,
        anomaliesLoading: false,
        selectedMember: null,
        memberAnomalies: [],
```

### 2. Add Modal Functions

Add these three functions inside the `membersPage()` return object:

```javascript
formatDate(value) {{
    if (!value) return '-';
    const d = new Date(value);
    return isNaN(d.getTime()) ? '-' : d.toLocaleDateString();
}},

async openMemberAnomalies(member) {{
    this.selectedMember = member;
    this.memberAnomalies = [];
    this.showAnomaliesModal = true;
    this.anomaliesLoading = true;

    try {{
        const url = `/api/anomalies?member_id=${{member.id}}&page_size=200`;
        const data = await fetch(url).then(r => r.json());
        this.memberAnomalies = data.anomalies || [];
    }} catch (e) {{
        console.error('[Members] Failed to load anomalies:', e);
        alert('Failed to load anomalies for this member.');
    }} finally {{
        this.anomaliesLoading = false;
    }}
}},

closeMemberAnomalies() {{
    this.showAnomaliesModal = false;
    this.selectedMember = null;
    this.memberAnomalies = [];
}},
```

### 3. Make Table Rows Clickable

Find this line (around line 433):
```html
<tr class="hover:bg-gray-50">
```

Replace with:
```html
<tr class="hover:bg-gray-50 cursor-pointer" 
    @click="openMemberAnomalies(member)">
```

### 4. Add Modal HTML

After `{FOOTER_HTML}` (around line 495), add this modal HTML:

```html
{FOOTER_HTML}

<!-- Member Anomalies Modal -->
<div x-show="showAnomaliesModal" class="fixed inset-0 z-50 overflow-y-auto" x-cloak>
    <div class="flex items-center justify-center min-h-screen px-4">
        <div class="fixed inset-0 bg-black/50" @click="closeMemberAnomalies()"></div>
        <div class="relative bg-white rounded-xl shadow-2xl max-w-3xl w-full p-6">
            <div class="flex items-start justify-between">
                <div>
                    <h3 class="text-xl font-bold text-gray-800" x-text="selectedMember ? (selectedMember.first_name + ' ' + selectedMember.last_name) : 'Member'"></h3>
                    <p class="text-sm text-gray-500 mt-1">Anomalies for this member</p>
                </div>
                <button @click="closeMemberAnomalies()" class="text-gray-400 hover:text-gray-600">✕</button>
            </div>

            <template x-if="anomaliesLoading">
                <div class="text-center py-10">
                    <div class="animate-spin inline-block w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full mb-3"></div>
                    <p class="text-gray-600">Loading anomalies...</p>
                </div>
            </template>

            <template x-if="!anomaliesLoading">
                <div class="mt-4">
                    <div class="flex items-center justify-between mb-3">
                        <span class="text-sm text-gray-600">Total: <span class="font-semibold" x-text="memberAnomalies.length"></span></span>
                        <template x-if="selectedMember">
                            <a :href="`/anomalies?member_id=${{selectedMember.id}}`" class="text-sm text-blue-600 hover:text-blue-800">View in anomalies page</a>
                        </template>
                    </div>
                    <template x-if="memberAnomalies.length === 0">
                        <div class="text-center py-8 text-gray-500">No anomalies found for this member.</div>
                    </template>
                    <div class="space-y-3 max-h-[400px] overflow-y-auto">
                        <template x-for="anomaly in memberAnomalies" :key="anomaly.id">
                            <div class="border rounded-lg p-3">
                                <div class="flex items-center justify-between">
                                    <div class="font-semibold text-gray-800" x-text="anomaly.title || anomaly.anomaly_type"></div>
                                    <span class="text-xs px-2 py-0.5 rounded" :class="{{
                                        'bg-red-100 text-red-800': anomaly.severity === 'high',
                                        'bg-yellow-100 text-yellow-800': anomaly.severity === 'medium',
                                        'bg-green-100 text-green-800': anomaly.severity === 'low'
                                    }}" x-text="anomaly.severity"></span>
                                </div>
                                <div class="text-sm text-gray-600 mt-1" x-text="anomaly.description || 'No description available.'"></div>
                                <div class="text-xs text-gray-500 mt-2">
                                    <span x-text="anomaly.anomaly_type"></span>
                                    <span class="mx-1">•</span>
                                    <span x-text="anomaly.filing_year || '-'"></span>
                                    <span class="mx-1">•</span>
                                    <span x-text="formatDate(anomaly.detected_at)"></span>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
            </template>
        </div>
    </div>
</div>
</div>
```

**IMPORTANT:** Make sure the last `</div>` closes the `x-data="membersPage()"` div properly.

### 5. Restart Server

```powershell
python start_server.py
```

### 6. Test

1. Go to http://localhost:8000/members
2. Click any member row
3. Modal should appear with anomalies
4. Click X or outside to close

## Files to Edit

- `src/api/routes/dashboard_v2.py` - Only this file needs changes

## Why This Is Needed

Currently, there's no way for users to quickly view a member's anomalies from the members list. This feature adds:
- Quick anomaly preview
- One-click access
- Better UX
- Less navigation required

## Server Restart

**After making changes:**
```powershell
# Stop existing server
Get-Process python | Where-Object {$_.Id -ne $PID} | Stop-Process -Force

# Start fresh
python start_server.py
```


