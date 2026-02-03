"""Enhanced web dashboard for Honest Congress - Anomaly-focused design."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <meta name="version" content="2.1.0-20260201">
    <title>Honest Congress - Congressional Financial Anomaly Tracker</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
    <script>
        // Define dashboard function BEFORE Alpine loads so it's available when x-data="dashboard()" is evaluated
        window.dashboard = function() {
            return {
                stats: { totalMembers: 0, totalDisclosures: 0, parsedDisclosures: 0, totalTrades: 0, totalAnomalies: 0 },
                anomalyStats: { critical: 0, high: 0, medium: 0, low: 0 },
                allAnomalies: [],
                filteredAnomalies: [],
                membersWithAnomalies: [],
                memberAnomalies: [],
                selectedMember: null,
                showMemberModal: false,
                searchQuery: '',
                filterTypes: {
                    excessive_wealth_growth: true,
                    high_trading_frequency: true,
                    large_trade: true,
                    sector_concentration: true,
                    late_filing: true
                },
                filterSeverities: {
                    critical: true,
                    high: true,
                    medium: true,
                    low: true
                },
                filterParties: {
                    D: true,
                    R: true,
                    I: true
                },
                currentPage: 1,
                pageSize: 20,
                get totalPages() {
                    return Math.max(1, Math.ceil(this.filteredAnomalies.length / this.pageSize));
                },
                get paginatedAnomalies() {
                    const start = (this.currentPage - 1) * this.pageSize;
                    return this.filteredAnomalies.slice(start, start + this.pageSize);
                },
                showDataExplorer: false,
                explorerTab: 'members',
                explorerPage: 1,
                explorerSearch: '',
                explorerParty: '',
                explorerMembers: [],
                explorerDisclosures: [],
                explorerTrades: [],
                loadingMembers: true,
                loadingAnomalies: true,
                showAnomalyModal: false,
                selectedAnomaly: null,
                anomalyDocuments: [],
                statModalOpen: false,
                statModalType: '',
                statModalTitle: '',
                statModalSubtitle: '',
                statModalData: [],
                statModalPage: 1,
                statSearchQuery: '',
                statPartyFilter: '',
                showMaintenancePanel: false,
                adminPassword: '',
                adminToken: null,
                isAdmin: false,
                adminStatus: '',
                
                // All methods defined below
                init: async function() {
                    console.log('Initializing dashboard...');
                    try {
                        await this.loadStats();
                        await this.loadAnomalies();
                        await this.loadFlaggedMembers();
                        console.log('Init complete. Anomalies:', this.allAnomalies.length, 'Flagged Members:', this.membersWithAnomalies.length);
                    } catch (e) {
                        console.error('Init error:', e);
                    }
                },
                
                loadStats: async function() {
                    try {
                        const [members, disclosures, parsed, trades, anomalies] = await Promise.all([
                            fetch('/api/members?page_size=1').then(r => r.json()),
                            fetch('/api/disclosures?page_size=1').then(r => r.json()),
                            fetch('/api/disclosures?page_size=1&parsed=true').then(r => r.json()),
                            fetch('/api/disclosures?page_size=1&is_ptr=true').then(r => r.json()),
                            fetch('/api/anomalies/summary').then(r => r.json())
                        ]);
                        
                        this.stats = {
                            totalMembers: members.total || 0,
                            totalDisclosures: disclosures.total || 0,
                            parsedDisclosures: parsed.total || 0,
                            totalTrades: trades.total || 0,
                            totalAnomalies: anomalies.total_anomalies || 0
                        };
                        
                        const bySeverity = anomalies.by_severity || {};
                        this.anomalyStats = {
                            critical: (bySeverity['10'] || 0) + (bySeverity['9'] || 0),
                            high: (bySeverity['8'] || 0) + (bySeverity['7'] || 0) + (bySeverity['high'] || 0),
                            medium: (bySeverity['6'] || 0) + (bySeverity['5'] || 0) + (bySeverity['4'] || 0) + (bySeverity['medium'] || 0),
                            low: (bySeverity['3'] || 0) + (bySeverity['2'] || 0) + (bySeverity['1'] || 0) + (bySeverity['low'] || 0)
                        };
                    } catch (e) {
                        console.error('Error loading stats:', e);
                    }
                },
                
                async loadFlaggedMembers() {
                    this.loadingMembers = true;
                    try {
                        const data = await fetch('/api/members?has_anomalies=true&page_size=100').then(r => r.json());
                        const members = data.members || [];
                        this.membersWithAnomalies = members.map(m => ({
                            id: m.id, name: m.first_name + ' ' + m.last_name, party: m.party, state: m.state,
                            anomaly_count: m.anomaly_count || 0, anomalies: []
                        })).filter(m => m.anomaly_count > 0).sort((a, b) => b.anomaly_count - a.anomaly_count);
                    } catch (e) {
                        console.error('Error loading flagged members:', e);
                    } finally {
                        this.loadingMembers = false;
                    }
                },
                
                async loadAnomalies() {
                    this.loadingAnomalies = true;
                    try {
                        let allAnomalies = [];
                        let page = 1;
                        while (page <= 10) {
                            const data = await fetch(`/api/anomalies?page=${page}&page_size=100`).then(r => r.json());
                            const anomalies = data.anomalies || [];
                            allAnomalies = allAnomalies.concat(anomalies);
                            if (anomalies.length < 100) break;
                            page++;
                        }
                        this.allAnomalies = allAnomalies;
                        const memberMap = {};
                        this.allAnomalies.forEach(a => {
                            if (!memberMap[a.member_id]) memberMap[a.member_id] = [];
                            memberMap[a.member_id].push(a);
                        });
                        this.membersWithAnomalies.forEach(m => { m.anomalies = memberMap[m.id] || []; });
                        this.filterAnomalies();
                    } catch (e) {
                        console.error('Error loading anomalies:', e);
                    } finally {
                        this.loadingAnomalies = false;
                    }
                },
                
                filterAnomalies() {
                    let filtered = [...this.allAnomalies];
                    if (this.searchQuery) {
                        const q = this.searchQuery.toLowerCase();
                        filtered = filtered.filter(a => a.member_name?.toLowerCase().includes(q) || a.title?.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q));
                    }
                    const activeTypes = Object.entries(this.filterTypes).filter(([,v]) => v).map(([k]) => k);
                    if (activeTypes.length < Object.keys(this.filterTypes).length) {
                        filtered = filtered.filter(a => activeTypes.includes(a.anomaly_type));
                    }
                    const activeSeverities = Object.entries(this.filterSeverities).filter(([,v]) => v).map(([k]) => k);
                    if (activeSeverities.length < Object.keys(this.filterSeverities).length) {
                        filtered = filtered.filter(a => {
                            const score = this.getSeverityScore(a.severity);
                            if (activeSeverities.includes('critical') && score >= 9) return true;
                            if (activeSeverities.includes('high') && score >= 7 && score < 9) return true;
                            if (activeSeverities.includes('medium') && score >= 4 && score < 7) return true;
                            if (activeSeverities.includes('low') && score < 4) return true;
                            return false;
                        });
                    }
                    const activeParties = Object.entries(this.filterParties).filter(([,v]) => v).map(([k]) => k);
                    if (activeParties.length < Object.keys(this.filterParties).length) {
                        filtered = filtered.filter(a => activeParties.includes(a.member_party));
                    }
                    filtered.sort((a, b) => this.getSeverityScore(b.severity) - this.getSeverityScore(a.severity));
                    this.filteredAnomalies = filtered;
                    if (this.currentPage > this.totalPages) this.currentPage = this.totalPages;
                    if (this.currentPage < 1) this.currentPage = 1;
                },
                
                selectMember(member) {
                    this.selectedMember = member;
                    this.memberAnomalies = member.anomalies || [];
                    this.showMemberModal = true;
                },
                
                showAnomalyDetail(anomaly) {
                    this.selectedAnomaly = anomaly;
                    this.showAnomalyModal = true;
                    this.anomalyDocuments = [];
                },
                
                showStatModal(type) {
                    this.statModalType = type;
                    this.statModalPage = 1;
                    this.statModalOpen = true;
                },
                
                clearFilters() {
                    this.searchQuery = '';
                    this.filterTypes = { excessive_wealth_growth: true, high_trading_frequency: true, large_trade: true, sector_concentration: true, late_filing: true };
                    this.filterSeverities = { critical: true, high: true, medium: true, low: true };
                    this.filterParties = { D: true, R: true, I: true };
                    this.filterAnomalies();
                },
                
                getSeverityScore(severity) {
                    if (typeof severity === 'number') return severity;
                    const num = parseInt(severity);
                    if (!isNaN(num)) return num;
                    if (severity === 'high') return 8;
                    if (severity === 'medium') return 5;
                    if (severity === 'low') return 2;
                    return 5;
                },
                
                getSeverityClass(severity) {
                    const score = this.getSeverityScore(severity);
                    if (score >= 9) return 'severity-critical';
                    if (score >= 7) return 'severity-high';
                    if (score >= 4) return 'severity-medium';
                    return 'severity-low';
                },
                
                getSeverityBadgeClass(severity) {
                    const score = this.getSeverityScore(severity);
                    if (score >= 9) return 'bg-red-600';
                    if (score >= 7) return 'bg-orange-600';
                    if (score >= 4) return 'bg-yellow-600';
                    return 'bg-green-600';
                },
                
                getSeverityLabel(severity) {
                    const score = this.getSeverityScore(severity);
                    if (score >= 9) return 'CRITICAL';
                    if (score >= 7) return 'HIGH';
                    if (score >= 4) return 'MEDIUM';
                    return 'LOW';
                },
                
                getTypeIcon(type) {
                    const icons = { excessive_wealth_growth: '💰', high_trading_frequency: '📈', large_trade: '💎', sector_concentration: '🏭', late_filing: '⏰' };
                    return icons[type] || '⚠️';
                },
                
                getTypeName(type) {
                    const names = { excessive_wealth_growth: 'Wealth Growth', high_trading_frequency: 'High Frequency Trading', large_trade: 'Large Trade', sector_concentration: 'Sector Concentration', late_filing: 'Late Filing' };
                    return names[type] || type;
                },
                
                getExplanation(type) {
                    return 'Anomaly detected. See details for more information.';
                },
                
                getThresholdSource(type) {
                    return 'Internal or regulatory threshold';
                },
                
                isQuiverQuantDoc(doc) {
                    if (!doc || !doc.document_id) return false;
                    return String(doc.document_id).includes('QANT');
                },
                
                getFilingTypeLabel(filingType) {
                    const labels = { FD: 'Annual Financial Disclosure', PTR: 'Periodic Transaction Report', T: 'PTR' };
                    return labels[filingType] || 'Filing';
                },
                
                formatDate(dateStr) {
                    if (!dateStr) return '-';
                    const date = new Date(dateStr);
                    return isNaN(date.getTime()) ? '-' : date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
                },
                
                formatTextWithDates(text) {
                    if (!text) return '';
                    return text.replace(/(\\d{4})-(\\d{2})(?!\\d)/g, (match, year, month) => {
                        const monthNum = parseInt(month, 10);
                        if (monthNum < 1 || monthNum > 12) return match;
                        const date = new Date(parseInt(year, 10), monthNum - 1, 1);
                        return date.toLocaleDateString('en-US', { year: 'numeric', month: 'long' });
                    });
                },
                
                formatValue(value) {
                    if (!value) return '-';
                    if (value >= 1000000) return '$' + (value / 1000000).toFixed(1) + 'M';
                    if (value >= 1000) return '$' + (value / 1000).toFixed(0) + 'K';
                    return value.toFixed(0);
                },
                
                async adminLogin() {
                    this.adminStatus = 'Authenticating...';
                    try {
                        const result = await fetch('/api/anomalies/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: this.adminPassword }) }).then(r => r.json());
                        if (result.token) {
                            this.adminToken = result.token;
                            this.isAdmin = true;
                            this.adminPassword = '';
                            this.adminStatus = 'Authenticated.';
                        } else {
                            this.adminStatus = result.detail || 'Login failed.';
                        }
                    } catch (e) { this.adminStatus = 'Login failed.'; }
                },
                
                async adminLogout() {
                    this.adminToken = null;
                    this.isAdmin = false;
                    this.adminStatus = 'Logged out.';
                },
                
                async adminCleanup() {
                    this.adminStatus = 'Cleanup...';
                    try {
                        const result = await fetch('/api/anomalies/cleanup', { method: 'POST', headers: { 'X-Admin-Token': this.adminToken } }).then(r => r.json());
                        this.adminStatus = result.message || 'Cleanup complete.';
                        await this.loadAnomalies();
                    } catch (e) { this.adminStatus = 'Error'; }
                },
                
                async adminRunAnalysis() {
                    this.adminStatus = 'Analyzing...';
                    try {
                        const result = await fetch('/api/anomalies/analyze', { method: 'POST', headers: { 'X-Admin-Token': this.adminToken } }).then(r => r.json());
                        this.adminStatus = result.message || 'Complete.';
                        await this.loadAnomalies();
                    } catch (e) { this.adminStatus = 'Error'; }
                },
                
                async adminRegenerate() {
                    this.adminStatus = 'Regenerating...';
                    try {
                        const result = await fetch('/api/anomalies/regenerate', { method: 'POST', headers: { 'X-Admin-Token': this.adminToken } }).then(r => r.json());
                        this.adminStatus = result.message || 'Complete.';
                        await this.loadAnomalies();
                    } catch (e) { this.adminStatus = 'Error'; }
                },
                
                async loadExplorerMembers() {
                    let url = `/api/members?page=${this.explorerPage}&page_size=15`;
                    if (this.explorerSearch) url += `&search=${encodeURIComponent(this.explorerSearch)}`;
                    const data = await fetch(url).then(r => r.json());
                    this.explorerMembers = data.members || [];
                },
                
                async loadExplorerDisclosures() {
                    const data = await fetch(`/api/disclosures?page=${this.explorerPage}&page_size=15`).then(r => r.json());
                    this.explorerDisclosures = data.disclosures || [];
                },
                
                async loadExplorerTrades() {
                    const data = await fetch(`/api/disclosures?page=${this.explorerPage}&page_size=15&is_ptr=true`).then(r => r.json());
                    this.explorerTrades = data.disclosures || [];
                },
                
                prevPage() { if (this.currentPage > 1) this.currentPage--; },
                nextPage() { if (this.currentPage < this.totalPages) this.currentPage++; }
            };
        };
    </script>
    <style>
        [x-cloak] { display: none !important; }
        .severity-critical { background: linear-gradient(135deg, #dc2626 0%, #991b1b 100%); }
        .severity-high { background: linear-gradient(135deg, #ea580c 0%, #c2410c 100%); }
        .severity-medium { background: linear-gradient(135deg, #d97706 0%, #b45309 100%); }
        .severity-low { background: linear-gradient(135deg, #65a30d 0%, #4d7c0f 100%); }
        .glass-card { backdrop-filter: blur(10px); background: rgba(255,255,255,0.95); }
        .member-card:hover { transform: translateY(-2px); }
        .pulse-dot { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .stat-card { cursor: pointer; transition: all 0.2s; }
        .stat-card:hover { transform: translateY(-3px); box-shadow: 0 10px 25px rgba(0,0,0,0.2); }
        .checkbox-filter { user-select: none; }
        .checkbox-filter input[type="checkbox"] { width: 18px; height: 18px; cursor: pointer; }
    </style>
</head>
<body class="bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 min-h-screen">
    <div x-data="dashboard()" x-init="init()" x-cloak>
        <!-- Debug Banner -->
        <div style="background:#333;color:#fff;padding:10px;font-family:monospace;font-size:12px;display:none" id="debugBanner" x-show="false">
            <strong>DEBUG:</strong> <span id="debugText">Loading...</span>
        </div>
        
        <!-- Header -->
        <header class="bg-black/30 backdrop-blur-md border-b border-white/10">
            <div class="container mx-auto px-4 py-4">
                <div class="flex justify-between items-center">
                    <div class="flex items-center gap-3">
                        <span class="text-4xl">🏛️</span>
                        <div>
                            <h1 class="text-2xl font-bold text-white">Honest Congress</h1>
                            <p class="text-blue-300 text-sm">Congressional Financial Anomaly Tracker</p>
                        </div>
                    </div>
                    <div class="flex gap-3">
                        <button x-show="isAdmin" x-cloak @click="cleanupAnomalies()" class="bg-orange-600 hover:bg-orange-500 text-white px-4 py-2 rounded-lg flex items-center gap-2 transition-all" title="Remove anomalies where value equals threshold">
                            🧹 Cleanup
                        </button>
                        <button x-show="isAdmin" x-cloak @click="runAnalysis()" class="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg flex items-center gap-2 transition-all" title="Re-runs anomaly detection algorithms on all data">
                            <span class="pulse-dot">●</span> Run Analysis
                        </button>
                        <button @click="showMaintenancePanel = true" class="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg transition-all">
                            🛠️ Maintenance
                        </button>
                        <a href="/docs" class="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg transition-all">API Docs</a>
                    </div>
                </div>
            </div>
        </header>

        <!-- Maintenance Panel -->
        <div x-show="showMaintenancePanel" x-cloak class="fixed inset-0 bg-black/60 flex items-center justify-center z-50" @click.self="showMaintenancePanel = false">
            <div class="glass-card rounded-2xl shadow-xl w-full max-w-lg p-6 relative" @click.stop>
                <button @click="showMaintenancePanel = false" class="absolute top-3 right-3 text-gray-500 hover:text-gray-700 text-xl">&times;</button>
                <h2 class="text-xl font-bold text-gray-800 mb-4">🛠️ Maintenance</h2>

                <div x-show="!isAdmin" class="bg-gray-50 border border-gray-200 rounded-lg p-4">
                    <div class="font-semibold text-gray-700 mb-2">Admin Login</div>
                    <div class="flex gap-2">
                        <input type="password" x-model="adminPassword" @keyup.enter="adminLogin()" placeholder="Admin password" class="flex-1 px-3 py-2 border border-gray-300 rounded-lg">
                        <button @click="adminLogin()" class="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg">Login</button>
                    </div>
                    <div class="text-xs text-gray-500 mt-2" x-text="adminStatus"></div>
                </div>

                <div x-show="isAdmin" class="space-y-3">
                    <div class="flex items-center justify-between">
                        <div class="text-sm text-green-700 font-semibold">✓ Admin authenticated</div>
                        <button @click="adminLogout()" class="text-sm text-red-600 hover:text-red-700">Logout</button>
                    </div>
                    <button @click="adminCleanup()" class="w-full bg-orange-600 hover:bg-orange-500 text-white px-4 py-2 rounded-lg">🧹 Cleanup Invalid Anomalies</button>
                    <button @click="adminRunAnalysis()" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg">🔍 Run Analysis</button>
                    <button @click="adminRegenerate()" class="w-full bg-red-700 hover:bg-red-600 text-white px-4 py-2 rounded-lg">🔄 Regenerate All Anomalies</button>
                    <div class="text-xs text-gray-500 mt-2" x-text="adminStatus"></div>
                </div>
            </div>
        </div>

        <!-- Stats Bar -->
        <div class="container mx-auto px-4 py-6">
            <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
                <div @click="showStatModal('members')" class="glass-card rounded-xl p-4 shadow-lg stat-card">
                    <div class="text-gray-500 text-xs font-medium uppercase tracking-wider">Members</div>
                    <div class="text-3xl font-bold text-slate-800" x-text="stats.totalMembers">-</div>
                    <div class="text-xs text-blue-500 mt-1">Click for details</div>
                </div>
                <div @click="showStatModal('disclosures')" class="glass-card rounded-xl p-4 shadow-lg stat-card">
                    <div class="text-gray-500 text-xs font-medium uppercase tracking-wider">Disclosures</div>
                    <div class="text-3xl font-bold text-blue-600" x-text="stats.totalDisclosures">-</div>
                    <div class="text-xs text-blue-500 mt-1">Click for details</div>
                </div>
                <div @click="showStatModal('parsed')" class="glass-card rounded-xl p-4 shadow-lg stat-card">
                    <div class="text-gray-500 text-xs font-medium uppercase tracking-wider">Parsed</div>
                    <div class="text-3xl font-bold text-teal-600" x-text="stats.parsedDisclosures">-</div>
                    <div class="text-xs text-blue-500 mt-1">Click for details</div>
                </div>
                <div @click="showStatModal('trades')" class="glass-card rounded-xl p-4 shadow-lg stat-card">
                    <div class="text-gray-500 text-xs font-medium uppercase tracking-wider">Stock Trades</div>
                    <div class="text-3xl font-bold text-purple-600" x-text="stats.totalTrades">-</div>
                    <div class="text-xs text-blue-500 mt-1">Click for details</div>
                </div>
                <div @click="showStatModal('anomalies')" class="glass-card rounded-xl p-4 shadow-lg stat-card border-2 border-red-400">
                    <div class="text-red-500 text-xs font-medium uppercase tracking-wider">Anomalies</div>
                    <div class="text-3xl font-bold text-red-600" x-text="stats.totalAnomalies">-</div>
                    <div class="text-xs text-red-500 mt-1">Click for details</div>
                </div>
            </div>
        </div>

        <!-- Main Content Area -->
        <div class="container mx-auto px-4 pb-8">
            <div class="grid grid-cols-1 lg:grid-cols-4 gap-6">
                
                <!-- Left Sidebar - Members with Anomalies -->
                <div class="lg:col-span-1">
                    <div class="glass-card rounded-xl shadow-lg overflow-hidden sticky top-4">
                        <div class="bg-gradient-to-r from-red-600 to-orange-600 text-white p-4">
                            <h2 class="font-bold text-lg">🚨 Flagged Members</h2>
                            <p class="text-red-100 text-sm" x-text="membersWithAnomalies.length + ' members with anomalies'"></p>
                        </div>
                        <div class="p-2 max-h-[500px] overflow-y-auto bg-white">
                            <template x-if="loadingMembers">
                                <div class="text-center py-8 text-gray-500">
                                    <div class="animate-spin inline-block w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full mb-2"></div>
                                    <p>Loading flagged members...</p>
                                </div>
                            </template>
                            <template x-if="!loadingMembers && membersWithAnomalies.length > 0">
                                <div>
                                    <template x-for="member in membersWithAnomalies" :key="member.id">
                                        <div @click="selectMember(member)" 
                                             :class="selectedMember?.id === member.id ? 'bg-blue-100 border-blue-500' : 'bg-gray-50 hover:bg-gray-100 border-gray-200'"
                                             class="member-card p-3 rounded-lg border-2 cursor-pointer transition-all mb-2">
                                            <div class="flex justify-between items-start">
                                                <div>
                                                    <div class="font-semibold text-gray-800" x-text="member.name"></div>
                                                    <div class="text-xs text-gray-500" x-text="member.state"></div>
                                                </div>
                                                <div class="flex flex-col items-end">
                                                    <span :class="{
                                                        'bg-blue-500': member.party === 'D',
                                                        'bg-red-500': member.party === 'R',
                                                        'bg-gray-500': member.party === 'I'
                                                    }" class="text-white text-xs px-2 py-0.5 rounded" x-text="member.party"></span>
                                                    <span class="text-red-600 font-bold text-sm mt-1" x-text="member.anomaly_count + ' flags'"></span>
                                                </div>
                                            </div>
                                        </div>
                                    </template>
                                </div>
                            </template>
                            <template x-if="!loadingMembers && membersWithAnomalies.length === 0">
                                <div class="text-center py-8 text-gray-500">
                                    No flagged members found
                                </div>
                            </template>
                        </div>
                    </div>
                </div>

                <!-- Main Content - Anomalies -->
                <div class="lg:col-span-3">
                    <!-- Severity Legend -->
                    <div class="glass-card rounded-xl shadow-lg p-4 mb-4">
                        <h3 class="font-bold text-gray-700 mb-3">📊 Severity Grades Explained</h3>
                        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                            <div class="flex items-center gap-2 p-2 bg-red-50 rounded-lg border border-red-200">
                                <div class="w-4 h-4 rounded bg-gradient-to-r from-red-600 to-red-800"></div>
                                <div>
                                    <div class="font-bold text-red-700">CRITICAL</div>
                                    <div class="text-xs text-red-600">Very large amounts or extreme patterns that stand out significantly.</div>
                                </div>
                            </div>
                            <div class="flex items-center gap-2 p-2 bg-orange-50 rounded-lg border border-orange-200">
                                <div class="w-4 h-4 rounded bg-gradient-to-r from-orange-500 to-orange-700"></div>
                                <div>
                                    <div class="font-bold text-orange-700">HIGH</div>
                                    <div class="text-xs text-orange-600">Notable deviations from typical Congressional member activity.</div>
                                </div>
                            </div>
                            <div class="flex items-center gap-2 p-2 bg-yellow-50 rounded-lg border border-yellow-200">
                                <div class="w-4 h-4 rounded bg-gradient-to-r from-yellow-500 to-yellow-700"></div>
                                <div>
                                    <div class="font-bold text-yellow-700">MEDIUM</div>
                                    <div class="text-xs text-yellow-600">Patterns that exceed normal thresholds but are less extreme.</div>
                                </div>
                            </div>
                            <div class="flex items-center gap-2 p-2 bg-green-50 rounded-lg border border-green-200">
                                <div class="w-4 h-4 rounded bg-gradient-to-r from-green-500 to-green-700"></div>
                                <div>
                                    <div class="font-bold text-green-700">LOW</div>
                                    <div class="text-xs text-green-600">Minor flags. Noted for tracking patterns over time.</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Filter Bar -->
                    <div class="glass-card rounded-xl shadow-lg p-4 mb-4">
                        <div class="flex flex-wrap gap-4 items-start">
                            <div class="flex-1 min-w-[200px]">
                                <input type="text" x-model="searchQuery" @input.debounce.300ms="filterAnomalies()"
                                       placeholder="Search member, ticker, description..."
                                       class="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                            </div>
                            <button @click="clearFilters()" class="px-4 py-2 bg-gray-200 hover:bg-gray-300 rounded-lg">Clear All</button>
                        </div>
                        
                        <!-- Checkbox Filters -->
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4 pt-4 border-t border-gray-200">
                            <!-- Anomaly Types -->
                            <div>
                                <div class="text-xs font-bold text-gray-600 uppercase mb-2">Anomaly Types</div>
                                <div class="space-y-1">
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterTypes.excessive_wealth_growth" @change="filterAnomalies()" class="rounded text-blue-500">
                                        <span>💰 Wealth Growth</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterTypes.high_trading_frequency" @change="filterAnomalies()" class="rounded text-blue-500">
                                        <span>📈 High Frequency Trading</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterTypes.large_trade" @change="filterAnomalies()" class="rounded text-blue-500">
                                        <span>💎 Large Trade</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterTypes.sector_concentration" @change="filterAnomalies()" class="rounded text-blue-500">
                                        <span>🏭 Sector Concentration</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterTypes.late_filing" @change="filterAnomalies()" class="rounded text-blue-500">
                                        <span>⏰ Late Filing</span>
                                    </label>
                                </div>
                            </div>
                            
                            <!-- Severity Levels -->
                            <div>
                                <div class="text-xs font-bold text-gray-600 uppercase mb-2">Severity Levels</div>
                                <div class="space-y-1">
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterSeverities.critical" @change="filterAnomalies()" class="rounded text-red-500">
                                        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded bg-red-600"></span> Critical</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterSeverities.high" @change="filterAnomalies()" class="rounded text-orange-500">
                                        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded bg-orange-500"></span> High</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterSeverities.medium" @change="filterAnomalies()" class="rounded text-yellow-500">
                                        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded bg-yellow-500"></span> Medium</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterSeverities.low" @change="filterAnomalies()" class="rounded text-green-500">
                                        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded bg-green-500"></span> Low</span>
                                    </label>
                                </div>
                            </div>
                            
                            <!-- Party -->
                            <div>
                                <div class="text-xs font-bold text-gray-600 uppercase mb-2">Party</div>
                                <div class="space-y-1">
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterParties.D" @change="filterAnomalies()" class="rounded text-blue-500">
                                        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded bg-blue-500"></span> Democrat</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterParties.R" @change="filterAnomalies()" class="rounded text-red-500">
                                        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded bg-red-500"></span> Republican</span>
                                    </label>
                                    <label class="checkbox-filter flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 p-1 rounded">
                                        <input type="checkbox" x-model="filterParties.I" @change="filterAnomalies()" class="rounded text-gray-500">
                                        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded bg-gray-500"></span> Independent</span>
                                    </label>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Stats Row -->
                    <div class="grid grid-cols-4 gap-3 mb-4">
                        <div class="glass-card rounded-xl p-3 text-center">
                            <div class="text-2xl font-bold text-red-600" x-text="anomalyStats.critical || 0"></div>
                            <div class="text-xs text-gray-500">CRITICAL</div>
                        </div>
                        <div class="glass-card rounded-xl p-3 text-center">
                            <div class="text-2xl font-bold text-orange-600" x-text="anomalyStats.high || 0"></div>
                            <div class="text-xs text-gray-500">HIGH</div>
                        </div>
                        <div class="glass-card rounded-xl p-3 text-center">
                            <div class="text-2xl font-bold text-yellow-600" x-text="anomalyStats.medium || 0"></div>
                            <div class="text-xs text-gray-500">MEDIUM</div>
                        </div>
                        <div class="glass-card rounded-xl p-3 text-center">
                            <div class="text-2xl font-bold text-green-600" x-text="anomalyStats.low || 0"></div>
                            <div class="text-xs text-gray-500">LOW</div>
                        </div>
                    </div>

                    <!-- Anomalies List -->
                    <div class="space-y-3">
                        <template x-for="anomaly in paginatedAnomalies" :key="anomaly.id">
                            <div :class="getSeverityClass(anomaly.severity)" class="rounded-xl shadow-lg overflow-hidden cursor-pointer hover:shadow-xl transition-all" @click="showAnomalyDetail(anomaly)">
                                <div class="text-white p-4">
                                    <div class="flex justify-between items-start">
                                        <div>
                                            <div class="flex items-center gap-2">
                                                <span class="text-2xl" x-text="getTypeIcon(anomaly.anomaly_type)"></span>
                                                <h3 class="font-bold text-lg" x-text="formatTextWithDates(anomaly.title)"></h3>
                                                <span x-show="anomaly.filing_year" class="bg-white/20 px-2 py-0.5 rounded text-xs" x-text="anomaly.filing_year"></span>
                                            </div>
                                            <div class="flex items-center gap-2 mt-1 text-white/80">
                                                <span x-text="anomaly.member_name"></span>
                                                <span :class="{
                                                    'bg-blue-400': anomaly.member_party === 'D',
                                                    'bg-red-400': anomaly.member_party === 'R',
                                                    'bg-gray-400': anomaly.member_party === 'I'
                                                }" class="px-2 py-0.5 rounded text-xs" x-text="anomaly.member_party"></span>
                                                <span x-text="anomaly.member_state"></span>
                                            </div>
                                        </div>
                                        <div class="text-right">
                                            <div class="text-xl font-bold uppercase" x-text="getSeverityLabel(anomaly.severity)"></div>
                                        </div>
                                    </div>
                                </div>
                                <div class="bg-white p-4">
                                    <p class="text-gray-700" x-text="formatTextWithDates(anomaly.description)"></p>
                                    <div class="flex justify-between items-center mt-2">
                                        <div class="text-xs text-gray-500">
                                            <span x-show="anomaly.filing_year" class="font-semibold text-blue-600">Year <span x-text="anomaly.filing_year"></span></span>
                                            <span x-show="anomaly.disclosure_id"> | 📄 Disclosure #<span x-text="anomaly.disclosure_id"></span></span>
                                            <span x-show="anomaly.transaction_id"> | Transaction #<span x-text="anomaly.transaction_id"></span></span>
                                            <span x-show="!anomaly.disclosure_id && !anomaly.transaction_id">📊 Computed from member data</span>
                                        </div>
                                        <div class="text-xs text-blue-500 font-medium">Click for full details →</div>
                                    </div>
                                </div>
                            </div>
                        </template>
                        
                        <div x-show="filteredAnomalies.length === 0" class="glass-card rounded-xl p-12 text-center">
                            <div class="text-6xl mb-4">🔍</div>
                            <h3 class="text-xl font-medium text-gray-700">No anomalies match your filters</h3>
                            <button @click="clearFilters()" class="mt-4 px-6 py-2 bg-blue-600 text-white rounded-lg">Clear All Filters</button>
                        </div>
                    </div>
                    
                    <div x-show="filteredAnomalies.length > 0" class="mt-6 flex justify-between items-center">
                        <div class="text-white/70">Showing <span x-text="paginatedAnomalies.length"></span> of <span x-text="filteredAnomalies.length"></span> anomalies</div>
                        <div class="flex gap-2">
                            <button @click="prevPage()" :disabled="currentPage <= 1" class="px-4 py-2 bg-white/10 text-white rounded-lg disabled:opacity-50">← Previous</button>
                            <span class="px-4 py-2 text-white">Page <span x-text="currentPage"></span> of <span x-text="totalPages"></span></span>
                            <button @click="nextPage()" :disabled="currentPage >= totalPages" class="px-4 py-2 bg-white/10 text-white rounded-lg disabled:opacity-50">Next →</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Member Detail Modal -->
        <div x-show="showMemberModal" class="fixed inset-0 z-50 overflow-y-auto" x-cloak>
            <div class="flex items-center justify-center min-h-screen px-4">
                <div class="fixed inset-0 bg-black/50" @click="showMemberModal = false"></div>
                <div class="relative bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[80vh] overflow-y-auto">
                    <div class="sticky top-0 bg-gradient-to-r from-blue-600 to-blue-800 text-white p-6">
                        <button @click="showMemberModal = false" class="absolute top-4 right-4 text-white/80 hover:text-white text-2xl">&times;</button>
                        <h2 class="text-2xl font-bold" x-text="selectedMember?.name"></h2>
                        <div class="flex items-center gap-2 mt-1">
                            <span :class="{'bg-blue-400': selectedMember?.party === 'D', 'bg-red-400': selectedMember?.party === 'R'}" class="px-2 py-0.5 rounded text-sm" x-text="selectedMember?.party === 'D' ? 'Democrat' : selectedMember?.party === 'R' ? 'Republican' : 'Independent'"></span>
                            <span x-text="selectedMember?.state"></span>
                        </div>
                    </div>
                    <div class="p-6">
                        <h3 class="font-bold text-lg mb-4 text-gray-800">Anomalies for this Member (<span x-text="memberAnomalies.length"></span>)</h3>
                        <div class="space-y-3">
                            <template x-for="anomaly in memberAnomalies" :key="anomaly.id">
                                <div class="border rounded-lg p-4 cursor-pointer hover:bg-gray-50" @click="showAnomalyDetail(anomaly); showMemberModal = false;">
                                    <div class="flex justify-between items-start">
                                        <div>
                                            <div class="font-medium" x-text="formatTextWithDates(anomaly.title)"></div>
                                            <div class="text-sm text-gray-500" x-text="getTypeName(anomaly.anomaly_type)"></div>
                                        </div>
                                        <span :class="getSeverityBadgeClass(anomaly.severity)" class="px-2 py-1 rounded text-white text-sm font-bold" x-text="getSeverityLabel(anomaly.severity)"></span>
                                    </div>
                                    <p class="text-gray-600 mt-2 text-sm" x-text="formatTextWithDates(anomaly.description)"></p>
                                </div>
                            </template>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Anomaly Detail Modal -->
        <div x-show="showAnomalyModal" class="fixed inset-0 z-50 overflow-y-auto" x-cloak>
            <div class="flex items-center justify-center min-h-screen px-4">
                <div class="fixed inset-0 bg-black/50" @click="showAnomalyModal = false"></div>
                <div class="relative bg-white rounded-xl shadow-2xl max-w-3xl w-full max-h-[90vh] overflow-y-auto">
                    <div :class="getSeverityClass(selectedAnomaly?.severity)" class="sticky top-0 text-white p-6">
                        <button @click="showAnomalyModal = false" class="absolute top-4 right-4 text-white/80 hover:text-white text-2xl">&times;</button>
                        <div class="flex items-center gap-3">
                            <span class="text-4xl" x-text="getTypeIcon(selectedAnomaly?.anomaly_type)"></span>
                            <div>
                                <h2 class="text-2xl font-bold" x-text="formatTextWithDates(selectedAnomaly?.title)"></h2>
                                <div class="text-white/80 mt-1"><span class="font-bold text-xl uppercase" x-text="getSeverityLabel(selectedAnomaly?.severity)"></span></div>
                            </div>
                        </div>
                    </div>
                    <div class="p-6">
                        <!-- Member Info -->
                        <div class="bg-gray-50 rounded-lg p-4 mb-6">
                            <h3 class="font-bold text-gray-700 mb-3">👤 Member Information</h3>
                            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                                <div>
                                    <div class="text-xs text-gray-500 uppercase">Name</div>
                                    <div class="font-medium" x-text="selectedAnomaly?.member_name"></div>
                                </div>
                                <div>
                                    <div class="text-xs text-gray-500 uppercase">Party</div>
                                    <span :class="{
                                        'bg-blue-100 text-blue-800': selectedAnomaly?.member_party === 'D',
                                        'bg-red-100 text-red-800': selectedAnomaly?.member_party === 'R',
                                        'bg-gray-100 text-gray-800': selectedAnomaly?.member_party === 'I'
                                    }" class="px-2 py-0.5 rounded text-sm font-medium" x-text="selectedAnomaly?.member_party === 'D' ? 'Democrat' : selectedAnomaly?.member_party === 'R' ? 'Republican' : 'Independent'"></span>
                                </div>
                                <div>
                                    <div class="text-xs text-gray-500 uppercase">State</div>
                                    <div class="font-medium" x-text="selectedAnomaly?.member_state"></div>
                                </div>
                                <div>
                                    <div class="text-xs text-gray-500 uppercase">Member ID</div>
                                    <div class="font-medium" x-text="selectedAnomaly?.member_id"></div>
                                </div>
                            </div>
                        </div>

                        <!-- Anomaly Details -->
                        <div class="mb-6">
                            <h3 class="font-bold text-gray-700 mb-3">📋 Anomaly Details</h3>
                            <p class="text-gray-700 text-lg mb-4" x-text="formatTextWithDates(selectedAnomaly?.description)"></p>
                            <div class="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
                                <div class="bg-blue-50 rounded-lg p-3">
                                    <div class="text-xs text-blue-600 uppercase font-medium">Type</div>
                                    <div class="font-bold text-blue-800" x-text="getTypeName(selectedAnomaly?.anomaly_type)"></div>
                                </div>
                                <div class="bg-purple-50 rounded-lg p-3" x-show="selectedAnomaly?.computed_value">
                                    <div class="text-xs text-purple-600 uppercase font-medium">Computed Value</div>
                                    <div class="font-bold text-purple-800" x-text="formatValue(selectedAnomaly?.computed_value)"></div>
                                </div>
                                <div class="bg-orange-50 rounded-lg p-3" x-show="selectedAnomaly?.threshold_value">
                                    <div class="text-xs text-orange-600 uppercase font-medium">Threshold</div>
                                    <div class="font-bold text-orange-800" x-text="formatValue(selectedAnomaly?.threshold_value)"></div>
                                    <div class="text-xs text-orange-600 mt-1" x-text="getThresholdSource(selectedAnomaly?.anomaly_type)"></div>
                                </div>
                            </div>
                        </div>

                        <!-- Data Origin -->
                        <div class="bg-indigo-50 border border-indigo-200 rounded-lg p-4 mb-6">
                            <h3 class="font-bold text-indigo-700 mb-2">📑 Data Origin</h3>
                            <p class="text-sm text-indigo-600 mb-3">This section shows which official sources and records were used to detect this anomaly:</p>
                            <div class="text-sm text-indigo-600">
                                <div x-show="selectedAnomaly?.disclosure_id" class="flex items-center gap-2 mb-1">
                                    <span class="font-medium">📄 Disclosure ID:</span>
                                    <span x-text="selectedAnomaly?.disclosure_id"></span>
                                    <span class="text-xs text-indigo-500">(from official financial disclosure filing)</span>
                                </div>
                                <div x-show="selectedAnomaly?.transaction_id" class="flex items-center gap-2 mb-1">
                                    <span class="font-medium">💳 Transaction ID:</span>
                                    <span x-text="selectedAnomaly?.transaction_id"></span>
                                    <span class="text-xs text-indigo-500">(specific trade from disclosure)</span>
                                </div>
                                <div x-show="!selectedAnomaly?.disclosure_id && !selectedAnomaly?.transaction_id" class="text-indigo-500">
                                    <span>📊 This anomaly was computed by analyzing multiple disclosure records for this member. The pattern was detected across their filing history.</span>
                                </div>
                            </div>
                        </div>

                        <!-- What This Means -->
                        <div class="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-6">
                            <div class="flex items-start gap-3">
                                <span class="text-2xl">💡</span>
                                <div>
                                    <div class="font-bold text-yellow-800 mb-1">What this means</div>
                                    <p class="text-yellow-700" x-text="getExplanation(selectedAnomaly?.anomaly_type)"></p>
                                </div>
                            </div>
                        </div>

                        <!-- Supporting Documents -->
                        <div class="bg-gray-50 rounded-lg p-4">
                            <h3 class="font-bold text-gray-700 mb-3">📄 Supporting Documents & Evidence</h3>
                            
                            <div x-show="anomalyDocuments.length > 0">
                                <template x-for="doc in anomalyDocuments" :key="doc.id">
                                    <div class="bg-white border rounded-lg p-3 mb-2">
                                        <div class="flex justify-between items-start">
                                            <div class="flex-1">
                                                <div class="font-medium text-gray-800" x-text="getFilingTypeLabel(doc.filing_type)"></div>
                                                <div class="text-sm text-gray-500">Year: <span x-text="doc.filing_year"></span> | Doc ID: <span class="font-mono bg-yellow-100 px-1 rounded" x-text="doc.document_id"></span></div>
                                            </div>
                                            <div class="flex items-center gap-2">
                                                <span class="text-xs px-2 py-1 rounded" :class="doc.parsed ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'" x-text="doc.parsed ? 'Parsed' : 'Raw'"></span>
                                                
                                                <!-- QuiverQuant API data -->
                                                <template x-if="isQuiverQuantDoc(doc)">
                                                    <span class="px-3 py-1 bg-blue-100 text-blue-700 text-xs rounded flex items-center gap-1"
                                                          title="Data aggregated from official congressional disclosures by QuiverQuant">
                                                        📊 QuiverQuant API
                                                    </span>
                                                </template>
                                                
                                                <!-- House/Senate official records -->
                                                <template x-if="!isQuiverQuantDoc(doc)">
                                                    <span class="px-3 py-1 bg-green-100 text-green-700 text-xs rounded">
                                                        🏛️ Official Record
                                                    </span>
                                                </template>
                                            </div>
                                        </div>
                                        
                                        <!-- Verification instructions based on source -->
                                        <div class="mt-3 p-2 bg-amber-50 border border-amber-200 rounded text-xs">
                                            <template x-if="isQuiverQuantDoc(doc)">
                                                <div>
                                                    <div class="font-semibold text-amber-800 mb-1">📋 How to verify this data:</div>
                                                    <p class="text-amber-700">This trade data comes from QuiverQuant's API, which aggregates official congressional disclosures. To see the original filing:</p>
                                                    <div class="mt-2 flex flex-wrap gap-2">
                                                        <a href="https://disclosures-clerk.house.gov/FinancialDisclosure#Search" 
                                                           target="_blank" 
                                                           class="inline-flex items-center gap-1 px-2 py-1 bg-white border border-amber-300 rounded hover:bg-amber-100">
                                                            🏛️ Search House Disclosures
                                                        </a>
                                                        <a href="https://efdsearch.senate.gov/search/" 
                                                           target="_blank" 
                                                           class="inline-flex items-center gap-1 px-2 py-1 bg-white border border-amber-300 rounded hover:bg-amber-100">
                                                            🏛️ Search Senate Disclosures
                                                        </a>
                                                    </div>
                                                    <p class="text-amber-600 mt-2 italic">Search for the member's name to find the original Periodic Transaction Report (PTR).</p>
                                                </div>
                                            </template>
                                            <template x-if="!isQuiverQuantDoc(doc)">
                                                <div>
                                                    <div class="font-semibold text-amber-800 mb-1">📋 How to verify this filing:</div>
                                                    <p class="text-amber-700 mb-2">This disclosure ID comes from official House/Senate records. Search for it here:</p>
                                                    <div class="flex flex-wrap gap-2">
                                                        <a :href="'https://disclosures-clerk.house.gov/FinancialDisclosure#Search'" 
                                                           target="_blank" 
                                                           class="inline-flex items-center gap-1 px-2 py-1 bg-white border border-amber-300 rounded hover:bg-amber-100">
                                                            🏛️ House Clerk Search
                                                        </a>
                                                        <a href="https://efdsearch.senate.gov/search/" 
                                                           target="_blank" 
                                                           class="inline-flex items-center gap-1 px-2 py-1 bg-white border border-amber-300 rounded hover:bg-amber-100">
                                                            🏛️ Senate EFD Search
                                                        </a>
                                                    </div>
                                                    <p class="text-amber-600 mt-2 italic">Enter the member's name and year to find the original PDF filing.</p>
                                                </div>
                                            </template>
                                        </div>
                                    </div>
                                </template>
                            </div>
                            <div x-show="anomalyDocuments.length === 0" class="text-center py-4 text-gray-500">
                                <p>Loading related documents...</p>
                            </div>
                            
                            <!-- Important Note about Data Sources -->
                            <div class="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
                                <div class="font-semibold text-blue-800 mb-2 flex items-center gap-2">
                                    <span>ℹ️</span> About Our Data Sources
                                </div>
                                <div class="text-sm text-blue-700 space-y-2">
                                    <p><strong>QuiverQuant API:</strong> Provides aggregated trade data from official PTR filings. The data is accurate but PDFs are not directly available through the API. Use the official search links above to find original documents.</p>
                                    <p><strong>House/Senate Records:</strong> Document IDs reference official filings. Some older PDFs may not be directly accessible via URL - use the search tools to locate them.</p>
                                    <p><strong>Why no direct PDF links?</strong> Government websites frequently change their URL structures. We provide search links to ensure you can always find the original documents.</p>
                                </div>
                            </div>
                            
                            <!-- Verify Our Sources Section -->
                            <div class="mt-4 p-4 bg-slate-50 border border-slate-200 rounded-lg">
                                <h4 class="font-bold text-slate-700 mb-3 flex items-center gap-2">
                                    <span>🔍</span> Verify Our Sources
                                </h4>
                                <p class="text-sm text-slate-600 mb-3">Don't take our word for it. All data comes from official government sources and can be independently verified:</p>
                                
                                <!-- Primary Sources -->
                                <div class="mb-4">
                                    <div class="text-xs font-semibold text-slate-500 uppercase mb-2">Primary Official Sources</div>
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
                                        <a href="https://disclosures-clerk.house.gov/FinancialDisclosure" 
                                           target="_blank" 
                                           class="flex items-center gap-2 p-2 bg-white border border-slate-200 rounded hover:bg-blue-50 hover:border-blue-300 transition-colors">
                                            <span class="text-lg">🏛️</span>
                                            <div>
                                                <div class="text-sm font-medium text-slate-800">House Financial Disclosures</div>
                                                <div class="text-xs text-slate-500">clerk.house.gov (Official)</div>
                                            </div>
                                        </a>
                                        <a href="https://efdsearch.senate.gov/search/" 
                                           target="_blank" 
                                           class="flex items-center gap-2 p-2 bg-white border border-slate-200 rounded hover:bg-blue-50 hover:border-blue-300 transition-colors">
                                            <span class="text-lg">🏛️</span>
                                            <div>
                                                <div class="text-sm font-medium text-slate-800">Senate Financial Disclosures</div>
                                                <div class="text-xs text-slate-500">efdsearch.senate.gov (Official)</div>
                                            </div>
                                        </a>
                                        <a href="https://www.congress.gov/members" 
                                           target="_blank" 
                                           class="flex items-center gap-2 p-2 bg-white border border-slate-200 rounded hover:bg-blue-50 hover:border-blue-300 transition-colors">
                                            <span class="text-lg">📋</span>
                                            <div>
                                                <div class="text-sm font-medium text-slate-800">Congress Member Data</div>
                                                <div class="text-xs text-slate-500">congress.gov (Official)</div>
                                            </div>
                                        </a>
                                        <a href="https://www.congress.gov/bill/112th-congress/senate-bill/2038" 
                                           target="_blank" 
                                           class="flex items-center gap-2 p-2 bg-white border border-slate-200 rounded hover:bg-blue-50 hover:border-blue-300 transition-colors">
                                            <span class="text-lg">📜</span>
                                            <div>
                                                <div class="text-sm font-medium text-slate-800">STOCK Act (S.2038)</div>
                                                <div class="text-xs text-slate-500">45-day filing requirement law</div>
                                            </div>
                                        </a>
                                    </div>
                                </div>
                                
                                <!-- Data Aggregators -->
                                <div class="mb-4">
                                    <div class="text-xs font-semibold text-slate-500 uppercase mb-2">Data Aggregators (Sources for Stock Trade Data)</div>
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
                                        <a href="https://www.quiverquant.com/sources/senatetrading" 
                                           target="_blank" 
                                           class="flex items-center gap-2 p-2 bg-white border border-slate-200 rounded hover:bg-blue-50 hover:border-blue-300 transition-colors">
                                            <span class="text-lg">📊</span>
                                            <div>
                                                <div class="text-sm font-medium text-slate-800">QuiverQuant API</div>
                                                <div class="text-xs text-slate-500">Aggregated congressional trading data</div>
                                            </div>
                                        </a>
                                        <a href="https://github.com/unitedstates/congress-legislators" 
                                           target="_blank" 
                                           class="flex items-center gap-2 p-2 bg-white border border-slate-200 rounded hover:bg-blue-50 hover:border-blue-300 transition-colors">
                                            <span class="text-lg">📂</span>
                                            <div>
                                                <div class="text-sm font-medium text-slate-800">Congress Legislators Database</div>
                                                <div class="text-xs text-slate-500">@unitedstates project (Open Source)</div>
                                            </div>
                                        </a>
                                    </div>
                                </div>
                                
                                <!-- Threshold Sources -->
                                <div class="p-3 bg-amber-50 border border-amber-200 rounded">
                                    <div class="text-xs font-semibold text-amber-700 uppercase mb-2">📏 Anomaly Detection Thresholds</div>
                                    <ul class="text-xs text-amber-800 space-y-1">
                                        <li><strong>High Trading Frequency (>10/month):</strong> Internal threshold based on analysis of typical member trading patterns</li>
                                        <li><strong>Large Trade (>$1M):</strong> Based on STOCK Act disclosure reporting tiers</li>
                                        <li><strong>Sector Concentration (>50%):</strong> Internal threshold - most members diversify more broadly</li>
                                        <li><strong>Late Filing (>45 days):</strong> <a href="https://www.congress.gov/bill/112th-congress/senate-bill/2038" target="_blank" class="underline">STOCK Act requirement</a></li>
                                    </ul>
                                </div>
                                
                                <p class="text-xs text-slate-500 mt-3 italic">
                                    💡 Tip: Search any member's name on these official sites to verify the disclosures shown here match the original filings.
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Stat Detail Modal -->
        <div x-show="statModalOpen" class="fixed inset-0 z-50 overflow-y-auto" x-cloak>
            <div class="flex items-center justify-center min-h-screen px-4">
                <div class="fixed inset-0 bg-black/50" @click="statModalOpen = false"></div>
                <div class="relative bg-white rounded-xl shadow-2xl max-w-4xl w-full max-h-[85vh] overflow-y-auto">
                    <div class="sticky top-0 bg-gradient-to-r from-slate-700 to-slate-900 text-white p-6 z-10">
                        <button @click="statModalOpen = false" class="absolute top-4 right-4 text-white/80 hover:text-white text-2xl">&times;</button>
                        <h2 class="text-2xl font-bold" x-text="statModalTitle"></h2>
                        <p class="text-white/80 mt-1" x-text="statModalSubtitle"></p>
                    </div>
                    <div class="p-6">
                        <!-- Members View -->
                        <div x-show="statModalType === 'members'">
                            <div class="flex gap-2 mb-4">
                                <input type="text" x-model="statSearchQuery" @input.debounce.300ms="loadStatData()"
                                       placeholder="Search members..." class="flex-1 px-4 py-2 border rounded-lg">
                                <select x-model="statPartyFilter" @change="loadStatData()" class="px-4 py-2 border rounded-lg">
                                    <option value="">All Parties</option>
                                    <option value="D">Democrat</option>
                                    <option value="R">Republican</option>
                                    <option value="I">Independent</option>
                                </select>
                            </div>
                            <div class="overflow-x-auto">
                                <table class="w-full text-sm">
                                    <thead class="bg-gray-100">
                                        <tr>
                                            <th class="px-4 py-3 text-left">Name</th>
                                            <th class="px-4 py-3 text-left">Party</th>
                                            <th class="px-4 py-3 text-left">State</th>
                                            <th class="px-4 py-3 text-left">Chamber</th>
                                            <th class="px-4 py-3 text-left">Disclosures</th>
                                            <th class="px-4 py-3 text-left">Anomalies</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <template x-for="m in statModalData" :key="m.id">
                                            <tr class="border-b hover:bg-gray-50">
                                                <td class="px-4 py-3 font-medium" x-text="m.first_name + ' ' + m.last_name"></td>
                                                <td class="px-4 py-3">
                                                    <span :class="{'bg-blue-100 text-blue-800': m.party === 'D', 'bg-red-100 text-red-800': m.party === 'R', 'bg-gray-100 text-gray-800': m.party === 'I'}" 
                                                          class="px-2 py-0.5 rounded text-xs font-medium" x-text="m.party"></span>
                                                </td>
                                                <td class="px-4 py-3" x-text="m.state"></td>
                                                <td class="px-4 py-3 capitalize" x-text="m.chamber"></td>
                                                <td class="px-4 py-3" x-text="m.disclosure_count || 0"></td>
                                                <td class="px-4 py-3">
                                                    <span :class="m.anomaly_count > 0 ? 'bg-red-100 text-red-700 font-bold' : 'text-gray-500'" class="px-2 py-0.5 rounded" x-text="m.anomaly_count || 0"></span>
                                                </td>
                                            </tr>
                                        </template>
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <!-- Disclosures View -->
                        <div x-show="statModalType === 'disclosures' || statModalType === 'parsed'">
                            <div class="overflow-x-auto">
                                <table class="w-full text-sm">
                                    <thead class="bg-gray-100">
                                        <tr>
                                            <th class="px-4 py-3 text-left">Member</th>
                                            <th class="px-4 py-3 text-left">Year</th>
                                            <th class="px-4 py-3 text-left">Type</th>
                                            <th class="px-4 py-3 text-left">Filed</th>
                                            <th class="px-4 py-3 text-left">Status</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <template x-for="d in statModalData" :key="d.id">
                                            <tr class="border-b hover:bg-gray-50">
                                                <td class="px-4 py-3 font-medium" x-text="d.member_name"></td>
                                                <td class="px-4 py-3" x-text="d.filing_year"></td>
                                                <td class="px-4 py-3" x-text="d.filing_type"></td>
                                                <td class="px-4 py-3" x-text="formatDate(d.filing_date)"></td>
                                                <td class="px-4 py-3">
                                                    <span :class="d.parsed ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'" class="px-2 py-0.5 rounded text-xs" x-text="d.parsed ? 'Parsed' : 'Pending'"></span>
                                                </td>
                                            </tr>
                                        </template>
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <!-- Trades View -->
                        <div x-show="statModalType === 'trades'">
                            <div class="overflow-x-auto">
                                <table class="w-full text-sm">
                                    <thead class="bg-gray-100">
                                        <tr>
                                            <th class="px-4 py-3 text-left">Member</th>
                                            <th class="px-4 py-3 text-left">Year</th>
                                            <th class="px-4 py-3 text-left">Type</th>
                                            <th class="px-4 py-3 text-left">Filed</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <template x-for="t in statModalData" :key="t.id">
                                            <tr class="border-b hover:bg-gray-50">
                                                <td class="px-4 py-3 font-medium" x-text="t.member_name"></td>
                                                <td class="px-4 py-3" x-text="t.filing_year"></td>
                                                <td class="px-4 py-3">
                                                    <span class="bg-purple-100 text-purple-800 px-2 py-0.5 rounded text-xs" x-text="t.filing_type"></span>
                                                </td>
                                                <td class="px-4 py-3" x-text="formatDate(t.filing_date)"></td>
                                            </tr>
                                        </template>
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <!-- Anomalies Summary View -->
                        <div x-show="statModalType === 'anomalies'">
                            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                                <div class="bg-red-50 border border-red-200 rounded-lg p-4 text-center">
                                    <div class="text-3xl font-bold text-red-600" x-text="anomalyStats.critical"></div>
                                    <div class="text-sm text-red-700">Critical</div>
                                </div>
                                <div class="bg-orange-50 border border-orange-200 rounded-lg p-4 text-center">
                                    <div class="text-3xl font-bold text-orange-600" x-text="anomalyStats.high"></div>
                                    <div class="text-sm text-orange-700">High</div>
                                </div>
                                <div class="bg-yellow-50 border border-yellow-200 rounded-lg p-4 text-center">
                                    <div class="text-3xl font-bold text-yellow-600" x-text="anomalyStats.medium"></div>
                                    <div class="text-sm text-yellow-700">Medium</div>
                                </div>
                                <div class="bg-green-50 border border-green-200 rounded-lg p-4 text-center">
                                    <div class="text-3xl font-bold text-green-600" x-text="anomalyStats.low"></div>
                                    <div class="text-sm text-green-700">Low</div>
                                </div>
                            </div>
                            <h3 class="font-bold text-gray-700 mb-3">Recent Anomalies</h3>
                            <div class="space-y-2">
                                <template x-for="a in statModalData.slice(0, 10)" :key="a.id">
                                    <div class="border rounded-lg p-3 cursor-pointer hover:bg-gray-50" @click="showAnomalyDetail(a); statModalOpen = false;">
                                        <div class="flex justify-between items-start">
                                            <div>
                                                <div class="font-medium" x-text="a.title"></div>
                                                <div class="text-sm text-gray-500" x-text="a.member_name + ' • ' + getTypeName(a.anomaly_type)"></div>
                                            </div>
                                            <span :class="getSeverityBadgeClass(a.severity)" class="px-2 py-1 rounded text-white text-xs font-bold" x-text="getSeverityLabel(a.severity)"></span>
                                        </div>
                                    </div>
                                </template>
                            </div>
                        </div>

                        <!-- Pagination -->
                        <div class="mt-4 flex justify-between items-center">
                            <span class="text-gray-600">Page <span x-text="statModalPage"></span></span>
                            <div class="flex gap-2">
                                <button @click="statModalPage--; loadStatData()" :disabled="statModalPage <= 1" class="px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded disabled:opacity-50">Prev</button>
                                <button @click="statModalPage++; loadStatData()" class="px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded">Next</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Footer with Data Explorer -->
        <footer class="bg-black/30 text-white/60 py-6 mt-8">
            <div class="container mx-auto px-4">
                <div class="mb-6">
                    <button @click="showDataExplorer = !showDataExplorer; if(showDataExplorer && explorerMembers.length === 0) loadExplorerMembers()" 
                            class="w-full py-3 bg-white/10 hover:bg-white/20 rounded-lg text-white transition-all flex items-center justify-center gap-2">
                        <span x-text="showDataExplorer ? '▼' : '▶'"></span>
                        <span>Data Explorer (Members, Disclosures, Stock Trades)</span>
                    </button>
                </div>
                
                <div x-show="showDataExplorer" class="mb-6">
                    <div class="bg-white rounded-xl overflow-hidden shadow-lg">
                        <div class="flex border-b">
                            <button @click="explorerTab = 'members'; loadExplorerMembers()"
                                    :class="explorerTab === 'members' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'"
                                    class="px-6 py-3 font-medium transition-all">👥 Members</button>
                            <button @click="explorerTab = 'disclosures'; loadExplorerDisclosures()"
                                    :class="explorerTab === 'disclosures' ? 'bg-green-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'"
                                    class="px-6 py-3 font-medium transition-all">📄 Disclosures</button>
                            <button @click="explorerTab = 'trades'; loadExplorerTrades()"
                                    :class="explorerTab === 'trades' ? 'bg-purple-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'"
                                    class="px-6 py-3 font-medium transition-all">📈 Stock Trades</button>
                        </div>
                        
                        <!-- Members Explorer -->
                        <div x-show="explorerTab === 'members'" class="p-4">
                            <div class="flex gap-2 mb-4">
                                <input type="text" x-model="explorerSearch" @input.debounce.300ms="loadExplorerMembers()"
                                       placeholder="Search members..." class="flex-1 px-4 py-2 border rounded-lg text-gray-800">
                                <select x-model="explorerParty" @change="loadExplorerMembers()" class="px-4 py-2 border rounded-lg text-gray-800">
                                    <option value="">All Parties</option>
                                    <option value="D">Democrat</option>
                                    <option value="R">Republican</option>
                                </select>
                            </div>
                            <div class="overflow-x-auto">
                                <table class="w-full text-sm">
                                    <thead class="bg-gray-100 text-gray-700">
                                        <tr>
                                            <th class="px-4 py-2 text-left font-semibold">Name</th>
                                            <th class="px-4 py-2 text-left font-semibold">Party</th>
                                            <th class="px-4 py-2 text-left font-semibold">State</th>
                                            <th class="px-4 py-2 text-left font-semibold">Chamber</th>
                                        </tr>
                                    </thead>
                                    <tbody class="text-gray-800">
                                        <template x-for="m in explorerMembers" :key="m.id">
                                            <tr class="border-b hover:bg-gray-50">
                                                <td class="px-4 py-2 font-medium" x-text="m.first_name + ' ' + m.last_name"></td>
                                                <td class="px-4 py-2">
                                                    <span :class="{'bg-blue-100 text-blue-800': m.party === 'D', 'bg-red-100 text-red-800': m.party === 'R'}" 
                                                          class="px-2 py-0.5 rounded text-xs font-medium" x-text="m.party"></span>
                                                </td>
                                                <td class="px-4 py-2" x-text="m.state"></td>
                                                <td class="px-4 py-2 capitalize" x-text="m.chamber"></td>
                                            </tr>
                                        </template>
                                    </tbody>
                                </table>
                            </div>
                            <div class="mt-4 flex justify-between items-center text-gray-700">
                                <span>Page <span x-text="explorerPage"></span></span>
                                <div class="flex gap-2">
                                    <button @click="explorerPage--; loadExplorerMembers()" :disabled="explorerPage <= 1" class="px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded disabled:opacity-50 text-gray-800">Prev</button>
                                    <button @click="explorerPage++; loadExplorerMembers()" class="px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded text-gray-800">Next</button>
                                </div>
                            </div>
                        </div>
                        
                        <!-- Disclosures Explorer -->
                        <div x-show="explorerTab === 'disclosures'" class="p-4">
                            <div class="overflow-x-auto">
                                <table class="w-full text-sm">
                                    <thead class="bg-gray-100 text-gray-700">
                                        <tr>
                                            <th class="px-4 py-2 text-left font-semibold">Member</th>
                                            <th class="px-4 py-2 text-left font-semibold">Year</th>
                                            <th class="px-4 py-2 text-left font-semibold">Type</th>
                                            <th class="px-4 py-2 text-left font-semibold">Filed</th>
                                            <th class="px-4 py-2 text-left font-semibold">Parsed</th>
                                        </tr>
                                    </thead>
                                    <tbody class="text-gray-800">
                                        <template x-for="d in explorerDisclosures" :key="d.id">
                                            <tr class="border-b hover:bg-gray-50">
                                                <td class="px-4 py-2 font-medium" x-text="d.member_name"></td>
                                                <td class="px-4 py-2" x-text="d.filing_year"></td>
                                                <td class="px-4 py-2" x-text="d.filing_type"></td>
                                                <td class="px-4 py-2" x-text="formatDate(d.filing_date)"></td>
                                                <td class="px-4 py-2">
                                                    <span :class="d.parsed ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'" class="px-2 py-0.5 rounded text-xs font-medium" x-text="d.parsed ? 'Yes' : 'No'"></span>
                                                </td>
                                            </tr>
                                        </template>
                                    </tbody>
                                </table>
                            </div>
                            <div class="mt-4 flex justify-between items-center text-gray-700">
                                <span>Page <span x-text="explorerPage"></span></span>
                                <div class="flex gap-2">
                                    <button @click="explorerPage--; loadExplorerDisclosures()" :disabled="explorerPage <= 1" class="px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded disabled:opacity-50 text-gray-800">Prev</button>
                                    <button @click="explorerPage++; loadExplorerDisclosures()" class="px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded text-gray-800">Next</button>
                                </div>
                            </div>
                        </div>
                        
                        <!-- Trades Explorer -->
                        <div x-show="explorerTab === 'trades'" class="p-4">
                            <div class="overflow-x-auto">
                                <table class="w-full text-sm">
                                    <thead class="bg-gray-100 text-gray-700">
                                        <tr>
                                            <th class="px-4 py-2 text-left font-semibold">Member</th>
                                            <th class="px-4 py-2 text-left font-semibold">Year</th>
                                            <th class="px-4 py-2 text-left font-semibold">Type</th>
                                            <th class="px-4 py-2 text-left font-semibold">Filed</th>
                                        </tr>
                                    </thead>
                                    <tbody class="text-gray-800">
                                        <template x-for="t in explorerTrades" :key="t.id">
                                            <tr class="border-b hover:bg-gray-50">
                                                <td class="px-4 py-2 font-medium" x-text="t.member_name"></td>
                                                <td class="px-4 py-2" x-text="t.filing_year"></td>
                                                <td class="px-4 py-2">
                                                    <span class="bg-purple-100 text-purple-800 px-2 py-0.5 rounded text-xs font-medium" x-text="t.filing_type"></span>
                                                </td>
                                                <td class="px-4 py-2" x-text="formatDate(t.filing_date)"></td>
                                            </tr>
                                        </template>
                                    </tbody>
                                </table>
                            </div>
                            <div class="mt-4 flex justify-between items-center text-gray-700">
                                <span>Page <span x-text="explorerPage"></span></span>
                                <div class="flex gap-2">
                                    <button @click="explorerPage--; loadExplorerTrades()" :disabled="explorerPage <= 1" class="px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded disabled:opacity-50 text-gray-800">Prev</button>
                                    <button @click="explorerPage++; loadExplorerTrades()" class="px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded text-gray-800">Next</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Footer -->
        <footer class="bg-black/30 text-white/60 py-6 mt-8 border-t border-white/10">
            <div class="container mx-auto px-4 text-center text-sm">
                <p>Honest Congress - Congressional Financial Disclosure Analyzer</p>
                <p class="mt-1">Data: House Clerk, QuiverQuant API, congress-legislators</p>
            </div>
        </footer>
    </div>

    <script>
    <script>
        // Dashboard defined in head
    </script>
</body>
</html>
"""

@router.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the enhanced anomaly-focused dashboard."""
    response = HTMLResponse(content=DASHBOARD_HTML)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
