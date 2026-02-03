"""Enhanced web dashboard with detailed anomaly display."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Honest Congress - Financial Disclosure Analyzer</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
    <style>[x-cloak] { display: none !important; }</style>
</head>
<body class="bg-gray-100 min-h-screen">
    <div x-data="dashboard()" x-init="init()" x-cloak>
        <!-- Header -->
        <header class="bg-blue-900 text-white shadow-lg">
            <div class="container mx-auto px-4 py-6">
                <h1 class="text-3xl font-bold">🏛️ Honest Congress</h1>
                <p class="text-blue-200 mt-1">Congressional Financial Disclosure Analyzer</p>
            </div>
        </header>

        <!-- Stats Cards -->
        <div class="container mx-auto px-4 py-8">
            <div class="grid grid-cols-1 md:grid-cols-5 gap-6 mb-8">
                <div class="bg-white rounded-lg shadow p-6">
                    <div class="text-gray-500 text-sm font-medium">Total Members</div>
                    <div class="text-3xl font-bold text-blue-900" x-text="stats.totalMembers">-</div>
                </div>
                <div class="bg-white rounded-lg shadow p-6">
                    <div class="text-gray-500 text-sm font-medium">Disclosures</div>
                    <div class="text-3xl font-bold text-green-600" x-text="stats.totalDisclosures">-</div>
                </div>
                <div class="bg-white rounded-lg shadow p-6">
                    <div class="text-gray-500 text-sm font-medium">Stock Trades</div>
                    <div class="text-3xl font-bold text-purple-600" x-text="stats.totalPtrs">-</div>
                </div>
                <div class="bg-white rounded-lg shadow p-6">
                    <div class="text-gray-500 text-sm font-medium">Anomalies</div>
                    <div class="text-3xl font-bold text-red-600" x-text="stats.totalAnomalies">-</div>
                </div>
                <div class="bg-white rounded-lg shadow p-6">
                    <div class="text-gray-500 text-sm font-medium">High Severity</div>
                    <div class="text-3xl font-bold text-orange-600" x-text="stats.highSeverity">-</div>
                </div>
            </div>

            <!-- Tabs -->
            <div class="bg-white rounded-lg shadow">
                <div class="border-b">
                    <nav class="flex -mb-px">
                        <button @click="tab = 'members'" 
                                :class="tab === 'members' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
                                class="py-4 px-6 border-b-2 font-medium">Members</button>
                        <button @click="tab = 'disclosures'" 
                                :class="tab === 'disclosures' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
                                class="py-4 px-6 border-b-2 font-medium">Disclosures</button>
                        <button @click="tab = 'trades'; loadTrades()" 
                                :class="tab === 'trades' ? 'border-purple-500 text-purple-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
                                class="py-4 px-6 border-b-2 font-medium">📈 Stock Trades</button>
                        <button @click="tab = 'anomalies'" 
                                :class="tab === 'anomalies' ? 'border-red-500 text-red-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
                                class="py-4 px-6 border-b-2 font-medium">🚨 Anomalies</button>
                    </nav>
                </div>

                <!-- Members Tab -->
                <div x-show="tab === 'members'" class="p-6">
                    <div class="flex gap-4 mb-4">
                        <input type="text" x-model="memberSearch" @input.debounce.300ms="loadMembers()"
                               placeholder="Search by name..." 
                               class="flex-1 border rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        <select x-model="partyFilter" @change="loadMembers()" class="border rounded-lg px-4 py-2">
                            <option value="">All Parties</option>
                            <option value="D">Democrat</option>
                            <option value="R">Republican</option>
                            <option value="I">Independent</option>
                        </select>
                        <select x-model="chamberFilter" @change="loadMembers()" class="border rounded-lg px-4 py-2">
                            <option value="">All Chambers</option>
                            <option value="house">House</option>
                            <option value="senate">Senate</option>
                        </select>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full">
                            <thead class="bg-gray-50">
                                <tr>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Name</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Party</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">State</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Chamber</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">District</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y">
                                <template x-for="member in members" :key="member.id">
                                    <tr class="hover:bg-gray-50">
                                        <td class="px-4 py-3 font-medium" x-text="member.first_name + ' ' + member.last_name"></td>
                                        <td class="px-4 py-3">
                                            <span :class="{'bg-blue-100 text-blue-800': member.party === 'D', 'bg-red-100 text-red-800': member.party === 'R', 'bg-gray-100 text-gray-800': member.party === 'I'}" 
                                                  class="px-2 py-1 rounded text-sm" x-text="member.party"></span>
                                        </td>
                                        <td class="px-4 py-3" x-text="member.state"></td>
                                        <td class="px-4 py-3 capitalize" x-text="member.chamber"></td>
                                        <td class="px-4 py-3" x-text="member.district || '-'"></td>
                                    </tr>
                                </template>
                            </tbody>
                        </table>
                    </div>
                    <div class="mt-4 flex justify-between items-center">
                        <span class="text-gray-500 text-sm">Showing <span x-text="members.length"></span> of <span x-text="stats.totalMembers"></span> members</span>
                        <div class="flex gap-2">
                            <button @click="memberPage--; loadMembers()" :disabled="memberPage <= 1" class="px-4 py-2 border rounded disabled:opacity-50">Previous</button>
                            <span class="px-4 py-2" x-text="'Page ' + memberPage"></span>
                            <button @click="memberPage++; loadMembers()" class="px-4 py-2 border rounded bg-blue-500 text-white hover:bg-blue-600">Next</button>
                        </div>
                    </div>
                </div>

                <!-- Disclosures Tab -->
                <div x-show="tab === 'disclosures'" class="p-6">
                    <div class="overflow-x-auto">
                        <table class="w-full">
                            <thead class="bg-gray-50">
                                <tr>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Member</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Year</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Type</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Filing Date</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Status</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y">
                                <template x-for="disc in disclosures" :key="disc.id">
                                    <tr class="hover:bg-gray-50">
                                        <td class="px-4 py-3 font-medium" x-text="disc.member_name || 'Unknown'"></td>
                                        <td class="px-4 py-3" x-text="disc.filing_year"></td>
                                        <td class="px-4 py-3" x-text="disc.filing_type"></td>
                                        <td class="px-4 py-3" x-text="disc.filing_date ? new Date(disc.filing_date).toLocaleDateString() : '-'"></td>
                                        <td class="px-4 py-3">
                                            <span :class="disc.parsed ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'" class="px-2 py-1 rounded text-sm" x-text="disc.parsed ? 'Parsed' : 'Pending'"></span>
                                        </td>
                                    </tr>
                                </template>
                            </tbody>
                        </table>
                    </div>
                    <div class="mt-4 flex justify-between items-center">
                        <span class="text-gray-500 text-sm">Showing <span x-text="disclosures.length"></span> of <span x-text="stats.totalDisclosures"></span></span>
                        <div class="flex gap-2">
                            <button @click="disclosurePage--; loadDisclosures()" :disabled="disclosurePage <= 1" class="px-4 py-2 border rounded disabled:opacity-50">Previous</button>
                            <span class="px-4 py-2" x-text="'Page ' + disclosurePage"></span>
                            <button @click="disclosurePage++; loadDisclosures()" class="px-4 py-2 border rounded bg-blue-500 text-white hover:bg-blue-600">Next</button>
                        </div>
                    </div>
                </div>

                <!-- Stock Trades Tab -->
                <div x-show="tab === 'trades'" class="p-6">
                    <div class="mb-4 p-4 bg-purple-50 rounded-lg">
                        <h3 class="font-medium text-purple-900">📈 Periodic Transaction Reports (PTRs)</h3>
                        <p class="text-purple-700 text-sm mt-1">Stock trades by Congress members. By law, trades over $1,000 must be reported within 45 days.</p>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full">
                            <thead class="bg-gray-50">
                                <tr>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Member</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Year</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Filing Type</th>
                                    <th class="px-4 py-3 text-left text-sm font-medium text-gray-500">Filing Date</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y">
                                <template x-for="trade in trades" :key="trade.id">
                                    <tr class="hover:bg-gray-50">
                                        <td class="px-4 py-3 font-medium" x-text="trade.member_name || 'Unknown'"></td>
                                        <td class="px-4 py-3" x-text="trade.filing_year"></td>
                                        <td class="px-4 py-3">
                                            <span class="bg-purple-100 text-purple-800 px-2 py-1 rounded text-sm" x-text="trade.filing_type"></span>
                                        </td>
                                        <td class="px-4 py-3" x-text="trade.filing_date ? new Date(trade.filing_date).toLocaleDateString() : '-'"></td>
                                    </tr>
                                </template>
                            </tbody>
                        </table>
                    </div>
                    <template x-if="trades.length === 0">
                        <div class="text-center py-12">
                            <div class="text-6xl mb-4">📊</div>
                            <h3 class="text-xl font-medium text-gray-700">No Stock Trades Loaded</h3>
                            <p class="text-gray-500 mt-2">Run: python -m src.cli ingest-trades</p>
                        </div>
                    </template>
                    <div x-show="trades.length > 0" class="mt-4 flex justify-between items-center">
                        <span class="text-gray-500 text-sm">Showing <span x-text="trades.length"></span> of <span x-text="stats.totalPtrs"></span></span>
                        <div class="flex gap-2">
                            <button @click="tradePage--; loadTrades()" :disabled="tradePage <= 1" class="px-4 py-2 border rounded disabled:opacity-50">Previous</button>
                            <span class="px-4 py-2" x-text="'Page ' + tradePage"></span>
                            <button @click="tradePage++; loadTrades()" class="px-4 py-2 border rounded bg-purple-500 text-white hover:bg-purple-600">Next</button>
                        </div>
                    </div>
                </div>

                <!-- Anomalies Tab - ENHANCED -->
                <div x-show="tab === 'anomalies'" class="p-6">
                    <!-- Anomaly Type Legend -->
                    <div class="mb-6 p-4 bg-gray-50 rounded-lg">
                        <h3 class="font-medium text-gray-900 mb-3">🔍 Anomaly Types Explained</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
                            <div class="p-2 bg-white rounded border">
                                <span class="font-medium text-red-700">Wealth vs Salary</span>
                                <p class="text-gray-600 text-xs mt-1">Net worth grew faster than cumulative salary could explain</p>
                            </div>
                            <div class="p-2 bg-white rounded border">
                                <span class="font-medium text-orange-700">Asset Appreciation</span>
                                <p class="text-gray-600 text-xs mt-1">Single asset value increased >100% in one year</p>
                            </div>
                            <div class="p-2 bg-white rounded border">
                                <span class="font-medium text-yellow-700">Stock Outperformance</span>
                                <p class="text-gray-600 text-xs mt-1">Trading returns significantly beat S&P 500 benchmark</p>
                            </div>
                            <div class="p-2 bg-white rounded border">
                                <span class="font-medium text-purple-700">Trade Timing</span>
                                <p class="text-gray-600 text-xs mt-1">Perfect timing, consecutive trades, or volume spikes</p>
                            </div>
                            <div class="p-2 bg-white rounded border">
                                <span class="font-medium text-blue-700">Committee Conflict</span>
                                <p class="text-gray-600 text-xs mt-1">Trading in sectors the member's committee oversees</p>
                            </div>
                            <div class="p-2 bg-white rounded border">
                                <span class="font-medium text-green-700">Loss Avoidance</span>
                                <p class="text-gray-600 text-xs mt-1">Statistically improbable success rate (80%+)</p>
                            </div>
                            <div class="p-2 bg-white rounded border">
                                <span class="font-medium text-pink-700">Multi-Factor Risk</span>
                                <p class="text-gray-600 text-xs mt-1">Member shows 3+ different anomaly patterns</p>
                            </div>
                            <div class="p-2 bg-white rounded border">
                                <span class="font-medium text-gray-700">Late Filing</span>
                                <p class="text-gray-600 text-xs mt-1">Trade reported more than 45 days after transaction</p>
                            </div>
                        </div>
                    </div>

                    <!-- Filters -->
                    <div class="flex gap-4 mb-4">
                        <select x-model="anomalyTypeFilter" @change="loadAnomalies()" class="border rounded-lg px-4 py-2">
                            <option value="">All Types</option>
                            <option value="excessive_wealth_growth">Wealth vs Salary</option>
                            <option value="rapid_asset_appreciation">Asset Appreciation</option>
                            <option value="outperforming_trades">Stock Outperformance</option>
                            <option value="trade_timing">Trade Timing</option>
                            <option value="sector_concentration">Committee Conflict</option>
                            <option value="loss_avoidance">Loss Avoidance</option>
                            <option value="multi_factor_risk">Multi-Factor Risk</option>
                            <option value="late_filing">Late Filing</option>
                        </select>
                        <select x-model="severityFilter" @change="loadAnomalies()" class="border rounded-lg px-4 py-2">
                            <option value="">All Severities</option>
                            <option value="high">High</option>
                            <option value="medium">Medium</option>
                            <option value="low">Low</option>
                        </select>
                    </div>

                    <template x-if="anomalies.length === 0">
                        <div class="text-center py-12">
                            <div class="text-6xl mb-4">✅</div>
                            <h3 class="text-xl font-medium text-gray-700">No Anomalies Match Filters</h3>
                            <p class="text-gray-500 mt-2">Try adjusting the filters above or run anomaly detection.</p>
                            <code class="bg-gray-100 px-2 py-1 rounded text-sm mt-4 inline-block">python -m src.cli analyze</code>
                        </div>
                    </template>
                    
                    <template x-if="anomalies.length > 0">
                        <div class="space-y-4">
                            <template x-for="anomaly in anomalies" :key="anomaly.id">
                                <div :class="{
                                    'border-l-4 border-red-500 bg-red-50': anomaly.severity === 'high', 
                                    'border-l-4 border-orange-500 bg-orange-50': anomaly.severity === 'medium', 
                                    'border-l-4 border-yellow-500 bg-yellow-50': anomaly.severity === 'low'
                                }" class="p-4 rounded-lg shadow-sm">
                                    <!-- Header Row -->
                                    <div class="flex justify-between items-start mb-2">
                                        <div>
                                            <h4 class="font-semibold text-lg" x-text="anomaly.title"></h4>
                                            <div class="flex items-center gap-2 mt-1">
                                                <span class="text-sm font-medium" x-text="anomaly.member_name"></span>
                                                <span :class="{
                                                    'bg-blue-100 text-blue-800': anomaly.member_party === 'D',
                                                    'bg-red-100 text-red-800': anomaly.member_party === 'R',
                                                    'bg-gray-100 text-gray-800': anomaly.member_party === 'I'
                                                }" class="px-1.5 py-0.5 rounded text-xs" x-text="anomaly.member_party"></span>
                                                <span class="text-gray-500 text-sm" x-text="anomaly.member_state"></span>
                                            </div>
                                        </div>
                                        <div class="flex flex-col items-end gap-1">
                                            <span :class="{
                                                'bg-red-600 text-white': anomaly.severity === 'high', 
                                                'bg-orange-500 text-white': anomaly.severity === 'medium', 
                                                'bg-yellow-400 text-gray-800': anomaly.severity === 'low'
                                            }" class="px-2 py-1 rounded text-sm font-medium uppercase" x-text="anomaly.severity"></span>
                                            <span class="text-xs text-gray-500" x-text="getAnomalyTypeName(anomaly.anomaly_type)"></span>
                                        </div>
                                    </div>
                                    
                                    <!-- Description -->
                                    <p class="text-gray-700 mt-2" x-text="anomaly.description"></p>
                                    
                                    <!-- Details Row -->
                                    <div class="mt-3 pt-3 border-t border-gray-200 flex flex-wrap gap-4 text-sm">
                                        <div class="flex items-center gap-1">
                                            <span class="text-gray-500">📅 Detected:</span>
                                            <span class="font-medium" x-text="new Date(anomaly.detected_at).toLocaleDateString()"></span>
                                        </div>
                                        <template x-if="anomaly.computed_value">
                                            <div class="flex items-center gap-1">
                                                <span class="text-gray-500">📊 Value:</span>
                                                <span class="font-medium" x-text="formatValue(anomaly.computed_value, anomaly.anomaly_type)"></span>
                                            </div>
                                        </template>
                                        <template x-if="anomaly.threshold_value">
                                            <div class="flex items-center gap-1">
                                                <span class="text-gray-500">⚠️ Threshold:</span>
                                                <span class="font-medium" x-text="formatValue(anomaly.threshold_value, anomaly.anomaly_type)"></span>
                                            </div>
                                        </template>
                                        <div class="flex items-center gap-1">
                                            <span class="text-gray-500">🏛️ Source:</span>
                                            <span class="font-medium" x-text="getSourceFromType(anomaly.anomaly_type)"></span>
                                        </div>
                                    </div>
                                    
                                    <!-- What This Means -->
                                    <div class="mt-3 p-2 bg-white bg-opacity-50 rounded text-sm">
                                        <span class="font-medium text-gray-700">💡 What this means: </span>
                                        <span class="text-gray-600" x-text="getAnomalyExplanation(anomaly.anomaly_type)"></span>
                                    </div>
                                </div>
                            </template>
                        </div>
                    </template>
                    
                    <div x-show="anomalies.length > 0" class="mt-4 flex justify-between items-center">
                        <span class="text-gray-500 text-sm">Showing <span x-text="anomalies.length"></span> of <span x-text="stats.totalAnomalies"></span> anomalies</span>
                        <div class="flex gap-2">
                            <button @click="anomalyPage--; loadAnomalies()" :disabled="anomalyPage <= 1" class="px-4 py-2 border rounded disabled:opacity-50">Previous</button>
                            <span class="px-4 py-2" x-text="'Page ' + anomalyPage"></span>
                            <button @click="anomalyPage++; loadAnomalies()" class="px-4 py-2 border rounded bg-red-500 text-white hover:bg-red-600">Next</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Footer -->
        <footer class="bg-gray-800 text-gray-400 py-8 mt-12">
            <div class="container mx-auto px-4 text-center">
                <p>Honest Congress - Congressional Financial Disclosure Analyzer</p>
                <p class="text-sm mt-2">Data: House Clerk, QuiverQuant API, congress-legislators</p>
                <p class="text-sm mt-2"><a href="/docs" class="text-blue-400 hover:underline">API Documentation</a></p>
            </div>
        </footer>
    </div>

    <script>
        function dashboard() {
            return {
                tab: 'members',
                stats: { totalMembers: 0, totalDisclosures: 0, totalPtrs: 0, totalAnomalies: 0, highSeverity: 0 },
                members: [], disclosures: [], trades: [], anomalies: [],
                memberSearch: '', partyFilter: '', chamberFilter: '',
                anomalyTypeFilter: '', severityFilter: '',
                memberPage: 1, disclosurePage: 1, tradePage: 1, anomalyPage: 1,

                async init() {
                    await Promise.all([this.loadStats(), this.loadMembers(), this.loadDisclosures(), this.loadAnomalies()]);
                },

                async loadStats() {
                    try {
                        const [m, d, p, a] = await Promise.all([
                            fetch('/api/members?page_size=1').then(r => r.json()),
                            fetch('/api/disclosures?page_size=1&is_ptr=false').then(r => r.json()),
                            fetch('/api/disclosures?page_size=1&is_ptr=true').then(r => r.json()),
                            fetch('/api/anomalies/summary').then(r => r.json())
                        ]);
                        this.stats = { 
                            totalMembers: m.total || 0, 
                            totalDisclosures: d.total || 0, 
                            totalPtrs: p.total || 0,
                            totalAnomalies: a.total_anomalies || 0, 
                            highSeverity: a.by_severity?.high || 0 
                        };
                    } catch (e) { console.error('Stats error:', e); }
                },

                async loadMembers() {
                    try {
                        let url = `/api/members?page=${this.memberPage}&page_size=20`;
                        if (this.partyFilter) url += `&party=${this.partyFilter}`;
                        if (this.chamberFilter) url += `&chamber=${this.chamberFilter}`;
                        if (this.memberSearch) url += `&search=${encodeURIComponent(this.memberSearch)}`;
                        const data = await fetch(url).then(r => r.json());
                        this.members = data.members || [];
                    } catch (e) { console.error('Members error:', e); }
                },

                async loadDisclosures() {
                    try {
                        const data = await fetch(`/api/disclosures?page=${this.disclosurePage}&page_size=20&is_ptr=false`).then(r => r.json());
                        this.disclosures = data.disclosures || [];
                    } catch (e) { console.error('Disclosures error:', e); }
                },

                async loadTrades() {
                    try {
                        const data = await fetch(`/api/disclosures?page=${this.tradePage}&page_size=20&is_ptr=true`).then(r => r.json());
                        this.trades = data.disclosures || [];
                    } catch (e) { console.error('Trades error:', e); }
                },

                async loadAnomalies() {
                    try {
                        let url = `/api/anomalies?page=${this.anomalyPage}&page_size=20`;
                        if (this.anomalyTypeFilter) url += `&anomaly_type=${this.anomalyTypeFilter}`;
                        if (this.severityFilter) url += `&severity=${this.severityFilter}`;
                        const data = await fetch(url).then(r => r.json());
                        this.anomalies = data.anomalies || [];
                    } catch (e) { console.error('Anomalies error:', e); }
                },

                getAnomalyTypeName(type) {
                    const typeNames = {
                        'excessive_wealth_growth': 'Wealth vs Salary',
                        'rapid_asset_appreciation': 'Asset Appreciation',
                        'outperforming_trades': 'Stock Outperformance',
                        'trade_timing': 'Trade Timing',
                        'trade_clustering': 'Trade Clustering',
                        'perfect_timing': 'Perfect Timing',
                        'volume_spikes': 'Volume Spikes',
                        'sector_concentration': 'Committee Conflict',
                        'loss_avoidance': 'Loss Avoidance',
                        'multi_factor_risk': 'Multi-Factor Risk',
                        'late_filing': 'Late Filing',
                        'high_frequency': 'High Frequency',
                        'large_trade': 'Large Trade'
                    };
                    return typeNames[type] || type;
                },

                getAnomalyExplanation(type) {
                    const explanations = {
                        'excessive_wealth_growth': 'This member\\'s net worth increased significantly more than their Congressional salary could explain. May indicate undisclosed income sources or gains.',
                        'rapid_asset_appreciation': 'A single asset (business, property, or investment) grew in value by more than 100% in one year, which is unusual and warrants investigation.',
                        'outperforming_trades': 'This member\\'s stock trading returns significantly beat the S&P 500 benchmark, suggesting possible insider information or exceptional timing.',
                        'trade_timing': 'The member demonstrated unusually good timing on trades, such as buying before stock rises or selling before drops.',
                        'trade_clustering': 'Multiple consecutive trades in the same direction (all buys or all sells) suggests coordinated trading strategy.',
                        'perfect_timing': 'The member has an abnormally high success rate on trades (80%+), which is statistically improbable without insider information.',
                        'volume_spikes': 'Unusual trading volume detected - trade size significantly higher than the member\\'s typical trading pattern.',
                        'sector_concentration': 'The member is heavily trading in sectors that their Congressional committee oversees, creating potential conflict of interest.',
                        'loss_avoidance': 'This member almost never loses money on trades (80%+ win rate), which is statistically improbable and suggests insider knowledge.',
                        'multi_factor_risk': 'This member shows 3 or more different anomaly patterns, making them a high-priority investigation target.',
                        'late_filing': 'The member reported this trade more than 45 days after it occurred, violating the STOCK Act disclosure requirements.',
                        'high_frequency': 'This member trades much more frequently than typical members, warranting additional scrutiny.',
                        'large_trade': 'A single trade that is unusually large compared to typical Congressional member trading.'
                    };
                    return explanations[type] || 'This anomaly requires further investigation to understand its significance.';
                },

                getSourceFromType(type) {
                    const sources = {
                        'excessive_wealth_growth': 'Annual Financial Disclosure (FD)',
                        'rapid_asset_appreciation': 'Annual Financial Disclosure (FD)',
                        'outperforming_trades': 'Stock Trades (PTR)',
                        'trade_timing': 'Stock Trades (PTR)',
                        'trade_clustering': 'Stock Trades (PTR)',
                        'perfect_timing': 'Stock Trades (PTR)',
                        'volume_spikes': 'Stock Trades (PTR)',
                        'sector_concentration': 'Stock Trades (PTR)',
                        'loss_avoidance': 'Stock Trades (PTR)',
                        'multi_factor_risk': 'Combined Analysis',
                        'late_filing': 'Stock Trades (PTR)',
                        'high_frequency': 'Stock Trades (PTR)',
                        'large_trade': 'Stock Trades (PTR)'
                    };
                    return sources[type] || 'Financial Disclosures';
                },

                formatValue(value, type) {
                    if (!value) return '-';
                    if (type.includes('growth') || type.includes('appreciation') || type.includes('outperforming')) {
                        return value.toFixed(1) + '%';
                    }
                    if (value > 1000000) {
                        return '$' + (value / 1000000).toFixed(1) + 'M';
                    }
                    if (value > 1000) {
                        return '$' + (value / 1000).toFixed(0) + 'K';
                    }
                    return value.toFixed(2);
                }
            }
        }
    </script>
</body>
</html>
"""

@router.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the enhanced dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)

