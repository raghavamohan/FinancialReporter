/* ==========================================================================
   Financial Reporter Javascript Controller - State Management & Chart Plotting
   ========================================================================== */

// ─── Metrics Registry ────────────────────────────────────────────────────
// Central definition of every metric. Each entry drives the table rows,
// chart datasets, and the metrics picker UI.
const METRICS_REGISTRY = [
    // ── Common / Shared ──────────────────────────────────────────────
    { key: "revenue",               label: "Revenue from Operations (Cr)",  fmt: "cr",  cls: "",               group: "Income",       sector: "mfg",  chartType: "bar"  },
    { key: "total_income",          label: "Total Income (Cr)",             fmt: "cr",  cls: "",               group: "Income",       sector: "bank", chartType: "bar"  },
    { key: "nii",                   label: "Net Interest Income (Cr)",      fmt: "cr",  cls: "",               group: "Income",       sector: "bank", chartType: "bar"  },
    { key: "gross_profit",          label: "Gross Profit (Cr)",             fmt: "cr",  cls: "",               group: "Income",       sector: "mfg",  chartType: "bar"  },
    { key: "ebitda",                label: "EBITDA (Cr)",                   fmt: "cr",  cls: "highlight-value", group: "Operating",   sector: "mfg",  chartType: "bar"  },
    { key: "ppop",                  label: "Pre-Provision Op Profit (Cr)",  fmt: "cr",  cls: "highlight-value", group: "Operating",   sector: "bank", chartType: "bar"  },
    { key: "pbit",                  label: "Operating EBIT / PBIT (Cr)",    fmt: "cr",  cls: "",               group: "Operating",    sector: "mfg",  chartType: "bar"  },
    { key: "pbt",                   label: "Profit Before Tax (Cr)",        fmt: "cr",  cls: "",               group: "Income",       sector: "both", chartType: "bar"  },
    { key: "net_income",            label: "Net Income (Cr)",               fmt: "cr",  cls: "highlight-value", group: "Income",      sector: "both", chartType: "bar"  },
    { key: "basic_eps",             label: "Basic EPS (Rs)",                fmt: "raw", cls: "",               group: "Per Share",    sector: "both", chartType: "line" },
    { key: "diluted_eps",           label: "Diluted EPS (Rs)",              fmt: "raw", cls: "",               group: "Per Share",    sector: "both", chartType: "line" },
    { key: "trailing_eps",          label: "Trailing EPS (Rs)",             fmt: "raw", cls: "",               group: "Per Share",    sector: "both", chartType: "line" },
    { key: "share_price",           label: "Share Price (Rs)",              fmt: "raw", cls: "",               group: "Valuation",    sector: "both", chartType: "line" },
    { key: "quarter_dividend",      label: "Quarter Dividend (Rs/sh)",      fmt: "raw", cls: "",               group: "Per Share",    sector: "both", chartType: "bar"  },

    // ── Margins & Ratios ─────────────────────────────────────────────
    { key: "gross_margin",          label: "Gross Margin (%)",              fmt: "pct", cls: "margin-value",   group: "Margins",      sector: "mfg",  chartType: "line" },
    { key: "ebitda_margin",         label: "EBITDA Margin (%)",             fmt: "pct", cls: "margin-value",   group: "Margins",      sector: "mfg",  chartType: "line" },
    { key: "net_margin",            label: "Net Profit Margin (%)",         fmt: "pct", cls: "margin-value",   group: "Margins",      sector: "both", chartType: "line" },
    { key: "cost_to_income",        label: "Cost-to-Income (%)",            fmt: "pct", cls: "margin-value",   group: "Margins",      sector: "bank", chartType: "line" },

    // ── Valuation ────────────────────────────────────────────────────
    { key: "pe_ratio",              label: "Valuation P/E Ratio",           fmt: "raw", cls: "valuation-value", group: "Valuation",   sector: "both", chartType: "line" },
    { key: "pb_ratio",              label: "Valuation P/B Ratio",           fmt: "raw", cls: "valuation-value", group: "Valuation",   sector: "both", chartType: "line" },

    // ── Returns ──────────────────────────────────────────────────────
    { key: "roe",                   label: "Return on Equity (ROE %)",      fmt: "pct", cls: "margin-value",   group: "Returns",      sector: "both", chartType: "line" },
    { key: "roce",                  label: "Capital Employed ROCE (%)",     fmt: "pct", cls: "margin-value",   group: "Returns",      sector: "mfg",  chartType: "line" },
    { key: "roa",                   label: "Return on Assets (ROA %)",      fmt: "pct", cls: "margin-value",   group: "Returns",      sector: "both", chartType: "line" },

    // ── Asset Quality (Banks) ────────────────────────────────────────
    { key: "gnpa_pct",              label: "Gross NPA (%)",                 fmt: "pct", cls: "",               group: "Asset Quality", sector: "bank", chartType: "line" },
    { key: "nnpa_pct",              label: "Net NPA (%)",                   fmt: "pct", cls: "",               group: "Asset Quality", sector: "bank", chartType: "line" },
];

