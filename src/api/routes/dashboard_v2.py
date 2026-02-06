"""Enhanced web dashboard for Honest Congress - Clean implementation with all pages."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from decimal import Decimal
from typing import List, Dict, Any

from src.db import get_db_session, Transaction, Disclosure, Anomaly, Member

router = APIRouter()


# ============================================================================
# SHARED COMPONENTS
# ============================================================================

HEADER_HTML = """
<header class="bg-gradient-to-r from-blue-900 via-blue-800 to-blue-900 text-white shadow-lg sticky top-0 z-40">
    <div class="container mx-auto px-4 py-4">
        <div class="flex items-center justify-between">
            <div>
                <h1 class="text-2xl font-bold">🏛️ Honest Congress</h1>
                <p class="text-blue-200 text-sm">Congressional Financial Disclosure Analyzer</p>
            </div>
            <nav class="flex gap-4">
                <a href="/" class="hover:text-blue-200 transition">Home</a>
                <a href="/members" class="hover:text-blue-200 transition">Members</a>
                <a href="/disclosures" class="hover:text-blue-200 transition">Disclosures</a>
                <a href="/trades" class="hover:text-blue-200 transition">Trades</a>
                <a href="/parsed" class="hover:text-blue-200 transition">Parsed</a>
                <a href="/anomalies" class="hover:text-blue-200 transition">Anomalies</a>
            </nav>
        </div>
    </div>
</header>
"""

FOOTER_HTML = """
<footer class="bg-gray-800 text-gray-400 py-8 mt-12">
    <div class="container mx-auto px-4 text-center">
        <p>Honest Congress - Congressional Financial Disclosure Analyzer</p>
        <p class="text-sm mt-2">Data: House Clerk, QuiverQuant API, congress-legislators</p>
    </div>
</footer>
"""

STYLES = """
<style>
    [x-cloak] { display: none !important; }
    .glass-card { background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); }
    .btn-primary { @apply bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition; }
    .btn-secondary { @apply bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300 transition; }