// Build lookup by key
const METRICS_BY_KEY = {};
METRICS_REGISTRY.forEach(m => { METRICS_BY_KEY[m.key] = m; });


// ─── Global Dashboard State ─────────────────────────────────────────────
const state = {
    availableSymbols: [],
    cachedSymbols: new Set(),
    selectedSymbols: ["HDFCBANK", "RELIANCE"], // Default initial selections
    availableQuarters: [],
    metricsData: null,
    chartInstance: null,
    selectedMetrics: ["revenue", "total_income"],  // default selection on load
    activeCompanyTab: null,  // currently selected company symbol in tabs
};

// DOM Cache Elements
const DOM = {
    form: document.getElementById("control-form"),
    symbolSearch: document.getElementById("symbol-search"),
    selectedTags: document.getElementById("selected-tags"),
    suggestionsBox: document.getElementById("suggestions-box"),
    quarterSelect: document.getElementById("quarter-select"),
    slider: document.getElementById("back-quarters-slider"),
    sliderVal: document.getElementById("back-quarters-val"),
    ebitdaDef: document.getElementById("ebitda-definition"),
    analyzeBtn: document.getElementById("analyze-btn"),
    offlineBadge: document.getElementById("offline-badge"),
    networkBadge: document.getElementById("network-badge"),
    logConsole: document.getElementById("log-console"),
    logList: document.getElementById("log-list"),
    emptyState: document.getElementById("empty-state"),
    resultsArea: document.getElementById("results-area"),
    companyTabBar: document.getElementById("company-tab-bar"),
    companyTabContent: document.getElementById("company-tab-content"),
    exportCsvBtn: document.getElementById("export-csv-btn"),
    // Metrics picker
    metricsPickerWrapper: document.getElementById("metrics-picker-wrapper"),
    metricsSearch: document.getElementById("metrics-search"),
    metricsSelectedTags: document.getElementById("metrics-selected-tags"),
    metricsDropdown: document.getElementById("metrics-dropdown"),
    metricsDropdownList: document.getElementById("metrics-dropdown-list"),
};

// Initialize Dashboard
document.addEventListener("DOMContentLoaded", async () => {
    // 1. Fetch available symbols and quarters
    await fetchMetaDetails();

    // 2. Set up event listeners
    setupEventListeners();

    // 3. Render tags for initial selections
    renderTags();

    // 4. Build the metrics picker dropdown
    buildMetricsPicker();

    // 5. Auto-execute initial analysis for HDFCBANK & RELIANCE Q4_FY26
    analyzePerformance();
});

// Setup metadata details on boot
async function fetchMetaDetails() {
    try {
        // Fetch symbols list
        const symRes = await fetch("/api/symbols");
        if (symRes.ok) {
            const data = await symRes.json();
            state.availableSymbols = data.symbols || [];
            state.cachedSymbols = new Set(data.cached || []);
        }

        // Fetch quarters list
        const qtrRes = await fetch("/api/quarters");
        if (qtrRes.ok) {
            const data = await qtrRes.json();
            state.availableQuarters = data.quarters || [];
            populateQuarterDropdown();
        }
    } catch (e) {
        console.error("Error loading metadata: ", e);
        addLog("Error connecting to Python backend server.", "error");
    }
}

function populateQuarterDropdown() {
    const datalist = document.getElementById("quarter-options");
    if (!datalist) return;
    datalist.innerHTML = "";
    state.availableQuarters.forEach((quarter) => {
        const option = document.createElement("option");
        option.value = quarter;
        datalist.appendChild(option);
    });
    // Set default standard value
    DOM.quarterSelect.value = "Q4_FY26";
}

function setupEventListeners() {
    // Slider Change
    DOM.slider.addEventListener("input", (e) => {
        DOM.sliderVal.textContent = e.target.value;
    });

    // Stock selection input typing (auto-suggestions)
    DOM.symbolSearch.addEventListener("input", (e) => {
        showSuggestions(e.target.value);
    });

    // Close suggestions box on clicking outside
    document.addEventListener("click", (e) => {
        if (!DOM.symbolSearch.contains(e.target) && !DOM.suggestionsBox.contains(e.target)) {
            DOM.suggestionsBox.classList.add("hidden");
        }
        // Close metrics dropdown on clicking outside
        if (!DOM.metricsPickerWrapper.contains(e.target) && !DOM.metricsDropdown.contains(e.target)) {
            DOM.metricsDropdown.classList.add("hidden");
        }
    });

    // Trigger tag creation on Enter in search box
    DOM.symbolSearch.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            const val = DOM.symbolSearch.value.trim().toUpperCase();
            if (val && !state.selectedSymbols.includes(val)) {
                state.selectedSymbols.push(val);
                renderTags();
                DOM.symbolSearch.value = "";
                DOM.suggestionsBox.classList.add("hidden");
            }
        }
    });

    // Form submit triggers analysis
    DOM.form.addEventListener("submit", analyzePerformance);



    // Export CSV click
    DOM.exportCsvBtn.addEventListener("click", exportToCSV);

    // ── Metrics Picker Events ────────────────────────────────────────
    DOM.metricsSearch.addEventListener("focus", () => {
        DOM.metricsDropdown.classList.remove("hidden");
    });

    DOM.metricsSearch.addEventListener("input", () => {
        filterMetricsDropdown(DOM.metricsSearch.value);
    });

    DOM.metricsPickerWrapper.addEventListener("click", (e) => {
        // Clicking the wrapper area focuses the search and opens dropdown
        if (e.target === DOM.metricsPickerWrapper || e.target === DOM.metricsSelectedTags) {
            DOM.metricsSearch.focus();
        }
    });
}

// ─── Metrics Picker Logic ───────────────────────────────────────────────

function buildMetricsPicker() {
    renderMetricsDropdown("");
    renderMetricsTags();
}

function renderMetricsDropdown(filter) {
    const listEl = DOM.metricsDropdownList;
    listEl.innerHTML = "";

    const query = filter.trim().toLowerCase();
    let lastGroup = null;

    METRICS_REGISTRY.forEach(m => {
        // Filter by search query
        if (query && !m.label.toLowerCase().includes(query) && !m.key.toLowerCase().includes(query)) {
            return;
        }

        // Group header
        if (m.group !== lastGroup) {
            lastGroup = m.group;
            const groupLabel = document.createElement("div");
            groupLabel.className = "metrics-dropdown-group-label";
            groupLabel.textContent = m.group;
            listEl.appendChild(groupLabel);
        }

        const isChecked = state.selectedMetrics.includes(m.key);
        const item = document.createElement("div");
        item.className = `metrics-dropdown-item${isChecked ? ' checked' : ''}`;
        item.setAttribute("data-key", m.key);
        item.innerHTML = `
            <div class="mdp-checkbox"></div>
            <span class="mdp-label">${m.label}</span>
            <span class="mdp-key">${m.key}</span>
        `;

        item.addEventListener("click", () => {
            toggleMetric(m.key);
        });

        listEl.appendChild(item);
    });
}

function filterMetricsDropdown(query) {
    renderMetricsDropdown(query);
}