</style>
"""


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.get("/api/insights", tags=["Insights"])
async def get_insights(db: Session = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """Get interesting facts and insights from congressional data."""
    insights = []

    try:
        # Get highest anomaly count members
        top_anomalies_members = db.query(
            Member.first_name,
            Member.last_name,
            func.count(Anomaly.id).label('anomaly_count')
        ).join(
            Anomaly, Member.id == Anomaly.member_id
        ).group_by(
            Member.id, Member.first_name, Member.last_name
        ).order_by(
            func.count(Anomaly.id).desc()
        ).limit(1).first()

        if top_anomalies_members:
            member_name = f"{top_anomalies_members[0]} {top_anomalies_members[1]}"
            insights.append({
                'id': 1,
                'icon': '🚨',
                'title': 'Most Flagged Member',
                'description': 'Member with the highest number of detected anomalies',
                'value': f"{member_name} ({top_anomalies_members[2]} flags)"
            })

        # Get largest single trade
        largest_trade = db.query(
            Transaction.description,
            Transaction.amount_max,
            Member.first_name,
            Member.last_name
        ).join(
            Disclosure, Transaction.disclosure_id == Disclosure.id
        ).join(
            Member, Disclosure.member_id == Member.id
        ).order_by(
            Transaction.amount_max.desc()
        ).first()

        if largest_trade and largest_trade[1]:
            amount = int(largest_trade[1])
            insights.append({
                'id': 2,
                'icon': '📈',
                'title': 'Largest Single Trade',
                'description': f"Highest value stock trade on record",
                'value': f"${amount:,}"
            })

        # Get most active trader
        most_active = db.query(
            Member.first_name,
            Member.last_name,
            func.count(Transaction.id).label('trade_count')
        ).join(
            Disclosure, Member.id == Disclosure.member_id
        ).join(
            Transaction, Disclosure.id == Transaction.disclosure_id
        ).group_by(
            Member.id, Member.first_name, Member.last_name
        ).order_by(
            func.count(Transaction.id).desc()
        ).limit(1).first()

        if most_active:
            member_name = f"{most_active[0]} {most_active[1]}"
            insights.append({
                'id': 3,
                'icon': '📊',
                'title': 'Most Active Trader',
                'description': 'Member with highest frequency of stock trades',
                'value': f"{member_name} ({most_active[2]} trades)"
            })

        # Get disclosure count
        disclosure_count = db.query(func.count(Disclosure.id)).scalar()
        if disclosure_count:
            insights.append({
                'id': 4,
                'icon': '📄',
                'title': 'Total Disclosures Analyzed',
                'description': 'Financial and transaction disclosure reports processed',
                'value': f"{disclosure_count:,} filings"
            })

    except Exception as e:
        print(f"Error generating insights: {e}")

    return insights


# ============================================================================
# LANDING PAGE
# ============================================================================

@router.get("/", response_class=HTMLResponse)
async def landing_page():
    """Serve the landing page with key insights."""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Honest Congress - Home</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
        {STYLES}
    </head>
    <body class="bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 min-h-screen">
        <div x-data="landingPage()" x-init="init()" x-cloak>
            {HEADER_HTML}
            
            <!-- Hero Section -->
            <div class="container mx-auto px-4 py-12 text-white">
                <div class="text-center mb-12">
                    <h2 class="text-5xl font-bold mb-4">Track Congressional Financial Activity</h2>
                    <p class="text-xl text-blue-200">Analyzing disclosures, trades, and anomalies in Congress</p>
                </div>
                
                <!-- Stats Cards -->
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-12">
                    <a href="/members" class="glass-card rounded-xl shadow-lg p-6 text-gray-800 hover:scale-105 transition transform cursor-pointer">
                        <div class="text-gray-600 text-sm font-medium uppercase">Total Members</div>
                        <div class="text-4xl font-bold text-blue-900 mt-2" x-text="stats.totalMembers || '...'"></div>
                        <div class="text-sm text-gray-500 mt-1">House & Senate (Past & Present)</div>
                    </a>
                    <a href="/disclosures" class="glass-card rounded-xl shadow-lg p-6 text-gray-800 hover:scale-105 transition transform cursor-pointer">
                        <div class="text-gray-600 text-sm font-medium uppercase">Disclosures</div>
                        <div class="text-4xl font-bold text-green-600 mt-2" x-text="stats.totalDisclosures || '...'"></div>
                        <div class="text-sm text-gray-500 mt-1">Financial reports</div>
                    </a>
                    <a href="/trades" class="glass-card rounded-xl shadow-lg p-6 text-gray-800 hover:scale-105 transition transform cursor-pointer">
                        <div class="text-gray-600 text-sm font-medium uppercase">Stock Trades</div>
                        <div class="text-4xl font-bold text-purple-600 mt-2" x-text="stats.totalTrades || '...'"></div>
                        <div class="text-sm text-gray-500 mt-1">PTR filings</div>
                    </a>
                    <a href="/anomalies" class="glass-card rounded-xl shadow-lg p-6 text-gray-800 hover:scale-105 transition transform cursor-pointer">
                        <div class="text-gray-600 text-sm font-medium uppercase">Anomalies</div>
                        <div class="text-4xl font-bold text-red-600 mt-2" x-text="stats.totalAnomalies || '...'"></div>
                        <div class="text-sm text-gray-500 mt-1">Flags detected</div>
                    </a>
                </div>
                
                <!-- Insights Section -->
                <div class="mt-16">
                    <h3 class="text-3xl font-bold text-white mb-8 text-center">Key Insights</h3>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6" x-data="{{{{ insights: [] }}}}" x-init="loadInsights()">
                        <template x-for="insight in insights" :key="insight.id">
                            <div class="glass-card rounded-xl shadow-lg p-6 text-gray-800">
                                <div class="flex items-start gap-3">
                                    <div class="text-3xl" x-text="insight.icon"></div>
                                    <div class="flex-1">
                                        <h4 class="font-bold text-lg text-gray-900" x-text="insight.title"></h4>
                                        <p class="text-gray-600 text-sm mt-2" x-text="insight.description"></p>
                                        <div class="mt-3 text-2xl font-bold text-blue-600" x-text="insight.value"></div>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
            </div>
            
            {FOOTER_HTML}
        </div>
        
        <script>
            function landingPage() {{
                return {{
                    stats: {{}},
                    insights: [],
                    
                    async init() {{
                        await Promise.all([this.loadStats(), this.loadInsights()]);
                    }},
                    
                    async loadStats() {{
                        try {{
                            const [members, disclosures, trades, anomalies] = await Promise.all([
                                fetch('/api/members?page_size=1').then(r => r.json()),
                                fetch('/api/disclosures?page_size=1&is_ptr=false').then(r => r.json()),
                                fetch('/api/disclosures?page_size=1&is_ptr=true').then(r => r.json()),
                                fetch('/api/anomalies/summary').then(r => r.json())
                            ]);
                            
                            this.stats = {{
                                totalMembers: members.total || 0,
                                totalDisclosures: disclosures.total || 0,
                                totalTrades: trades.total || 0,
                                totalAnomalies: anomalies.total_anomalies || 0
                            }};
                        }} catch (e) {{
                            console.error('Error loading stats:', e);
                        }}
                    }},
                    
                    async loadInsights() {{
                        try {{
                            const response = await fetch('/api/insights');
                            if (response.ok) {{
                                this.insights = await response.json();
                            }}
                        }} catch (e) {{
                            console.error('Error loading insights:', e);
                            // Set default insights if API fails
                            this.insights = [
                                {{
                                    id: 1,
                                    icon: '📊',
                                    title: 'Most Active Traders',
                                    description: 'Members with the highest frequency of stock trades',
                                    value: 'View Analysis'
                                }},
                                {{
                                    id: 2,
                                    icon: '🚨',
                                    title: 'Anomalies Detected',
                                    description: 'Unusual trading patterns and timing anomalies',
                                    value: 'Learn More'
                                }}
                            ];
                        }}
                    }}
                }};
            }}
        </script>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ============================================================================
# MEMBERS PAGE
# ============================================================================

@router.get("/members", response_class=HTMLResponse)
async def members_page():
    """Serve the members browsing page with sorting and filtering."""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Congressional Members - Honest Congress</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
        {STYLES}
    </head>
    <body class="bg-gray-100 min-h-screen">
        <div x-data="membersPage()" x-init="init()" x-cloak>
            {HEADER_HTML}
            
            <div class="container mx-auto px-4 py-8">
                <div class="glass-card rounded-xl shadow-lg p-6">
                    <h2 class="text-3xl font-bold text-gray-800 mb-6">Congressional Members</h2>
                    
                    <!-- Filters and Search -->
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-6 gap-4 mb-6">
                        <input type="text" 
                               x-model="search" 
                               @input.debounce.300ms="loadMembers()"
                               placeholder="Search by name..." 
                               class="border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        
                        <select x-model="partyFilter" @change="loadMembers()" class="border rounded-lg px-4 py-2">
                            <option value="">All Parties</option>
                            <option value="D">Democrat</option>
                            <option value="R">Republican</option>
                            <option value="I">Independent</option>
                        </select>
                        
                        <input type="text" 
                               x-model="stateFilter" 
                               @input.debounce.300ms="loadMembers()"
                               placeholder="State (e.g., CA, NY)..." 
                               class="border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                               maxlength="2"
                               style="text-transform: uppercase;">
                        
                        <input type="text" 
                               x-model="districtFilter" 
                               @input.debounce.300ms="loadMembers()"
                               placeholder="District (1-53, or - for Senate)..." 
                               class="border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        
                        <select x-model="chamberFilter" @change="loadMembers()" class="border rounded-lg px-4 py-2">
                            <option value="">All Chambers</option>
                            <option value="house">House</option>
                            <option value="senate">Senate</option>
                        </select>
                        
                        <select x-model="statusFilter" @change="loadMembers()" class="border rounded-lg px-4 py-2">
                            <option value="">All Members</option>
                            <option value="true">In Office</option>
                            <option value="false">Retired</option>
                        </select>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                        <select x-model="disclosureMinFilter" @change="loadMembers()" class="border rounded-lg px-4 py-2">
                            <option value="">Min Disclosures</option>
                            <option value="1">≥ 1</option>
                            <option value="5">≥ 5</option>
                            <option value="10">≥ 10</option>
                            <option value="20">≥ 20</option>
                        </select>
                        
                        <select x-model="anomalyMinFilter" @change="loadMembers()" class="border rounded-lg px-4 py-2">
                            <option value="">Min Anomalies</option>
                            <option value="1">≥ 1</option>
                            <option value="5">≥ 5</option>
                            <option value="10">≥ 10</option>
                            <option value="20">≥ 20</option>
                        </select>
                        
                        <button @click="resetFilters()" class="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400">
                            Reset Filters
                        </button>
                    </div>
                    
                    <!-- Loading -->
                    <template x-if="loading">
                        <div class="text-center py-12">
                            <div class="animate-spin inline-block w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full mb-4"></div>
                            <p class="text-gray-600">Loading members...</p>
                        </div>
                    </template>
                    
                    <!-- Members Table -->
                    <template x-if="!loading">
                        <div class="overflow-x-auto">
                            <table class="w-full">
                                <thead class="bg-gray-50">
                                    <tr>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('name')">
                                            Name <span x-show="sortField === 'name'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('party')">
                                            Party <span x-show="sortField === 'party'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('state')">
                                            State <span x-show="sortField === 'state'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('chamber')">
                                            Chamber <span x-show="sortField === 'chamber'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('district')">
                                            District <span x-show="sortField === 'district'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('disclosures')">
                                            Disclosures <span x-show="sortField === 'disclosures'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('anomalies')">
                                            Anomalies <span x-show="sortField === 'anomalies'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('status')">
                                            Status <span x-show="sortField === 'status'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                    </tr>
                                </thead>
                                <tbody class="divide-y">
                                    <template x-for="member in members" :key="member.id">
                                        <tr class="hover:bg-gray-50 cursor-pointer" 
                                            @click="openMemberAnomalies(member)" 
                                            role="button" 
                                            tabindex="0" 
                                            @keydown.enter.prevent="openMemberAnomalies(member)" 
                                            @keydown.space.prevent="openMemberAnomalies(member)">
                                            <td class="px-4 py-3 font-medium" x-text="member.first_name + ' ' + member.last_name"></td>
                                            <td class="px-4 py-3">
                                                <span :class="{{
                                                    'bg-blue-100 text-blue-800': member.party === 'D',
                                                    'bg-red-100 text-red-800': member.party === 'R',
                                                    'bg-gray-100 text-gray-800': member.party === 'I'
                                                }}" class="px-2 py-1 rounded text-sm font-medium" x-text="member.party"></span>
                                            </td>
                                            <td class="px-4 py-3" x-text="member.state"></td>
                                            <td class="px-4 py-3 capitalize" x-text="member.chamber"></td>
                                            <td class="px-4 py-3" x-text="(member.district && member.district !== -1) ? member.district : '-'"></td>
                                            <td class="px-4 py-3" x-text="member.disclosure_count || 0"></td>
                                            <td class="px-4 py-3">
                                                <span :class="member.anomaly_count > 0 ? 'bg-red-100 text-red-800 font-bold' : 'text-gray-500'"
                                                      class="px-2 py-1 rounded text-sm"
                                                      x-text="member.anomaly_count || 0"></span>
                                            </td>
                                            <td class="px-4 py-3">
                                                <span :class="member.in_office ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'"
                                                      class="px-2 py-1 rounded text-xs"
                                                      x-text="member.in_office ? 'In Office' : 'Retired'"></span>
                                            </td>
                                        </tr>
                                    </template>
                                </tbody>
                            </table>
                            
                            <template x-if="members.length === 0">
                                <div class="text-center py-12 text-gray-500">
                                    No members found matching your filters.
                                </div>
                            </template>
                        </div>
                    </template>
                    
                    <!-- Pagination -->
                    <template x-if="!loading && members.length > 0">
                        <div class="mt-6 flex justify-between items-center">
                            <span class="text-gray-600 text-sm">
                                Showing <span x-text="members.length"></span> of <span x-text="total"></span> members
                            </span>
                            <div class="flex gap-2">
                                <button @click="previousPage()" 
                                        :disabled="page <= 1"
                                        class="px-4 py-2 border rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50">
                                    Previous
                                </button>
                                <span class="px-4 py-2" x-text="'Page ' + page"></span>
                                <button @click="nextPage()" 
                                        :disabled="page * pageSize >= total"
                                        class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed">
                                    Next
                                </button>
                            </div>
                        </div>
                    </template>
                </div>
            </div>
            
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
            
            <!-- PDF Fallback Modal -->
            <div x-show="showPdfFallback" class="fixed inset-0 z-50 overflow-y-auto" x-cloak>
                <div class="flex items-center justify-center min-h-screen px-4">
                    <div class="fixed inset-0 bg-black/50" @click="showPdfFallback = false"></div>
                    <div class="relative bg-white rounded-xl shadow-2xl max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
                        <div class="flex items-start justify-between mb-4">
                            <div>
                                <h3 class="text-xl font-bold text-gray-800">📄 Document Not Available</h3>
                                <p class="text-sm text-gray-500 mt-1">Unable to open this PDF file</p>
                            </div>
                            <button @click="showPdfFallback = false" class="text-gray-400 hover:text-gray-600 text-2xl flex-shrink-0">✕</button>
                        </div>
                        
                        <div class="space-y-4 text-sm text-gray-700">
                            <div class="p-4 bg-red-50 border border-red-200 rounded-lg">
                                <p class="font-medium text-red-900 mb-2">❌ What Went Wrong</p>
                                <p class="text-red-800 mb-2">The PDF file could not be accessed. This can happen for several reasons:</p>
                                <ul class="list-disc list-inside space-y-1 text-red-800">
                                    <li><strong>File Deleted:</strong> The PDF may have been removed from the source server</li>
                                    <li><strong>URL Changed:</strong> The source website may have reorganized or updated their file locations</li>
                                    <li><strong>Server Issues:</strong> The hosting server may be temporarily unavailable</li>
                                    <li><strong>Access Restricted:</strong> Some documents may require special permissions or authentication</li>
                                    <li><strong>Network Error:</strong> There may be a connectivity issue between our server and the source</li>
                                    <li><strong>CORS Restriction:</strong> Cross-origin restrictions may prevent direct access</li>
                                </ul>
                            </div>
                            
                            <div class="p-4 bg-blue-50 border border-blue-200 rounded-lg">
                                <p class="font-medium text-blue-900 mb-2">📋 What We Know About This Disclosure</p>
                                <div class="space-y-2 text-blue-800">
                                    <div class="flex justify-between">
                                        <span class="font-medium">Member:</span>
                                        <span x-text="selectedPdf?.member_name || 'Unknown'"></span>
                                    </div>
                                    <div class="flex justify-between">
                                        <span class="font-medium">Filing Year:</span>
                                        <span x-text="selectedPdf?.filing_year || 'Unknown'"></span>
                                    </div>
                                    <div class="flex justify-between">
                                        <span class="font-medium">Filing Type:</span>
                                        <span x-text="selectedPdf?.filing_type || 'Unknown'"></span>
                                    </div>
                                    <div class="flex justify-between">
                                        <span class="font-medium">Parse Status:</span>
                                        <span x-text="selectedPdf?.parsed ? 'Parsed ✓' : 'Pending'"></span>
                                    </div>
                                    <div class="border-t border-blue-200 pt-2 mt-2">
                                        <span class="font-medium">Document ID:</span>
                                        <p class="font-mono text-xs text-blue-600 break-all bg-blue-100 p-2 rounded mt-1" x-text="selectedPdf?.document_id || 'Unknown'"></p>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="p-4 bg-gray-50 border border-gray-200 rounded-lg">
                                <p class="font-medium text-gray-900 mb-2">🔗 Document Source URL</p>
                                <p class="text-xs text-gray-600">The original URL we attempted to access:</p>
                                <p class="font-mono text-xs text-gray-700 break-all bg-gray-100 p-2 rounded mt-2" x-text="selectedPdf?.document_url || 'No URL available'"></p>
                            </div>
                            
                            <div class="p-4 bg-amber-50 border border-amber-200 rounded-lg">
                                <p class="font-medium text-amber-900 mb-2">💡 What You Can Do</p>
                                <ul class="list-disc list-inside space-y-1 text-amber-800">
                                    <li>Try again later - the source server may be temporarily down</li>
                                    <li>Check if the data shown above is sufficient for your needs</li>
                                    <li>Visit the original source website directly using the URL below</li>
                                    <li>Contact the source provider if you believe this is an error</li>
                                </ul>
                            </div>
                        </div>
                        
                        <div class="mt-6 flex flex-col sm:flex-row gap-3 justify-end">
                            <button @click="showPdfFallback = false" class="px-4 py-2 text-gray-700 border border-gray-300 hover:bg-gray-50 rounded-lg order-2 sm:order-1">
                                Close
                            </button>
                            <a :href="selectedPdf?.document_url" 
                               target="_blank"
                               rel="noopener noreferrer"
                               class="px-4 py-2 bg-blue-600 text-white hover:bg-blue-700 rounded-lg text-center order-1 sm:order-2">
                                🔗 Try Original Link
                            </a>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            function membersPage() {{
                return {{
                    members: [],
                    total: 0,
                    page: 1,
                    pageSize: 50,
                    search: '',
                    partyFilter: '',
                    stateFilter: '',
                    districtFilter: '',
                    chamberFilter: '',
                    statusFilter: '',
                    disclosureMinFilter: '',
                    anomalyMinFilter: '',
                    sortField: 'name',
                    sortOrder: 'asc',
                    loading: false,
                    showAnomaliesModal: false,
                    anomaliesLoading: false,
                    selectedMember: null,
                    memberAnomalies: [],

                    async init() {{
                        console.log('[Members] Initializing...');
                        await this.loadMembers();
                    }},

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
                            const url = `/api/anomalies?member_id=${{member.id}}&page_size=100`;
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

                    async loadMembers() {{
                        if (this.loading) return;

                        this.loading = true;

                        try {{
                            let url = `/api/members?page=${{this.page}}&page_size=${{this.pageSize}}`;
                            if (this.search) url += `&search=${{encodeURIComponent(this.search)}}`;
                            if (this.partyFilter) url += `&party=${{this.partyFilter}}`;
                            if (this.stateFilter) url += `&state=${{this.stateFilter.toUpperCase()}}`;
                            if (this.districtFilter) url += `&district=${{this.districtFilter}}`;
                            if (this.chamberFilter) url += `&chamber=${{this.chamberFilter}}`;
                            if (this.statusFilter) url += `&in_office=${{this.statusFilter}}`;
                            if (this.disclosureMinFilter) url += `&min_disclosures=${{this.disclosureMinFilter}}`;
                            if (this.anomalyMinFilter) url += `&min_anomalies=${{this.anomalyMinFilter}}`;

                            // Apply server-side sorting for all fields
                            url += `&sort_by=${{this.sortField}}&sort_order=${{this.sortOrder}}`;

                            console.log('[Members] Fetching:', url);
                            const data = await fetch(url).then(r => r.json());

                            this.members = data.members || [];
                            this.total = data.total || 0;

                            console.log('[Members] Loaded:', this.members.length, 'total:', this.total);
                        }} catch (e) {{
                            console.error('[Members] Error:', e);
                            alert('Failed to load members: ' + e.message);
                        }} finally {{
                            this.loading = false;
                        }}
                    }},

                    async sortBy(field) {{
                        console.log('[Members] Sort clicked:', field, 'current:', this.sortField, this.sortOrder);

                        // Reset to page 1 when changing sort
                        if (this.sortField !== field) {{
                            this.page = 1;
                        }}

                        // Toggle sort order if clicking the same field
                        if (this.sortField === field) {{
                            this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
                        }} else {{
                            this.sortField = field;
                            // Default sort order based on field
                            this.sortOrder = (field === 'name') ? 'asc' : 'desc';
                        }}

                        await this.loadMembers();
                    }},

                    resetFilters() {{
                        this.search = '';
                        this.partyFilter = '';
                        this.stateFilter = '';
                        this.districtFilter = '';
                        this.chamberFilter = '';
                        this.statusFilter = '';
                        this.disclosureMinFilter = '';
                        this.anomalyMinFilter = '';
                        this.page = 1;
                        this.sortField = 'name';
                        this.sortOrder = 'asc';
                        this.loadMembers();
                    }},


                    previousPage() {{
                        if (this.page > 1) {{
                            this.page--;
                            this.loadMembers();
                        }}
                    }},

                    nextPage() {{
                        if (this.page * this.pageSize < this.total) {{
                            this.page++;
                            this.loadMembers();
                        }}
                    }}
                }};
            }}
        </script>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ============================================================================
# DISCLOSURES PAGE
# ============================================================================

@router.get("/disclosures", response_class=HTMLResponse)
async def disclosures_page():
    """Serve the financial disclosures browsing page."""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Financial Disclosures - Honest Congress</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
        {STYLES}
    </head>
    <body class="bg-gray-100 min-h-screen">
        <div x-data="disclosuresPage()" x-init="init()" x-cloak>
            {HEADER_HTML}
            
            <div class="container mx-auto px-4 py-8">
                <div class="glass-card rounded-xl shadow-lg p-6">
                    <h2 class="text-3xl font-bold text-gray-800 mb-6">📄 Financial Disclosures</h2>
                    
                    <div class="mb-6 p-4 bg-blue-50 rounded-lg">
                        <p class="text-blue-800">
                            <strong>Financial Disclosures (FD)</strong> are annual reports where members report their assets, 
                            income, liabilities, and transactions. These are different from PTRs (stock trades).
                        </p>
                    </div>
                    
                    <!-- Filters -->
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                        <input type="text" 
                               x-model="memberSearch" 
                               @input.debounce.300ms="loadDisclosures()"
                               placeholder="Search member..." 
                               class="border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        
                        <select x-model="yearFilter" @change="loadDisclosures()" class="border rounded-lg px-4 py-2">
                            <option value="">All Years</option>
                            <option value="2026">2026</option>
                            <option value="2025">2025</option>
                            <option value="2024">2024</option>
                            <option value="2023">2023</option>
                            <option value="2022">2022</option>
                            <option value="2021">2021</option>
                            <option value="2020">2020</option>
                            <option value="2019">2019</option>
                            <option value="2018">2018</option>
                            <option value="2017">2017</option>
                            <option value="2016">2016</option>
                            <option value="2015">2015</option>
                            <option value="2014">2014</option>
                            <option value="2013">2013</option>
                            <option value="2012">2012</option>
                        </select>
                        
                        <select x-model="typeFilter" @change="loadDisclosures()" class="border rounded-lg px-4 py-2">
                            <option value="">All Types</option>
                            <option value="A">A - Annual Report</option>
                            <option value="C">C - Candidate Report</option>
                            <option value="FD">FD - Financial Disclosure</option>
                            <option value="G">G - Gingles Report</option>
                            <option value="H">H - House Member Report</option>
                            <option value="O">O - Original Report</option>
                            <option value="P">P - Periodic Report</option>
                            <option value="T">T - Termination Report</option>
                            <option value="X">X - Amended/Corrected</option>
                        </select>
                        
                        <select x-model="parsedFilter" @change="loadDisclosures()" class="border rounded-lg px-4 py-2">
                            <option value="">All Status</option>
                            <option value="true">Parsed</option>
                            <option value="false">Not Parsed</option>
                        </select>
                    </div>
                    <!-- Filing Type Legend -->
                    <div class="mb-6 p-4 bg-blue-50 rounded-lg border border-blue-200">
                        <h3 class="font-semibold text-blue-900 mb-3">📋 Filing Type Legend</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3 text-sm">
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">A</span>
                                <p class="text-xs text-blue-800 mt-1">Annual Report</p>
                                <p class="text-xs text-gray-500">(24)</p>
                            </div>
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">C</span>
                                <p class="text-xs text-blue-800 mt-1">Candidate Report</p>
                                <p class="text-xs text-gray-500">(48)</p>
                            </div>
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">FD</span>
                                <p class="text-xs text-blue-800 mt-1">Financial Disclosure</p>
                                <p class="text-xs text-gray-500">(4,742)</p>
                            </div>
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">G</span>
                                <p class="text-xs text-blue-800 mt-1">Gingles Report</p>
                                <p class="text-xs text-gray-500">(1)</p>
                            </div>
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">H</span>
                                <p class="text-xs text-blue-800 mt-1">House Member Report</p>
                                <p class="text-xs text-gray-500">(43)</p>
                            </div>
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">O</span>
                                <p class="text-xs text-blue-800 mt-1">Original Report</p>
                                <p class="text-xs text-gray-500">(212)</p>
                            </div>
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">P</span>
                                <p class="text-xs text-blue-800 mt-1">Periodic Report</p>
                                <p class="text-xs text-gray-500">(188)</p>
                            </div>
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">T</span>
                                <p class="text-xs text-blue-800 mt-1">Termination Report</p>
                                <p class="text-xs text-gray-500">(1,256)</p>
                            </div>
                            <div class="bg-white p-3 rounded border border-blue-100">
                                <span class="font-medium text-blue-900">X</span>
                                <p class="text-xs text-blue-800 mt-1">Amended/Corrected</p>
                                <p class="text-xs text-gray-500">(191)</p>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Loading -->
                    <template x-if="loading">
                        <div class="text-center py-12">
                            <div class="animate-spin inline-block w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full mb-4"></div>
                            <p class="text-gray-600">Loading disclosures...</p>
                        </div>
                    </template>
                    
                    <!-- Disclosures Table -->
                    <template x-if="!loading">
                        <div class="overflow-x-auto">
                            <table class="w-full" style="table-layout: fixed;">
                                <thead class="bg-gray-50">
                                    <tr>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100 w-1/4" @click="sortBy('member')">
                                            Member <span x-show="sortField === 'member'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100 w-16" @click="sortBy('year')">
                                            Year <span x-show="sortField === 'year'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100 w-24" @click="sortBy('type')">
                                            Type <span x-show="sortField === 'type'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100 w-24" @click="sortBy('status')">
                                            Status <span x-show="sortField === 'status'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 w-20">Actions</th>
                                    </tr>
                                </thead>
                                <tbody class="divide-y">
                                    <template x-for="disc in disclosures" :key="disc.id">
                                        <tr class="hover:bg-gray-50">
                                            <td class="px-4 py-3 font-medium" x-text="disc.member_name || 'Unknown'"></td>
                                            <td class="px-4 py-3" x-text="disc.filing_year"></td>
                                            <td class="px-4 py-3">
                                                <span class="px-2 py-1 bg-green-100 text-green-800 rounded text-sm" x-text="disc.filing_type"></span>
                                            </td>
                                            <td class="px-4 py-3">
                                                <span :class="disc.parsed ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'"
                                                      class="px-2 py-1 rounded text-sm font-medium"
                                                      x-text="disc.parsed ? 'Parsed' : 'Pending'"></span>
                                            </td>
                                            <td class="px-4 py-3">
                                                <template x-if="disc.document_url">
                                                    <button @click="openPdfOrFallback(disc)" 
                                                            class="text-blue-600 hover:text-blue-800 text-sm font-medium">
                                                        View PDF
                                                    </button>
                                                </template>
                            </td>
                                        </tr>
                                    </template>
                                </tbody>
                            </table>
                            
                            <template x-if="disclosures.length === 0">
                                <div class="text-center py-12 text-gray-500">
                                    No disclosures found matching your filters.
                                </div>
                            </template>
                        </div>
                    </template>
                    
                    <!-- Pagination -->
                    <template x-if="!loading && disclosures.length > 0">
                        <div class="mt-6 flex justify-between items-center">
                            <span class="text-gray-600 text-sm">
                                Showing <span x-text="disclosures.length"></span> of <span x-text="total"></span> disclosures
                            </span>
                            <div class="flex gap-2">
                                <button @click="previousPage()" 
                                        :disabled="page <= 1"
                                        class="px-4 py-2 border rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50">
                                    Previous
                                </button>
                                <span class="px-4 py-2" x-text="'Page ' + page"></span>
                                <button @click="nextPage()" 
                                        :disabled="page * pageSize >= total"
                                        class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed">
                                    Next
                                </button>
                            </div>
                        </div>
                    </template>
                </div>
            </div>
            
            {FOOTER_HTML}
        </div>
        
        <script>
            function disclosuresPage() {{
                return {{
                    disclosures: [],
                    total: 0,
                    page: 1,
                    pageSize: 50,
                    memberSearch: '',
                    yearFilter: '',
                    typeFilter: '',
                    parsedFilter: '',
                    loading: false,
                    sortField: 'filing_date',
                    sortOrder: 'desc',
                    showPdfFallback: false,
                    selectedPdf: null,
                    
                    async init() {{
                        console.log('[Disclosures] Initializing...');
                        await this.loadDisclosures();
                    }},
                    
                    async loadDisclosures() {{
                        this.loading = true;
                        
                        try {{
                            // Use server-side sorting for accurate results across all entries
                            let url = `/api/disclosures?page=${{this.page}}&page_size=${{this.pageSize}}&is_ptr=false`;
                            url += `&sort_by=${{this.sortField}}&sort_order=${{this.sortOrder}}`;
                            
                            if (this.yearFilter) url += `&filing_year=${{this.yearFilter}}`;
                            if (this.typeFilter) url += `&filing_type=${{encodeURIComponent(this.typeFilter)}}`;
                            if (this.parsedFilter !== '') url += `&parsed=${{this.parsedFilter}}`;
                            
                            console.log('[Disclosures] Fetching (server-side sort):', url);
                            const data = await fetch(url).then(r => r.json());
                            
                            this.total = data.total || 0;
                            this.disclosures = data.disclosures || [];
                            
                            console.log('[Disclosures] Loaded:', this.disclosures.length, 'total:', this.total);
                        }} catch (e) {{
                            console.error('[Disclosures] Error:', e);
                            alert('Failed to load disclosures: ' + e.message);
                        }} finally {{
                            this.loading = false;
                        }}
                    }},
                    
                    sortBy(field) {{
                        // Map field names for server-side sorting
                        const fieldMap = {{
                            'member': 'member_name',
                            'year': 'year',
                            'type': 'type',
                            'status': 'status'
                        }};
                        
                        const sortField = fieldMap[field] || field;
                        
                        if (this.sortField === sortField) {{
                            this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
                        }} else {{
                            this.sortField = sortField;
                            this.sortOrder = 'asc';
                        }}
                        
                        this.page = 1;  // Reset to page 1 when changing sort
                        this.loadDisclosures();
                    }},
                    
                    openPdfOrFallback(disclosure) {{
                        this.selectedPdf = disclosure;
                        console.log('[Disclosures] Attempting to access PDF:', disclosure.document_url);
                        
                        // Strategy: Open in hidden iframe first to detect 404s
                        // If browser shows error page, we'll detect it and show fallback
                        // If PDF loads, we open it properly in new tab
                        
                        const testFrame = document.createElement('iframe');
                        testFrame.style.display = 'none';
                        let hasErrored = false;
                        let checkTimeout;
                        
                        // If the iframe receives an error or fails to load, show fallback
                        testFrame.onerror = () => {{
                            hasErrored = true;
                            console.error('[Disclosures] iframe onerror triggered - PDF likely 404');
                            cleanup();
                            this.showPdfFallback = true;
                        }};
                        
                        // Monitor iframe content for error page indicators
                        const monitorFrame = () => {{
                            try {{
                                const frameDoc = testFrame.contentDocument || testFrame.contentWindow.document;
                                if (frameDoc) {{
                                    const text = frameDoc.body.innerText || '';
                                    const title = frameDoc.title || '';
                                    
                                    // Check for common 404 indicators
                                    if (text.includes('404') || 
                                        text.includes('not found') || 
                                        text.includes('File or directory not found') ||
                                        title.includes('404')) {{
                                        hasErrored = true;
                                        console.error('[Disclosures] Detected 404 error page in iframe');
                                        cleanup();
                                        this.showPdfFallback = true;
                                        return;
                                    }}
                                    
                                    // If we got here and it's still loading, wait a bit more
                                    if (!frameDoc.body || frameDoc.readyState !== 'complete') {{
                                        checkTimeout = setTimeout(monitorFrame, 100);
                                        return;
                                    }}
                                    
                                    // Content loaded successfully - open in new tab
                                    console.log('[Disclosures] PDF appears valid, opening in new tab');
                                    cleanup();
                                    window.open(disclosure.document_url, '_blank');
                                }}
                            }} catch (e) {{
                                // CORS or other issue - try opening anyway
                                // Real PDFs will work, fake ones will show error page in browser
                                console.log('[Disclosures] iframe access blocked by CORS, opening directly');
                                cleanup();
                                window.open(disclosure.document_url, '_blank');
                            }}
                        }};
                        
                        const cleanup = () => {{
                            clearTimeout(checkTimeout);
                            testFrame.onerror = null;
                            if (testFrame.parentNode) {{
                                testFrame.parentNode.removeChild(testFrame);
                            }}
                        }};
                        
                        // Set overall timeout
                        const overallTimeout = setTimeout(() => {{
                            if (!hasErrored) {{
                                console.log('[Disclosures] Check timeout, opening PDF');
                                cleanup();
                                window.open(disclosure.document_url, '_blank');
                            }}
                        }}, 2000);
                        
                        checkTimeout = overallTimeout;
                        
                        // Append iframe and load URL
                        document.body.appendChild(testFrame);
                        testFrame.src = disclosure.document_url;
                    }},
                    
                    previousPage() {{
                        if (this.page > 1) {{
                            this.page--;
                            this.loadDisclosures();
                        }}
                    }},
                    
                    nextPage() {{
                        if (this.page * this.pageSize < this.total) {{
                            this.page++;
                            this.loadDisclosures();
                        }}
                    }}
                }};
            }}
        </script>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ============================================================================
# TRADES PAGE
# ============================================================================

@router.get("/trades", response_class=HTMLResponse)
async def trades_page():
    """Serve the stock trades (PTR) browsing page."""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Stock Trades - Honest Congress</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
        {STYLES}
    </head>
    <body class="bg-gray-100 min-h-screen">
        <div x-data="tradesPage()" x-init="init()" x-cloak>
            {HEADER_HTML}
            
            <div class="container mx-auto px-4 py-8">
                <div class="glass-card rounded-xl shadow-lg p-6">
                    <h2 class="text-3xl font-bold text-gray-800 mb-6">📈 Stock Trades (PTRs)</h2>
                    
                    <div class="mb-6 p-4 bg-purple-50 rounded-lg">
                        <p class="text-purple-800">
                            <strong>Periodic Transaction Reports (PTR)</strong> are filed when members or their spouses 
                            make stock trades. By law, trades over $1,000 must be reported within 45 days.
                        </p>
                    </div>
                    
                    <!-- Filters -->
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                        <input type="text" 
                               x-model="memberSearch" 
                               @input.debounce.300ms="loadTrades()"
                               placeholder="Search member..." 
                               class="border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-purple-500">
                        
                        <select x-model="yearFilter" @change="loadTrades()" class="border rounded-lg px-4 py-2">
                            <option value="">All Years</option>
                            <option value="2026">2026</option>
                            <option value="2025">2025</option>
                            <option value="2024">2024</option>
                            <option value="2023">2023</option>
                            <option value="2022">2022</option>
                            <option value="2021">2021</option>
                            <option value="2020">2020</option>
                            <option value="2019">2019</option>
                            <option value="2018">2018</option>
                            <option value="2017">2017</option>
                            <option value="2016">2016</option>
                            <option value="2015">2015</option>
                            <option value="2014">2014</option>
                            <option value="2013">2013</option>
                            <option value="2012">2012</option>
                        </select>
                        
                        <select x-model="typeFilter" @change="loadTrades()" class="border rounded-lg px-4 py-2">
                            <option value="">All Types</option>
                            <option value="PTR">PTR</option>
                            <option value="PTR - Periodic">PTR - Periodic</option>
                        </select>
                        
                        <select x-model="parsedFilter" @change="loadTrades()" class="border rounded-lg px-4 py-2">
                            <option value="">All Status</option>
                            <option value="true">Parsed</option>
                            <option value="false">Not Parsed</option>
                        </select>
                    </div>
                    
                    <!-- Loading -->
                    <template x-if="loading">
                        <div class="text-center py-12">
                            <div class="animate-spin inline-block w-12 h-12 border-4 border-purple-500 border-t-transparent rounded-full mb-4"></div>
                            <p class="text-gray-600">Loading trades...</p>
                        </div>
                    </template>
                    
                    <!-- Trades Table -->
                    <template x-if="!loading">
                        <div class="overflow-x-auto">
                            <table class="w-full">
                                <thead class="bg-gray-50">
                                    <tr>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('member')">
                                            Member <span x-show="sortField === 'member'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('year')">
                                            Year <span x-show="sortField === 'year'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Type</th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('filing_date')">
                                            Filing Date <span x-show="sortField === 'filing_date'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Status</th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Actions</th>
                                    </tr>
                                </thead>
                                <tbody class="divide-y">
                                    <template x-for="trade in trades" :key="trade.id">
                                        <tr class="hover:bg-gray-50">
                                            <td class="px-4 py-3 font-medium" x-text="trade.member_name || 'Unknown'"></td>
                                            <td class="px-4 py-3" x-text="trade.filing_year"></td>
                                            <td class="px-4 py-3">
                                                <span class="px-2 py-1 bg-purple-100 text-purple-800 rounded text-sm" x-text="trade.filing_type"></span>
                                            </td>
                                            <td class="px-4 py-3" x-text="trade.filing_date ? new Date(trade.filing_date).toLocaleDateString() : '-'"></td>
                                            <td class="px-4 py-3">
                                                <span :class="trade.parsed ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'"
                                                      class="px-2 py-1 rounded text-sm font-medium"
                                                      x-text="trade.parsed ? 'Parsed' : 'Pending'"></span>
                                            </td>
                                            <td class="px-4 py-3">
                                                <template x-if="trade.document_url">
                                                    <a :href="trade.document_url" 
                                                       target="_blank"
                                                       class="text-purple-600 hover:text-purple-800 text-sm">
                                                        View PDF
                                                    </a>
                                                </template>
                                            </td>
                                        </tr>
                                    </template>
                                </tbody>
                            </table>
                            
                            <template x-if="trades.length === 0">
                                <div class="text-center py-12 text-gray-500">
                                    No trades found matching your filters.
                                </div>
                            </template>
                        </div>
                    </template>
                    
                    <!-- Pagination -->
                    <template x-if="!loading && trades.length > 0">
                        <div class="mt-6 flex justify-between items-center">
                            <span class="text-gray-600 text-sm">
                                Showing <span x-text="trades.length"></span> of <span x-text="total"></span> trades
                            </span>
                            <div class="flex gap-2">
                                <button @click="previousPage()" 
                                        :disabled="page <= 1"
                                        class="px-4 py-2 border rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50">
                                    Previous
                                </button>
                                <span class="px-4 py-2" x-text="'Page ' + page"></span>
                                <button @click="nextPage()" 
                                        :disabled="page * pageSize >= total"
                                        class="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed">
                                    Next
                                </button>
                            </div>
                        </div>
                    </template>
                </div>
            </div>
            
            {FOOTER_HTML}
        </div>
        
        <script>
            function tradesPage() {{
                return {{
                    trades: [],
                    total: 0,
                    page: 1,
                    pageSize: 50,
                    memberSearch: '',
                    yearFilter: '',
                    typeFilter: '',
                    parsedFilter: '',
                    loading: false,
                    sortField: 'filing_date',
                    sortOrder: 'desc',
                    
                    async init() {{
                        console.log('[Trades] Initializing...');
                        await this.loadTrades();
                    }},
                    
                    async loadTrades() {{
                        this.loading = true;
                        
                        try {{
                            let url = `/api/disclosures?page=${{this.page}}&page_size=${{this.pageSize}}&is_ptr=true`;
                            if (this.yearFilter) url += `&filing_year=${{this.yearFilter}}`;
                            if (this.typeFilter) url += `&filing_type=${{encodeURIComponent(this.typeFilter)}}`;
                            if (this.parsedFilter !== '') url += `&parsed=${{this.parsedFilter}}`;
                            
                            console.log('[Trades] Fetching:', url);
                            const data = await fetch(url).then(r => r.json());
                            
                            this.trades = data.disclosures || [];
                            this.total = data.total || 0;
                            
                            // Apply client-side sorting
                            this.applySorting();
                            
                            console.log('[Trades] Loaded:', this.trades.length, 'total:', this.total);
                        }} catch (e) {{
                            console.error('[Trades] Error:', e);
                            alert('Failed to load trades: ' + e.message);
                        }} finally {{
                            this.loading = false;
                        }}
                    }},
                    
                    sortBy(field) {{
                        if (this.sortField === field) {{
                            this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
                        }} else {{
                            this.sortField = field;
                            this.sortOrder = 'asc';
                        }}
                        this.applySorting();
                    }},
                    
                    applySorting() {{
                        const sortMultiplier = this.sortOrder === 'asc' ? 1 : -1;
                        
                        if (this.sortField === 'member') {{
                            this.trades.sort((a, b) => (a.member_name || '').localeCompare(b.member_name || '') * sortMultiplier);
                        }} else if (this.sortField === 'year') {{
                            this.trades.sort((a, b) => ((a.filing_year || 0) - (b.filing_year || 0)) * sortMultiplier);
                        }} else if (this.sortField === 'filing_date') {{
                            this.trades.sort((a, b) => {{
                                const dateA = new Date(a.filing_date || 0);
                                const dateB = new Date(b.filing_date || 0);
                                return (dateA - dateB) * sortMultiplier;
                            }});
                        }}
                    }},
                    
                    previousPage() {{
                        if (this.page > 1) {{
                            this.page--;
                            this.loadTrades();
                        }}
                    }},
                    
                    nextPage() {{
                        if (this.page * this.pageSize < this.total) {{
                            this.page++;
                            this.loadTrades();
                        }}
                    }}
                }};
            }}
        </script>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ============================================================================
# PARSED DOCUMENTS PAGE
# ============================================================================

@router.get("/parsed", response_class=HTMLResponse)
async def parsed_page():
    """Serve the parsed documents browsing page."""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Parsed Documents - Honest Congress</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
        {STYLES}
    </head>
    <body class="bg-gray-100 min-h-screen">
        <div x-data="parsedPage()" x-init="init()" x-cloak>
            {HEADER_HTML}
            
            <div class="container mx-auto px-4 py-8">
                <div class="glass-card rounded-xl shadow-lg p-6">
                    <h2 class="text-3xl font-bold text-gray-800 mb-6">📊 Parsed Documents</h2>
                    
                    <div class="mb-6 p-4 bg-teal-50 rounded-lg">
                        <p class="text-teal-800">
                            <strong>Parsed Documents</strong> are disclosures and PTRs that have been processed to extract 
                            structured data like assets, transactions, and liabilities.
                        </p>
                    </div>
                    
                    <!-- Filters -->
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                        <input type="text" 
                               x-model="memberSearch" 
                               @input.debounce.300ms="loadParsed()"
                               placeholder="Search member..." 
                               class="border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500">
                        
                        <select x-model="docTypeFilter" @change="loadParsed()" class="border rounded-lg px-4 py-2">
                            <option value="">All Document Types</option>
                            <option value="disclosure">Financial Disclosures</option>
                            <option value="ptr">Stock Trades (PTR)</option>
                        </select>
                        
                        <select x-model="yearFilter" @change="loadParsed()" class="border rounded-lg px-4 py-2">
                            <option value="">All Years</option>
                            <option value="2026">2026</option>
                            <option value="2025">2025</option>
                            <option value="2024">2024</option>
                            <option value="2023">2023</option>
                            <option value="2022">2022</option>
                            <option value="2021">2021</option>
                            <option value="2020">2020</option>
                            <option value="2019">2019</option>
                            <option value="2018">2018</option>
                            <option value="2017">2017</option>
                            <option value="2016">2016</option>
                            <option value="2015">2015</option>
                            <option value="2014">2014</option>
                            <option value="2013">2013</option>
                            <option value="2012">2012</option>
                        </select>
                        
                        <select x-model="hasDataFilter" @change="loadParsed()" class="border rounded-lg px-4 py-2">
                            <option value="">All Documents</option>
                            <option value="assets">With Assets</option>
                            <option value="transactions">With Transactions</option>
                            <option value="liabilities">With Liabilities</option>
                        </select>
                    </div>
                    
                    <!-- Loading -->
                    <template x-if="loading">
                        <div class="text-center py-12">
                            <div class="animate-spin inline-block w-12 h-12 border-4 border-teal-500 border-t-transparent rounded-full mb-4"></div>
                            <p class="text-gray-600">Loading parsed documents...</p>
                        </div>
                    </template>
                    
                    <!-- Parsed Documents Table -->
                    <template x-if="!loading">
                        <div class="overflow-x-auto">
                            <table class="w-full">
                                <thead class="bg-gray-50">
                                    <tr>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('member')">
                                            Member <span x-show="sortField === 'member'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('year')">
                                            Year <span x-show="sortField === 'year'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Type</th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('assets')">
                                            Assets <span x-show="sortField === 'assets'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('transactions')">
                                            Transactions <span x-show="sortField === 'transactions'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('liabilities')">
                                            Liabilities <span x-show="sortField === 'liabilities'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500 cursor-pointer hover:bg-gray-100" @click="sortBy('filing_date')">
                                            Filing Date <span x-show="sortField === 'filing_date'" x-text="sortOrder === 'asc' ? '↑' : '↓'"></span>
                                        </th>
                                        <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Actions</th>
                                    </tr>
                                </thead>
                                <tbody class="divide-y">
                                    <template x-for="doc in documents" :key="doc.id">
                                        <tr class="hover:bg-gray-50">
                                            <td class="px-4 py-3 font-medium" x-text="doc.member_name || 'Unknown'"></td>
                                            <td class="px-4 py-3" x-text="doc.filing_year"></td>
                                            <td class="px-4 py-3">
                                                <span :class="doc.is_ptr ? 'bg-purple-100 text-purple-800' : 'bg-green-100 text-green-800'"
                                                      class="px-2 py-1 rounded text-sm"
                                                      x-text="doc.is_ptr ? 'PTR' : 'FD'"></span>
                                            </td>
                                            <td class="px-4 py-3 text-center">
                                                <span class="font-medium" x-text="doc.asset_count || 0"></span>
                                            </td>
                                            <td class="px-4 py-3 text-center">
                                                <span class="font-medium" x-text="doc.transaction_count || 0"></span>
                                            </td>
                                            <td class="px-4 py-3 text-center">
                                                <span class="font-medium" x-text="doc.liability_count || 0"></span>
                                            </td>
                                            <td class="px-4 py-3" x-text="doc.filing_date ? new Date(doc.filing_date).toLocaleDateString() : '-'"></td>
                                            <td class="px-4 py-3">
                                                <template x-if="doc.document_url">
                                                    <a :href="doc.document_url" 
                                                       target="_blank"
                                                       class="text-teal-600 hover:text-teal-800 text-sm">
                                                        View PDF
                                                    </a>
                                                </template>
                                            </td>
                                        </tr>
                                    </template>
                                </tbody>
                            </table>
                            
                            <template x-if="documents.length === 0">
                                <div class="text-center py-12 text-gray-500">
                                    No parsed documents found matching your filters.
                                </div>
                            </template>
                        </div>
                    </template>
                    
                    <!-- Pagination -->
                    <template x-if="!loading && documents.length > 0">
                        <div class="mt-6 flex justify-between items-center">
                            <span class="text-gray-600 text-sm">
                                Showing <span x-text="documents.length"></span> of <span x-text="total"></span> documents
                            </span>
                            <div class="flex gap-2">
                                <button @click="previousPage()" 
                                        :disabled="page <= 1"
                                        class="px-4 py-2 border rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50">
                                    Previous
                                </button>
                                <span class="px-4 py-2" x-text="'Page ' + page"></span>
                                <button @click="nextPage()" 
                                        :disabled="page * pageSize >= total"
                                        class="px-4 py-2 bg-teal-600 text-white rounded-lg hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed">
                                    Next
                                </button>
                            </div>
                        </div>
                    </template>
                </div>
            </div>
            
            {FOOTER_HTML}
        </div>
        
        <script>
            function parsedPage() {{
                return {{
                    documents: [],
                    total: 0,
                    page: 1,
                    pageSize: 50,
                    memberSearch: '',
                    docTypeFilter: '',
                    yearFilter: '',
                    hasDataFilter: '',
                    loading: false,
                    sortField: 'filing_date',
                    sortOrder: 'desc',
                    
                    async init() {{
                        console.log('[Parsed] Initializing...');
                        await this.loadParsed();
                    }},
                    
                    async loadParsed() {{
                        this.loading = true;
                        
                        try {{
                            let url = `/api/disclosures?page=${{this.page}}&page_size=${{this.pageSize}}&parsed=true`;
                            
                            if (this.docTypeFilter === 'disclosure') {{
                                url += '&is_ptr=false';
                            }} else if (this.docTypeFilter === 'ptr') {{
                                url += '&is_ptr=true';
                            }}
                            
                            if (this.yearFilter) {{
                                url += `&filing_year=${{this.yearFilter}}`;
                            }}
                            
                            // Note: has_data filters would need backend support
                            
                            console.log('[Parsed] Fetching:', url);
                            const data = await fetch(url).then(r => r.json());
                            
                            this.documents = data.disclosures || [];
                            this.total = data.total || 0;
                            
                            // Apply client-side sorting
                            this.applySorting();
                            
                            console.log('[Parsed] Loaded:', this.documents.length, 'total:', this.total);
                        }} catch (e) {{
                            console.error('[Parsed] Error:', e);
                            alert('Failed to load parsed documents: ' + e.message);
                        }} finally {{
                            this.loading = false;
                        }}
                    }},
                    
                    sortBy(field) {{
                        if (this.sortField === field) {{
                            this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
                        }} else {{
                            this.sortField = field;
                            this.sortOrder = 'asc';
                        }}
                        this.applySorting();
                    }},
                    
                    applySorting() {{
                        const sortMultiplier = this.sortOrder === 'asc' ? 1 : -1;
                        
                        if (this.sortField === 'member') {{
                            this.documents.sort((a, b) => (a.member_name || '').localeCompare(b.member_name || '') * sortMultiplier);
                        }} else if (this.sortField === 'year') {{
                            this.documents.sort((a, b) => ((a.filing_year || 0) - (b.filing_year || 0)) * sortMultiplier);
                        }} else if (this.sortField === 'assets') {{
                            this.documents.sort((a, b) => ((a.asset_count || 0) - (b.asset_count || 0)) * sortMultiplier);
                        }} else if (this.sortField === 'transactions') {{
                            this.documents.sort((a, b) => ((a.transaction_count || 0) - (b.transaction_count || 0)) * sortMultiplier);
                        }} else if (this.sortField === 'liabilities') {{
                            this.documents.sort((a, b) => ((a.liability_count || 0) - (b.liability_count || 0)) * sortMultiplier);
                        }} else if (this.sortField === 'filing_date') {{
                            this.documents.sort((a, b) => {{
                                const dateA = new Date(a.filing_date ||  0);
                                const dateB = new Date(b.filing_date || 0);
                                return (dateA - dateB) * sortMultiplier;
                            }});
                        }}
                    }},
                    
                    previousPage() {{
                        if (this.page > 1) {{
                            this.page--;
                            this.loadParsed();
                        }}
                    }},
                    
                    nextPage() {{
                        if (this.page * this.pageSize < this.total) {{
                            this.page++;
                            this.loadParsed();
                        }}
                    }}
                }};
            }}
        </script>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ============================================================================
# ANOMALIES PAGE (redirect to main dashboard)
# ============================================================================

@router.get("/anomalies", response_class=HTMLResponse)
async def anomalies_page():
    """Redirect to the main dashboard which has the full anomalies implementation."""
    from .dashboard import DASHBOARD_HTML
    response = HTMLResponse(content=DASHBOARD_HTML)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