function toggleMetric(key) {
    const idx = state.selectedMetrics.indexOf(key);
    if (idx === -1) {
        state.selectedMetrics.push(key);
    } else {
        state.selectedMetrics.splice(idx, 1);
    }
    renderMetricsDropdown(DOM.metricsSearch.value);
    renderMetricsTags();

    // Live-update if results are visible
    if (state.metricsData) {
        renderResults();
        DOM.resultsArea.classList.remove("hidden");
    }
}

function renderMetricsTags() {
    DOM.metricsSelectedTags.innerHTML = "";
    if (state.selectedMetrics.length === 0) {
        // Show placeholder hint inline
        return;
    }
    state.selectedMetrics.forEach(key => {
        const meta = METRICS_BY_KEY[key];
        if (!meta) return;
        const tag = document.createElement("span");
        tag.className = "tag-badge";
        tag.innerHTML = `${meta.label} <i class="fa-solid fa-xmark" data-metric-key="${key}"></i>`;
        tag.querySelector("i").addEventListener("click", (e) => {
            e.stopPropagation();
            toggleMetric(e.target.getAttribute("data-metric-key"));
        });
        DOM.metricsSelectedTags.appendChild(tag);
    });
}

/**
 * Returns the list of metric keys to show for a given company type.
 * If the user has selected specific metrics, filter to those.
 * Otherwise return all applicable for the sector.
 */
function getVisibleMetrics(companyType) {
    const sectorFilter = companyType === "bank" ? ["bank", "both"] : ["mfg", "both"];
    const allApplicable = METRICS_REGISTRY.filter(m => sectorFilter.includes(m.sector));

    if (state.selectedMetrics.length === 0) {
        return allApplicable;
    }

    // Filter to user-selected AND applicable to this sector
    return allApplicable.filter(m => state.selectedMetrics.includes(m.key));
}


// Render stock search suggestions
function showSuggestions(query) {
    const val = query.trim().toUpperCase();
    if (!val) {
        DOM.suggestionsBox.classList.add("hidden");
        return;
    }

    const matches = state.availableSymbols.filter(symbol => 
        symbol.includes(val) && !state.selectedSymbols.includes(symbol)
    ).slice(0, 8);

    if (matches.length === 0) {
        DOM.suggestionsBox.classList.add("hidden");
        return;
    }

    DOM.suggestionsBox.innerHTML = "";
    matches.forEach(symbol => {
        const item = document.createElement("div");
        item.className = "suggestion-item";
        
        const isPrewarmed = state.cachedSymbols.has(symbol);
        const cacheLabel = isPrewarmed ? '<span class="item-desc"><i class="fa-solid fa-circle-check" style="color:#10b981;"></i> Cached</span>' : '<span class="item-desc">NSE API</span>';
        
        item.innerHTML = `<span>${symbol}</span> ${cacheLabel}`;
        item.addEventListener("click", () => {
            state.selectedSymbols.push(symbol);
            renderTags();
            DOM.symbolSearch.value = "";
            DOM.suggestionsBox.classList.add("hidden");
        });
        DOM.suggestionsBox.appendChild(item);
    });
    DOM.suggestionsBox.classList.remove("hidden");
}

// Render selected tags in the input wrapper
function renderTags() {
    DOM.selectedTags.innerHTML = "";
    state.selectedSymbols.forEach(symbol => {
        const tag = document.createElement("span");
        tag.className = "tag-badge";
        tag.innerHTML = `${symbol} <i class="fa-solid fa-xmark" data-symbol="${symbol}"></i>`;
        
        // Tag remove trigger
        tag.querySelector("i").addEventListener("click", (e) => {
            const symToRemove = e.target.getAttribute("data-symbol");
            state.selectedSymbols = state.selectedSymbols.filter(s => s !== symToRemove);
            renderTags();
        });
        
        DOM.selectedTags.appendChild(tag);
    });
}

// REST API logic
async function analyzePerformance(e) {
    if (e && typeof e.preventDefault === "function") e.preventDefault();
    if (state.selectedSymbols.length === 0) {
        alert("Please select at least one company to analyze.");
        return;
    }

    // Toggle Badges and Consoles
    DOM.logList.innerHTML = "";
    DOM.logConsole.classList.remove("hidden");
    DOM.emptyState.classList.add("hidden");
    DOM.resultsArea.classList.add("hidden");
    DOM.offlineBadge.classList.add("hidden");
    DOM.networkBadge.classList.remove("hidden");
    
    addLog(`Resolving quarter sequence for anchor ${DOM.quarterSelect.value}...`, "info");
    addLog(`Initiating financial analysis pipeline for symbols: ${state.selectedSymbols.join(", ")}`, "info");

    const query = new URLSearchParams({
        symbols: state.selectedSymbols.join(","),
        quarter: DOM.quarterSelect.value,
        back_quarters: DOM.slider.value,
        ebitda_definition: DOM.ebitdaDef.value
    });

    try {
        const response = await fetch(`/api/metrics?${query.toString()}`);
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || "Server responded with an error.");
        }

        const data = await response.json();
        state.metricsData = data;
        
        // Print download statuses
        let cacheHits = 0;
        let onlineFetches = 0;
        
        (data.downloads || []).forEach(dl => {
            const isCached = dl.source === "cached" || dl.message.toLowerCase().includes("cached");
            if (isCached) {
                cacheHits++;
                addLog(`[+] ${dl.symbol} (${dl.quarter}): Loaded from local cache file.`, "success");
            } else {
                onlineFetches++;
                addLog(`[*] ${dl.symbol} (${dl.quarter}): Fetched from remote source (${dl.message}).`, "info");
            }
        });

        addLog(`Analysis complete. Hits: ${cacheHits}, Downloads: ${onlineFetches}.`, "success");

        // Toggle Status Indicator Pill
        DOM.networkBadge.classList.add("hidden");
        if (onlineFetches === 0 && cacheHits > 0) {
            DOM.offlineBadge.classList.remove("hidden");
        } else {
            DOM.offlineBadge.classList.add("hidden");
        }

        // Render visual tables and plots
        renderResults();
        DOM.resultsArea.classList.remove("hidden");

        // Set timeout to hide log console cleanly after 5 seconds
        setTimeout(() => {
            DOM.logConsole.classList.add("hidden");
        }, 5000);

    } catch (err) {
        console.error(err);
        addLog(`[!] Calculation failure: ${err.message}`, "error");
        DOM.networkBadge.classList.add("hidden");
        DOM.offlineBadge.classList.add("hidden");
    }
}

// Logger utility
function addLog(message, type) {
    const item = document.createElement("div");
    item.className = `log-item-${type}`;
    item.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    DOM.logList.appendChild(item);
    DOM.logList.scrollTop = DOM.logList.scrollHeight;
}

// Formatting utils
const croreDiv = 10000000;
function fmtCr(val) {
    if (val === null || val === undefined) return "-";
    return (val / croreDiv).toLocaleString("en-IN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}
function fmtPct(val) {
    if (val === null || val === undefined) return "-";
    return `${val.toLocaleString("en-IN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    })}%`;
}
function fmtRaw(val) {
    if (val === null || val === undefined) return "-";
    return val.toLocaleString("en-IN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).replace(/\.00$/, "");
}

const FORMAT_FNS = { cr: fmtCr, pct: fmtPct, raw: fmtRaw };

// ─── Render Results ─────────────────────────────────────────────────────

function renderResults() {
    const data = state.metricsData;
    if (!data) return;

    // 1. Render chart (all stocks overlaid)
    plotTrendsChart();

    // 2. Build company tab bar
    DOM.companyTabBar.innerHTML = "";
    const validSymbols = data.symbols.filter(s => {
        const sq = data.metrics[s] || {};
        return Object.keys(sq).length > 0;
    });

    if (validSymbols.length === 0) return;

    // Default to first symbol if no active tab or active tab no longer valid
    if (!state.activeCompanyTab || !validSymbols.includes(state.activeCompanyTab)) {
        state.activeCompanyTab = validSymbols[0];
    }

    validSymbols.forEach(symbol => {
        const btn = document.createElement("button");
        btn.className = `company-tab-btn${symbol === state.activeCompanyTab ? ' active' : ''}`;
        btn.textContent = symbol;
        btn.addEventListener("click", () => switchCompanyTab(symbol));
        DOM.companyTabBar.appendChild(btn);
    });

    // 3. Render the active tab content
    renderCompanyTab(state.activeCompanyTab);
}

function switchCompanyTab(symbol) {
    state.activeCompanyTab = symbol;
    // Update tab bar active state
    DOM.companyTabBar.querySelectorAll(".company-tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.textContent === symbol);
    });
    renderCompanyTab(symbol);
}

function renderCompanyTab(symbol) {
    const data = state.metricsData;
    if (!data) return;

    const contentEl = DOM.companyTabContent;
    contentEl.innerHTML = "";
    // Re-trigger fade animation
    contentEl.style.animation = 'none';
    contentEl.offsetHeight; // force reflow
    contentEl.style.animation = '';

    const symbolQuarters = data.metrics[symbol] || {};
    const qtrList = Object.keys(symbolQuarters);
    if (qtrList.length === 0) return;

    const firstQtr = qtrList[0];
    const companyType = symbolQuarters[firstQtr].company_type || "manufacturing";
    const isBank = companyType === "bank";
    const visibleMetrics = getVisibleMetrics(companyType);

    // ── Metrics Table ────────────────────────────────────────────
    const tableQuarters = [...data.display_quarters].reverse();

    let headersHTML = `<th>Financial Parameter</th>`;
    tableQuarters.forEach(qtr => {
        if (symbolQuarters[qtr]) {
            headersHTML += `<th style="text-align:right;">${qtr}</th>`;
        }
    });

    let rowsHTML = "";
    visibleMetrics.forEach(m => {
        const formatFn = FORMAT_FNS[m.fmt] || fmtRaw;
        rowsHTML += renderTableRow(m.label, tableQuarters, symbolQuarters, m.key, formatFn, m.cls);
    });

    const card = document.createElement("div");
    card.className = "stock-card";
    card.innerHTML = `
        <div class="stock-card-header">
            <div class="stock-title">
                <h3>${symbol}</h3>
                <span>Sector: ${isBank ? 'Banking / Financials' : 'Manufacturing / Consumer'}</span>
            </div>
            <div class="badge badge-success" style="padding: 4px 10px; font-size: 10px; text-transform: uppercase;">
                ${symbolQuarters[firstQtr].filing_nature || 'Consolidated'}
            </div>
        </div>
        <hr class="glow-divider" style="margin-top:0;">
        <div style="overflow-x: auto;">
            <table class="metrics-table">
                <thead>
                    <tr>${headersHTML}</tr>
                </thead>
                <tbody>
                    ${rowsHTML}
                </tbody>
            </table>
        </div>
    `;
    contentEl.appendChild(card);

    // ── Corporate Actions Timeline (for this symbol only) ────────
    const timelineWrapper = document.createElement("div");
    timelineWrapper.style.marginTop = "20px";
    timelineWrapper.innerHTML = `
        <h3 style="font-family:var(--font-display); font-size:15px; font-weight:600; margin-bottom:4px;">
            <i class="fa-solid fa-timeline"></i> Corporate Actions &amp; Payout Chronology
        </h3>
        <hr class="glow-divider">
    `;
    const timelineEl = document.createElement("div");
    timelineEl.className = "actions-timeline";
    renderTimelineForSymbol(symbol, timelineEl);
    timelineWrapper.appendChild(timelineEl);
    contentEl.appendChild(timelineWrapper);
}

function renderTableRow(paramName, quarters, symbolQuarters, key, formatFn, valueClass = "") {
    let cells = `<td class="parameter-name">${paramName}</td>`;
    quarters.forEach(qtr => {
        if (symbolQuarters[qtr]) {
            const rawVal = symbolQuarters[qtr][key];
            const fmtVal = formatFn(rawVal);
            cells += `<td class="${valueClass}" style="text-align:right;">${fmtVal}</td>`;
        }
    });
    return `<tr>${cells}</tr>`;
}

// ─── Chart Plotting ─────────────────────────────────────────────────────

const CHART_COLORS = [
    { primary: "#6366f1", secondary: "rgba(99, 102, 241, 0.12)" },
    { primary: "#06b6d4", secondary: "rgba(6, 182, 212, 0.12)" },
    { primary: "#10b981", secondary: "rgba(16, 185, 129, 0.12)" },
    { primary: "#f43f5e", secondary: "rgba(244, 63, 94, 0.12)" },
    { primary: "#f59e0b", secondary: "rgba(245, 158, 11, 0.12)" },
    { primary: "#8b5cf6", secondary: "rgba(139, 92, 246, 0.12)" },
    { primary: "#ec4899", secondary: "rgba(236, 72, 153, 0.12)" },
    { primary: "#14b8a6", secondary: "rgba(20, 184, 166, 0.12)" },
];

function plotTrendsChart() {
    const data = state.metricsData;
    if (!data || !data.display_quarters) return;

    if (state.chartInstance) {
        state.chartInstance.destroy();
    }

    const labels = [...data.display_quarters].reverse(); // Oldest to newest
    const datasets = [];
    let colorIdx = 0;

    // Determine which metrics to chart based on tab + user selection
    const chartMetrics = getChartableMetrics();

    data.symbols.forEach((symbol) => {
        const symbolQuarters = data.metrics[symbol] || {};
        const companyType = Object.values(symbolQuarters)[0]?.company_type || "manufacturing";
        const sectorFilter = companyType === "bank" ? ["bank", "both"] : ["mfg", "both"];

        chartMetrics.forEach(m => {
            // Only chart metrics applicable to this company's sector
            if (!sectorFilter.includes(m.sector)) return;

            const values = labels.map(q => {
                const metrics = symbolQuarters[q];
                if (!metrics) return null;
                const raw = metrics[m.key];
                if (raw === null || raw === undefined) return null;
                // Convert Crore-formatted metrics from raw INR
                return m.fmt === "cr" ? raw / croreDiv : raw;
            });

            // Skip if all null
            if (values.every(v => v === null)) return;

            const col = CHART_COLORS[colorIdx % CHART_COLORS.length];
            colorIdx++;

            const isFill = m.chartType === "bar" || m.fmt === "cr";
            datasets.push({
                label: `${symbol} ${m.label}`,
                data: values,
                borderColor: col.primary,
                backgroundColor: isFill ? col.secondary : "transparent",
                tension: 0.3,
                fill: isFill,
                pointRadius: 3,
                pointHoverRadius: 5,
            });
        });
    });

    const ctx = document.getElementById("trends-chart").getContext("2d");
    state.chartInstance = new Chart(ctx, {
        type: "line",
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    labels: {
                        color: "#9ca3af",
                        font: { family: "Inter", weight: 600, size: 11 },
                        usePointStyle: true,
                        pointStyle: 'circle',
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(13, 18, 33, 0.92)',
                    borderColor: 'rgba(99, 102, 241, 0.3)',
                    borderWidth: 1,
                    titleFont: { family: 'Inter', weight: 600 },
                    bodyFont: { family: 'Inter' },
                    cornerRadius: 8,
                    padding: 10,
                }
            },
            scales: {
                x: {
                    grid: { color: "rgba(255,255,255,0.04)" },
                    ticks: { color: "#9ca3af" }
                },
                y: {
                    grid: { color: "rgba(255,255,255,0.04)" },
                    ticks: { color: "#9ca3af" }
                }
            }
        }
    });
}

/**
 * Determine which metrics should appear in the chart.
 * Uses the user's explicit selection, or a sensible default set.
 */
function getChartableMetrics() {
    // Sensible defaults when no metrics are selected
    const DEFAULT_CHART_KEYS = ["revenue", "total_income", "net_income"];

    if (state.selectedMetrics.length > 0) {
        return METRICS_REGISTRY.filter(m => state.selectedMetrics.includes(m.key));
    }

    return METRICS_REGISTRY.filter(m => DEFAULT_CHART_KEYS.includes(m.key));
}


// Render timeline for a single symbol into a given container element
function renderTimelineForSymbol(symbol, containerEl) {
    const data = state.metricsData;
    if (!data) return;

    containerEl.innerHTML = "";
    const events = [];
    const sortedQuarters = [...data.display_quarters].reverse();

    sortedQuarters.forEach(qtr => {
        const metrics = data.metrics[symbol]?.[qtr];
        if (!metrics) return;

        if (metrics.quarter_dividend !== null && metrics.quarter_dividend > 0) {
            events.push({
                quarter: qtr,
                type: "Dividend Payout",
                icon: "fa-hand-holding-dollar",
                color: "#10b981",
                body: `Announced quarterly dividend payout of <strong>Rs ${metrics.quarter_dividend.toFixed(2)} per share</strong>.`
            });
        }

        if (metrics.other_corporate_actions && metrics.other_corporate_actions !== "-") {
            events.push({
                quarter: qtr,
                type: "Corporate Restructuring",
                icon: "fa-building-shield",
                color: "#6366f1",
                body: `Corporate announcements: <strong>${metrics.other_corporate_actions}</strong>`
            });
        }
    });

    if (events.length === 0) {
        containerEl.innerHTML = `
            <div class="empty-state" style="padding:20px; min-height:auto;">
                <i class="fa-solid fa-bell-slash" style="font-size:24px; color:var(--text-muted);"></i>
                <p style="font-size:12px; margin-top:8px;">No dividends or restructuring actions found for ${symbol} in the selected range.</p>
            </div>
        `;
        return;
    }

    events.forEach(ev => {
        const node = document.createElement("div");
        node.className = "timeline-node";
        node.innerHTML = `
            <div class="node-header">
                <span class="node-date">${ev.quarter}</span>
                <span class="badge" style="padding: 2px 8px; font-size: 10px; background: rgba(255,255,255,0.05); color: ${ev.color}; border: 1px solid rgba(255,255,255,0.05);">
                    <i class="fa-solid ${ev.icon}"></i> ${ev.type}
                </span>
            </div>
            <div class="node-body">${ev.body}</div>
        `;
        containerEl.appendChild(node);
    });
}

// CSV Export Generator
function exportToCSV() {
    const data = state.metricsData;
    if (!data) return;

    let csvContent = "";
    
    // Headers
    csvContent += "Symbol,Quarter,Company Type,Filing Nature,Revenue (INR),EBITDA (INR),PBIT (INR),PBT (INR),Net Income (INR),Basic EPS (INR),Diluted EPS (INR),Trailing EPS (INR),Share Price (INR),P/E Ratio,P/B Ratio,Quarter Dividend (INR/sh),ROE (%),ROA (%),Equity (INR),Cost-to-Income (%),ROCE (%),Gross Profit (INR),Gross Margin (%),EBITDA Margin (%),Net Margin (%),NII (INR),Total Income (INR),PPOP (INR),Gross NPA (%),Net NPA (%)\n";
    
    data.symbols.forEach(symbol => {
        const symbolQuarters = data.metrics[symbol] || {};
        data.display_quarters.forEach(qtr => {
            const m = symbolQuarters[qtr];
            if (!m) return;

            const row = [
                symbol,
                qtr,
                m.company_type,
                m.filing_nature,
                m.revenue ?? "",
                m.ebitda ?? "",
                m.pbit ?? "",
                m.pbt ?? "",
                m.net_income ?? "",
                m.basic_eps ?? "",
                m.diluted_eps ?? "",
                m.trailing_eps ?? "",
                m.share_price ?? "",
                m.pe_ratio ?? "",
                m.pb_ratio ?? "",
                m.quarter_dividend ?? "",
                m.roe ?? "",
                m.roa ?? "",
                m.equity ?? "",
                m.cost_to_income ?? "",
                m.roce ?? "",
                m.gross_profit ?? "",
                m.gross_margin ?? "",
                m.ebitda_margin ?? "",
                m.net_margin ?? "",
                m.nii ?? "",
                m.total_income ?? "",
                m.ppop ?? "",
                m.gnpa_pct ?? "",
                m.nnpa_pct ?? ""
            ];
            
            csvContent += row.map(val => {
                if (typeof val === "string") {
                    return `"${val.replace(/"/g, '""')}"`;
                }
                return val;
            }).join(",") + "\n";
        });
    });

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `FinancialReporter_export_${DOM.quarterSelect.value}.csv`);
    link.style.visibility = "hidden";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
