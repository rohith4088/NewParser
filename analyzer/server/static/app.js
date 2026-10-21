/**
 * OnPrem Analyzer - Air-Gapped Dashboard Controller
 * Zero CDN, Pure Vanilla JavaScript
 */

(function () {
  "use strict";

  // Application State
  const state = {
    activeSessionId: null,
    metadata: null,
    minTsMicros: null,
    maxTsMicros: null,
    fromMicros: null,
    toMicros: null,
    activeOrigin: "all",
    activeLevel: "all",
    activeNamespace: "all",
    activeIssueType: "all",
    searchQuery: "",
    pageOffset: 0,
    pageLimit: 100,
    currentTab: "overview",
    // Bundle Explorer & Priority Queue State
    inventory: null,
    activePartId: null,
    selectedExplorerUnits: new Set(),
    parseQueue: [],
    queuePollingTimer: null,
    focusedExplorerUnit: null,
    // AI Copilot State
    aiChatHistory: [],
    aiIsStreaming: false,
    aiAbortController: null,
    aiStatus: { available: false, model: "qwen2.5:3b" },
    selectedAiModel: "qwen2.5:3b",
    // Timeline Hybrid State
    timelineMode: "wave",
    timelineBuckets: [],
    timelineFromMicros: null,
    timelineToMicros: null,
    // Agentic Multi-Agent Triage State
    agenticVerdicts: {},
    agenticReport: null,
    agenticIsRunning: false,
  };

  // DOM Elements
  const el = {
    // Navigation & Active Bundle Header
    activeBundleName: document.getElementById("activeBundleName"),
    activeBundleStats: document.getElementById("activeBundleStats"),
    bundlePill: document.getElementById("bundlePill"),
    btnSwitchSession: document.getElementById("btnSwitchSession"),
    btnUploadBundle: document.getElementById("btnUploadBundle"),
    btnOpenExplorerModal: document.getElementById("btnOpenExplorerModal"),
    queueNavBadge: document.getElementById("queueNavBadge"),

    // Filter controls
    inputFromTime: document.getElementById("inputFromTime"),
    inputToTime: document.getElementById("inputToTime"),
    btnApplyTimeFilter: document.getElementById("btnApplyTimeFilter"),
    btnResetTimeFilter: document.getElementById("btnResetTimeFilter"),
    bundleRangeBadge: document.getElementById("bundleRangeBadge"),
    filterZeroWarning: document.getElementById("filterZeroWarning"),
    filterZeroWarningText: document.getElementById("filterZeroWarningText"),
    btnAlertResetRange: document.getElementById("btnAlertResetRange"),
    timePresets: document.getElementById("timePresets"),
    timelineContainer: document.getElementById("timelineContainer"),
    timelineChart: document.getElementById("timelineChart"),
    timelineRangeLabel: document.getElementById("timelineRangeLabel"),
    timelineModeToggle: document.getElementById("timelineModeToggle"),
    btnTimelineModeWave: document.getElementById("btnTimelineModeWave"),
    btnTimelineModeMatrix: document.getElementById("btnTimelineModeMatrix"),
    btnTimelineResetZoom: document.getElementById("btnTimelineResetZoom"),
    timelineTooltip: document.getElementById("timelineTooltip"),

    // Scope / Origin counters & filters
    originPills: document.getElementById("originPills"),
    cntAllOrigin: document.getElementById("cntAllOrigin"),
    cntCluster: document.getElementById("cntCluster"),
    cntHost: document.getElementById("cntHost"),
    selectOrigin: document.getElementById("selectOrigin"),

    // Level counters
    cntFatal: document.getElementById("cntFatal"),
    cntError: document.getElementById("cntError"),
    cntWarn: document.getElementById("cntWarn"),
    cntInfo: document.getElementById("cntInfo"),

    // Tab buttons & views
    viewTabs: document.getElementById("viewTabs"),
    tabCountFindings: document.getElementById("tabCountFindings"),
    tabCountK8s: document.getElementById("tabCountK8s"),
    tabCountAnomalies: document.getElementById("tabCountAnomalies"),

    // View containers
    findingsList: document.getElementById("findingsList"),
    logTableBody: document.getElementById("logTableBody"),
    k8sTableBody: document.getElementById("k8sTableBody"),
    anomaliesList: document.getElementById("anomaliesList"),
    topologyGrid: document.getElementById("topologyGrid"),
    topologyIngestNotice: document.getElementById("topologyIngestNotice"),

    // Log Controls
    inputSearch: document.getElementById("inputSearch"),
    selectNamespace: document.getElementById("selectNamespace"),
    selectLevel: document.getElementById("selectLevel"),
    selectIssueType: document.getElementById("selectIssueType"),
    logCountDisplay: document.getElementById("logCountDisplay"),
    btnPrevPage: document.getElementById("btnPrevPage"),
    btnNextPage: document.getElementById("btnNextPage"),

    // Modals
    modalSwitchSession: document.getElementById("modalSwitchSession"),
    btnCloseSwitchModal: document.getElementById("btnCloseSwitchModal"),
    btnCancelSwitchModal: document.getElementById("btnCancelSwitchModal"),
    sessionList: document.getElementById("sessionList"),

    modalUploadBundle: document.getElementById("modalUploadBundle"),
    btnCloseUploadModal: document.getElementById("btnCloseUploadModal"),
    btnCancelUploadModal: document.getElementById("btnCancelUploadModal"),
    tabUploadBrowser: document.getElementById("tabUploadBrowser"),
    tabUploadDropFolder: document.getElementById("tabUploadDropFolder"),
    panelUploadBrowser: document.getElementById("panelUploadBrowser"),
    panelUploadDropFolder: document.getElementById("panelUploadDropFolder"),
    dropZone: document.getElementById("dropZone"),
    btnBrowseFiles: document.getElementById("btnBrowseFiles"),
    fileInput: document.getElementById("fileInput"),
    uploadProgressContainer: document.getElementById("uploadProgressContainer"),
    progressBarFill: document.getElementById("progressBarFill"),
    uploadProgressText: document.getElementById("uploadProgressText"),
    uploadProgressPercent: document.getElementById("uploadProgressPercent"),
    bundlesList: document.getElementById("bundlesList"),



    // Log view on-demand alert
    unparsedNsAlert: document.getElementById("unparsedNsAlert"),
    unparsedNsAlertText: document.getElementById("unparsedNsAlertText"),
    btnQuickIngestCurrentNs: document.getElementById("btnQuickIngestCurrentNs"),

    // Bundle Explorer Modal Elements
    modalBundleExplorer: document.getElementById("modalBundleExplorer"),
    btnCloseExplorerModal: document.getElementById("btnCloseExplorerModal"),
    btnDoneExplorerModal: document.getElementById("btnDoneExplorerModal"),
    explorerBundleName: document.getElementById("explorerBundleName"),
    queueProgressBarContainer: document.getElementById("queueProgressBarContainer"),
    queueProgressLabel: document.getElementById("queueProgressLabel"),
    queueProgressPercent: document.getElementById("queueProgressPercent"),
    queueProgressFill: document.getElementById("queueProgressFill"),
    partTabsBar: document.getElementById("partTabsBar"),
    inputExplorerFilter: document.getElementById("inputExplorerFilter"),
    btnSelectAllExplorer: document.getElementById("btnSelectAllExplorer"),
    btnClearExplorer: document.getElementById("btnClearExplorer"),
    btnAddSelectedToQueue: document.getElementById("btnAddSelectedToQueue"),
    explorerUnitsList: document.getElementById("explorerUnitsList"),
    queueCountBadge: document.getElementById("queueCountBadge"),
    btnClearQueue: document.getElementById("btnClearQueue"),
    btnCancelQueue: document.getElementById("btnCancelQueue"),
    queueItemsContainer: document.getElementById("queueItemsContainer"),
    btnStartParsingQueue: document.getElementById("btnStartParsingQueue"),
    btnStartParsingQueueText: document.getElementById("btnStartParsingQueueText"),
    unitDetailSection: document.getElementById("unitDetailSection"),
    unitDetailContent: document.getElementById("unitDetailContent"),
    explorerSummaryStats: document.getElementById("explorerSummaryStats"),

    // AI Copilot Elements
    btnOpenAiChat: document.getElementById("btnOpenAiChat"),
    aiStatusDot: document.getElementById("aiStatusDot"),
    aiStatusText: document.getElementById("aiStatusText"),
    aiDrawer: document.getElementById("aiDrawer"),
    aiDrawerBackdrop: document.getElementById("aiDrawerBackdrop"),
    btnCloseAiDrawer: document.getElementById("btnCloseAiDrawer"),
    btnClearAiChat: document.getElementById("btnClearAiChat"),
    aiActiveSessionName: document.getElementById("aiActiveSessionName"),
    aiMessagesContainer: document.getElementById("aiMessagesContainer"),
    aiChatForm: document.getElementById("aiChatForm"),
    aiChatInput: document.getElementById("aiChatInput"),
    btnAiSend: document.getElementById("btnAiSend"),
    aiTokenSpeed: document.getElementById("aiTokenSpeed"),
    aiQuickPrompts: document.getElementById("aiQuickPrompts"),
    aiModelSelect: document.getElementById("aiModelSelect"),
    aiHeaderModelBadge: document.getElementById("aiHeaderModelBadge"),
    aiCardModelCode: document.getElementById("aiCardModelCode"),
    aiWelcomeModelCode: document.getElementById("aiWelcomeModelCode"),

    // Agentic Triage Elements
    btnOpenAgenticTriageTop: document.getElementById("btnOpenAgenticTriageTop"),
    tabBtnAgentic: document.getElementById("tabBtnAgentic"),
    btnTriggerAgenticTriage: document.getElementById("btnTriggerAgenticTriage"),
    btnTriggerAgenticTriageText: document.getElementById("btnTriggerAgenticTriageText"),
    agenticProgressBar: document.getElementById("agenticProgressBar"),
    agenticProgressFill: document.getElementById("agenticProgressFill"),
    agenticProgressStatus: document.getElementById("agenticProgressStatus"),
    agenticCurrentStep: document.getElementById("agenticCurrentStep"),
    agenticCompletedCount: document.getElementById("agenticCompletedCount"),
    agenticPatientZeroCard: document.getElementById("agenticPatientZeroCard"),
    pzBadge: document.getElementById("pzBadge"),
    pzTimestamp: document.getElementById("pzTimestamp"),
    pzComponent: document.getElementById("pzComponent"),
    pzSummary: document.getElementById("pzSummary"),
    agenticCascadeContainer: document.getElementById("agenticCascadeContainer"),
    agenticCascadeList: document.getElementById("agenticCascadeList"),
    agenticRunbookContainer: document.getElementById("agenticRunbookContainer"),
    agenticRunbookContent: document.getElementById("agenticRunbookContent"),
    btnCopyAgenticPlaybook: document.getElementById("btnCopyAgenticPlaybook"),
    agenticSubAgentsGrid: document.getElementById("agenticSubAgentsGrid"),
    agenticSubAgentsPlaceholder: document.getElementById("agenticSubAgentsPlaceholder"),
    modalAgenticEvidence: document.getElementById("modalAgenticEvidence"),
    btnCloseAgenticModal: document.getElementById("btnCloseAgenticModal"),
    btnDismissAgenticModal: document.getElementById("btnDismissAgenticModal"),
    modalAgentTitle: document.getElementById("modalAgentTitle"),
    modalAgentSubtitle: document.getElementById("modalAgentSubtitle"),
    modalAgentBody: document.getElementById("modalAgentBody"),
  };

  // --- Initializer -----------------------------------------------------------

  async function init() {
    setupEventListeners();
    await checkSessions();
    checkAiStatus();
  }

  // --- Session Management ---------------------------------------------------

  let liveIngestTimer = null;

  async function checkSessions() {
    try {
      const res = await fetch("/api/sessions");
      const data = await res.json();
      if (data.active_session) {
        state.activeSessionId = data.active_session;
        await loadActiveSession();
      } else if (data.sessions && data.sessions.length > 0) {
        // Switch to first available
        await switchSession(data.sessions[0].id);
      } else {
        showNoSessionsState();
      }
    } catch (err) {
      console.error("Failed to load sessions:", err);
      showNoSessionsState();
    }
  }

  async function loadActiveSession() {
    try {
      const res = await fetch("/api/metadata");
      if (!res.ok) throw new Error("Metadata request failed");
      let meta = await res.json();
      state.metadata = meta;
      if (el.aiActiveSessionName) {
        el.aiActiveSessionName.textContent = state.activeSessionId || meta.session_id || "Active Bundle";
      }

      // Query live stats to get real-time event counts and time bounds while DuckDB builds
      let liveTotal = meta.total_events || 0;
      let minTs = meta.min_ts_micros;
      let maxTs = meta.max_ts_micros;

      try {
        const statsRes = await fetch("/api/stats");
        if (statsRes.ok) {
          const statsData = await statsRes.json();
          const lvls = statsData.levels || {};
          const sumEvents = Object.values(lvls).reduce((a, b) => a + b, 0);
          if (sumEvents > liveTotal) liveTotal = sumEvents;
          if (!minTs && statsData.min_ts_micros) minTs = statsData.min_ts_micros;
          if (!maxTs && statsData.max_ts_micros) maxTs = statsData.max_ts_micros;
        }
      } catch (e) {
        // ignore
      }

      const isBuilding = !meta.scanned_at || !meta.total_events || meta.scanned_at === "Parsing in progress...";

      el.activeBundleName.textContent = meta.filename || `${state.activeSessionId}.tar`;
      if (isBuilding && liveTotal > 0) {
        el.activeBundleStats.innerHTML = `<span style="color:var(--color-primary);font-weight:600;">Ingesting... ${liveTotal.toLocaleString()} events indexed</span>`;
        if (!liveIngestTimer) {
          liveIngestTimer = setInterval(async () => {
            const check = await fetch("/api/metadata");
            if (check.ok) {
              const m = await check.json();
              if (m.scanned_at && m.total_events) {
                clearInterval(liveIngestTimer);
                liveIngestTimer = null;
              }
            }
            await loadActiveSession();
          }, 4000);
        }
      } else {
        if (liveIngestTimer) {
          clearInterval(liveIngestTimer);
          liveIngestTimer = null;
        }
        el.activeBundleStats.textContent = `${(meta.total_events || liveTotal || 0).toLocaleString()} events (${(meta.total_files || 0).toLocaleString()} files)`;
      }

      state.minTsMicros = minTs;
      state.maxTsMicros = maxTs;

      // Default window to full range if not set or out of bounds
      if (!state.fromMicros || (minTs && state.fromMicros < minTs) || (maxTs && state.fromMicros > maxTs)) {
        state.fromMicros = state.minTsMicros;
      }
      if (!state.toMicros || (maxTs && state.toMicros > maxTs) || (minTs && state.toMicros < minTs)) {
        state.toMicros = state.maxTsMicros;
      }

      syncTimeInputFields();
      await refreshAllViews();
      await checkQueueStatus();
    } catch (err) {
      console.error("Error loading active session:", err);
    }
  }

  async function switchSession(sessionId) {
    try {
      const res = await fetch("/api/sessions/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
      if (res.ok) {
        closeModals();
        state.activeSessionId = sessionId;
        state.pageOffset = 0;
        state.fromMicros = null;
        state.toMicros = null;
        await loadActiveSession();
      }
    } catch (err) {
      alert("Failed to switch session: " + err.message);
    }
  }

  function showNoSessionsState() {
    el.activeBundleName.textContent = "No Bundle Loaded";
    el.activeBundleStats.textContent = "Upload or select a bundle to begin";
    openUploadModal();
  }

  // --- Time-Window Management & UTC Formatting ------------------------------

  function formatTimestamp(tsIso, tsMicros) {
    if (tsIso) {
      // Clean display: "2026-08-04 02:45:07 UTC" (no raw 'T' and no fractional decimals)
      return tsIso.slice(0, 19).replace("T", " ") + " UTC";
    }
    if (tsMicros) {
      const d = new Date(tsMicros / 1000);
      if (!isNaN(d.getTime())) {
        return d.toISOString().slice(0, 19).replace("T", " ") + " UTC";
      }
    }
    return "N/A";
  }

  function formatUtcDatetime(micros) {
    if (!micros) return "";
    const date = new Date(micros / 1000);
    if (isNaN(date.getTime())) return "";
    return date.toISOString().slice(0, 19).replace("T", " ");
  }

  function parseUtcDatetime(str) {
    if (!str || !str.trim()) return null;
    let s = str.trim();
    if (!s.includes("T") && s.includes(" ")) {
      s = s.replace(" ", "T");
    }
    if (!s.endsWith("Z")) {
      s += "Z";
    }
    const d = new Date(s);
    if (isNaN(d.getTime())) return null;
    return Math.floor(d.getTime() * 1000);
  }

  function syncTimeInputFields() {
    if (el.inputFromTime) el.inputFromTime.value = formatUtcDatetime(state.fromMicros);
    if (el.inputToTime) el.inputToTime.value = formatUtcDatetime(state.toMicros);
    if (el.bundleRangeBadge && state.minTsMicros && state.maxTsMicros) {
      el.bundleRangeBadge.textContent = `Span: ${formatUtcDatetime(state.minTsMicros)} → ${formatUtcDatetime(state.maxTsMicros)} UTC`;
    }
  }

  async function applyTimeFilter() {
    const fromVal = el.inputFromTime.value;
    const toVal = el.inputToTime.value;
    const parsedFrom = parseUtcDatetime(fromVal);
    const parsedTo = parseUtcDatetime(toVal);

    state.fromMicros = (parsedFrom !== null) ? parsedFrom : state.minTsMicros;
    state.toMicros = (parsedTo !== null) ? parsedTo : state.maxTsMicros;
    state.pageOffset = 0;

    syncTimeInputFields();
    document.querySelectorAll(".preset-btn").forEach((b) => b.classList.remove("active"));
    await refreshAllViews();
  }

  async function resetTimeFilter() {
    state.fromMicros = state.minTsMicros;
    state.toMicros = state.maxTsMicros;
    state.pageOffset = 0;
    syncTimeInputFields();

    if (el.filterZeroWarning) el.filterZeroWarning.style.display = "none";
    document.querySelectorAll(".preset-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.window === "all");
    });
    await refreshAllViews();
  }

  function applyPresetWindow(preset) {
    if (preset === "all" || !state.maxTsMicros) {
      resetTimeFilter();
      return;
    }
    const msMap = {
      "15m": 15 * 60 * 1000,
      "1h": 60 * 60 * 1000,
      "6h": 6 * 60 * 60 * 1000,
      "24h": 24 * 60 * 60 * 1000,
    };
    const spanMs = msMap[preset] || 0;
    const endMicros = state.maxTsMicros;
    const startMicros = Math.max(state.minTsMicros || 0, endMicros - spanMs * 1000);

    state.fromMicros = startMicros;
    state.toMicros = endMicros;
    state.pageOffset = 0;
    syncTimeInputFields();

    document.querySelectorAll(".preset-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.window === preset);
    });
    refreshAllViews();
  }

  // --- View Refreshers -------------------------------------------------------

  async function refreshAllViews() {
    const queryParams = getTimeQueryParams();
    await Promise.all([
      loadStats(queryParams),
      loadTimeline(queryParams),
      loadFindings(queryParams),
      loadK8sFacts(),
      loadAnomalies(queryParams),
      loadEvents(),
    ]);
  }

  function getTimeQueryParams() {
    const p = new URLSearchParams();
    if (state.fromMicros) p.set("from", state.fromMicros);
    if (state.toMicros) p.set("to", state.toMicros);
    return p.toString();
  }

  // Selected namespaces set for selective ingestion modal
  const selectedNamespacesForIngest = new Set();
  let focusedNamespace = null;

  async function fetchBundleNamespaces() {
    try {
      const res = await fetch("/api/bundle/namespaces");
      if (res.ok) {
        state.catalogData = await res.json();
        return state.catalogData;
      }
    } catch (e) {
      console.error("Failed to fetch bundle namespaces:", e);
    }
    return null;
  }

  async function loadStats(queryParams) {
    try {
      const res = await fetch(`/api/stats?${queryParams}`);
      if (!res.ok) return;
      const data = await res.json();

      const lvls = data.levels || {};
      el.cntFatal.textContent = (lvls.FATAL || 0).toLocaleString();
      el.cntError.textContent = (lvls.ERROR || 0).toLocaleString();
      el.cntWarn.textContent = (lvls.WARN || 0).toLocaleString();
      el.cntInfo.textContent = (lvls.INFO || 0).toLocaleString();

      const origins = data.origins || { cluster: 0, host: 0 };
      const clusterCnt = origins.cluster || 0;
      const hostCnt = origins.host || 0;
      if (el.cntCluster) el.cntCluster.textContent = clusterCnt.toLocaleString();
      if (el.cntHost) el.cntHost.textContent = hostCnt.toLocaleString();
      if (el.cntAllOrigin) el.cntAllOrigin.textContent = (clusterCnt + hostCnt).toLocaleString();

      const totalInWindow = Object.values(lvls).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);

      // Show in-place warning if filter window selected 0 events
      if (el.filterZeroWarning) {
        if (totalInWindow === 0 && (state.fromMicros || state.toMicros)) {
          el.filterZeroWarning.style.display = "flex";
          if (el.filterZeroWarningText) {
            el.filterZeroWarningText.textContent = `No events found between ${formatUtcDatetime(state.fromMicros)} and ${formatUtcDatetime(state.toMicros)} UTC. The support bundle covers ${formatUtcDatetime(state.minTsMicros)} to ${formatUtcDatetime(state.maxTsMicros)} UTC.`;
          }
        } else {
          el.filterZeroWarning.style.display = "none";
        }
      }

      state.activeNamespaces = data.namespaces || {};
      const catalogData = await fetchBundleNamespaces();

      // Populate Namespace select & Topology grid
      updateNamespaceOptions(state.activeNamespaces, catalogData);
      renderTopologyGrid(state.activeNamespaces, totalInWindow, catalogData);
    } catch (err) {
      console.error("Failed to load stats:", err);
    }
  }

  function updateNamespaceOptions(activeNamespaces, catalogData) {
    const curr = el.selectNamespace.value;
    el.selectNamespace.innerHTML = `<option value="all">All Namespaces</option>`;

    const presentNs = new Set(Object.keys(activeNamespaces || {}));

    // Group 1: Ingested namespaces
    if (presentNs.size > 0) {
      const ingestedGroup = document.createElement("optgroup");
      ingestedGroup.label = `Ingested Namespaces (${presentNs.size})`;
      for (const ns of Object.keys(activeNamespaces).sort()) {
        const opt = document.createElement("option");
        opt.value = ns;
        opt.textContent = `${ns} (${activeNamespaces[ns].toLocaleString()} events)`;
        ingestedGroup.appendChild(opt);
      }
      el.selectNamespace.appendChild(ingestedGroup);
    }

    // Group 2: Available namespaces from catalog (not yet ingested)
    const availableGroup = document.createElement("optgroup");
    availableGroup.label = "Available for On-Demand Ingestion (Parse on demand)";
    let availableCount = 0;

    const allCatalogNamespaces = [];
    if (catalogData && catalogData.categories) {
      Object.values(catalogData.categories).forEach((items) => {
        items.forEach((item) => allCatalogNamespaces.push(item));
      });
    }

    allCatalogNamespaces
      .filter((item) => !presentNs.has(item.namespace))
      .sort((a, b) => a.namespace.localeCompare(b.namespace))
      .forEach((item) => {
        availableCount++;
        const opt = document.createElement("option");
        opt.value = item.namespace;
        opt.textContent = `${item.namespace} (Available on-demand)`;
        availableGroup.appendChild(opt);
      });

    if (availableCount > 0) {
      el.selectNamespace.appendChild(availableGroup);
    }

    el.selectNamespace.value = curr || "all";
    checkUnparsedAlert();
  }

  function checkUnparsedAlert() {
    const selected = el.selectNamespace.value;
    if (!el.unparsedNsAlert) return;

    if (selected === "all") {
      el.unparsedNsAlert.style.display = "none";
      return;
    }

    const isIngested = state.activeNamespaces && (state.activeNamespaces[selected] || 0) > 0;
    if (!isIngested) {
      el.unparsedNsAlert.style.display = "flex";
      if (el.unparsedNsAlertText) {
        el.unparsedNsAlertText.innerHTML = `Namespace <strong>${escapeHtml(selected)}</strong> is in the support bundle archive but has not been parsed yet.`;
      }
      if (el.btnQuickIngestCurrentNs) {
        el.btnQuickIngestCurrentNs.textContent = `Parse ${selected} Now (~5s)`;
        el.btnQuickIngestCurrentNs.onclick = () => executeSelectiveIngest([selected]);
      }
    } else {
      el.unparsedNsAlert.style.display = "none";
    }
  }

  function renderTopologyGrid(activeNamespaces, totalInWindow, catalogData) {
    el.topologyGrid.innerHTML = "";

    const activeSet = new Set(Object.keys(activeNamespaces || {}));
    const totalEvents = Object.values(activeNamespaces || {}).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);

    // Update banner in view header
    if (el.topologyIngestNotice) {
      el.topologyIngestNotice.innerHTML = `
        <span style="display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <strong>Session Ingestion:</strong> ${totalEvents.toLocaleString()} events across ${activeSet.size} namespace(s).
          <button class="btn btn-xs btn-accent" id="btnNoticeIngestMore">+ Ingest More Namespaces</button>
        </span>
      `;
      const btnNotice = document.getElementById("btnNoticeIngestMore");
      if (btnNotice) btnNotice.onclick = () => openSelectiveModal();
    }

    const categories = (catalogData && catalogData.categories) ? catalogData.categories : {};

    for (const [catName, items] of Object.entries(categories)) {
      const group = document.createElement("div");
      group.className = "topology-category-group";

      const title = document.createElement("div");
      title.className = "topology-category-title";
      const activeCount = items.filter((n) => activeSet.has(n.namespace)).length;
      title.innerHTML = `
        <span>${escapeHtml(catName)}</span>
        <span style="font-size:11px;font-weight:normal;color:var(--text-dim);">${activeCount}/${items.length} Ingested</span>
      `;
      group.appendChild(title);

      const grid = document.createElement("div");
      grid.className = "topology-grid";

      items.forEach((item) => {
        const count = activeNamespaces[item.namespace] || 0;
        const isActive = (count > 0);
        const card = document.createElement("div");
        card.className = `topology-card ${isActive ? "active-ns" : "pending-ns"}`;

        const badgeHtml = isActive
          ? `<span class="badge airgap-badge">${count.toLocaleString()} EVENTS</span>`
          : `<span class="badge badge-available">ON-DEMAND</span>`;

        let relatedHtml = "";
        if (item.related && item.related.length > 0) {
          const relChips = item.related.slice(0, 4).map((r) => {
            const rActive = activeSet.has(r.namespace);
            return `<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;background:${rActive ? 'rgba(16,185,129,0.15)' : 'rgba(255,255,255,0.06)'};color:${rActive ? '#34d399' : 'var(--text-dim)'};" title="${escapeHtml(r.reason || "")}">${escapeHtml(r.namespace)}</span>`;
          }).join(" ");
          relatedHtml = `<div style="font-size:11px;color:var(--text-dim);margin-top:6px;display:flex;align-items:center;gap:4px;flex-wrap:wrap;">Dependencies: ${relChips}</div>`;
        }

        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
            <strong style="font-size:13px;color:${isActive ? '#fff' : 'var(--text-muted)'};">${escapeHtml(item.namespace)}</strong>
            ${badgeHtml}
          </div>
          <div style="font-size:12px;color:var(--text-muted);margin-top:6px;line-height:1.4;">
            ${escapeHtml(item.description || "")}
          </div>
          ${relatedHtml}
          <div class="topology-card-actions">
            ${isActive ? `
              <button class="btn btn-xs btn-outline btn-filter-logs">Filter Logs →</button>
              <button class="btn btn-xs btn-outline btn-view-impact">Inspect Impact</button>
            ` : `
              <button class="btn btn-xs btn-primary btn-parse-now">Parse Namespace (~5s)</button>
              <button class="btn btn-xs btn-outline btn-view-impact">View Impact</button>
            `}
          </div>
        `;

        const btnFilter = card.querySelector(".btn-filter-logs");
        if (btnFilter) {
          btnFilter.onclick = (e) => {
            e.stopPropagation();
            el.selectNamespace.value = item.namespace;
            state.activeNamespace = item.namespace;
            switchTab("logs");
            loadEvents();
          };
        }

        const btnParse = card.querySelector(".btn-parse-now");
        if (btnParse) {
          btnParse.onclick = (e) => {
            e.stopPropagation();
            executeSelectiveIngest([item.namespace]);
          };
        }

        const btnImpact = card.querySelector(".btn-view-impact");
        if (btnImpact) {
          btnImpact.onclick = (e) => {
            e.stopPropagation();
            openSelectiveModal(item.namespace);
          };
        }

        grid.appendChild(card);
      });

      group.appendChild(grid);
      el.topologyGrid.appendChild(group);
    }
  }

  // --- Selective Ingestion & Bundle Explorer Controller ---------------------

  async function openSelectiveModal(initialFocusNs = null) {
    await openExplorerModal(null, initialFocusNs);
  }

  function closeSelectiveModal() {
    closeExplorerModal();
  }

  function renderSelectiveList(filterText = "") {
    if (!el.selectiveList) return;
    el.selectiveList.innerHTML = "";
    const catalog = state.catalogData;
    if (!catalog || !catalog.categories) return;

    const term = filterText.toLowerCase().trim();
    let totalVisible = 0;

    for (const [catName, items] of Object.entries(catalog.categories)) {
      const filteredItems = items.filter(
        (it) => !term || it.namespace.toLowerCase().includes(term) || (it.description && it.description.toLowerCase().includes(term))
      );
      if (filteredItems.length === 0) continue;

      totalVisible += filteredItems.length;

      const group = document.createElement("div");
      group.className = "selective-cat-group";

      const title = document.createElement("div");
      title.className = "selective-cat-title";
      title.textContent = catName;
      group.appendChild(title);

      filteredItems.forEach((item) => {
        const isIngested = item.ingested;
        const isSelected = selectedNamespacesForIngest.has(item.namespace);
        const isFocused = focusedNamespace === item.namespace;

        const row = document.createElement("div");
        row.className = `selective-item ${isSelected ? "selected" : ""} ${isFocused ? "active-focus" : ""}`;
        row.dataset.ns = item.namespace;

        row.innerHTML = `
          <div class="selective-item-left">
            <input type="checkbox" class="selective-cb" ${isSelected ? "checked" : ""} />
            <div class="selective-item-info">
              <span class="selective-item-name">${escapeHtml(item.namespace)}</span>
              <span class="selective-item-desc">${escapeHtml(item.description || "")}</span>
            </div>
          </div>
          <div class="selective-item-right">
            ${isIngested
              ? `<span class="badge-ingested">${item.event_count.toLocaleString()}</span>`
              : `<span class="badge-available">Available</span>`}
          </div>
        `;

        const cb = row.querySelector(".selective-cb");
        cb.addEventListener("change", (e) => {
          e.stopPropagation();
          if (cb.checked) {
            selectedNamespacesForIngest.add(item.namespace);
            row.classList.add("selected");
          } else {
            selectedNamespacesForIngest.delete(item.namespace);
            row.classList.remove("selected");
          }
          updateSelectiveFooter();
        });

        row.addEventListener("click", () => {
          focusedNamespace = item.namespace;
          document.querySelectorAll(".selective-item").forEach((r) => r.classList.remove("active-focus"));
          row.classList.add("active-focus");
          showNamespaceImpact(item.namespace);
        });

        group.appendChild(row);
      });

      el.selectiveList.appendChild(group);
    }

    if (totalVisible === 0) {
      el.selectiveList.innerHTML = `<div class="table-empty">No namespaces match "${escapeHtml(filterText)}"</div>`;
    }

    updateSelectiveFooter();
  }

  function updateSelectiveFooter() {
    if (!el.selectiveCountSelected) return;
    const count = selectedNamespacesForIngest.size;
    el.selectiveCountSelected.textContent = `${count} namespace${count === 1 ? "" : "s"} selected`;
    if (el.btnExecuteSelectiveIngest) {
      el.btnExecuteSelectiveIngest.disabled = (count === 0);
    }
  }

  async function showNamespaceImpact(nsName) {
    if (!el.selectiveImpactPanel) return;
    focusedNamespace = nsName;
    el.selectiveImpactPanel.innerHTML = `<div class="loading-state">Analyzing dependencies for ${escapeHtml(nsName)}...</div>`;

    try {
      const res = await fetch(`/api/topology/related?namespace=${encodeURIComponent(nsName)}`);
      if (!res.ok) throw new Error("Topology fetch failed");
      const data = await res.json();

      const related = data.related || [];
      const hasDeps = related.length > 0;

      let depsHtml = "";
      if (hasDeps) {
        depsHtml = related.map((dep) => {
          const isDepIngested = dep.ingested;
          const isDepSelected = selectedNamespacesForIngest.has(dep.namespace);
          return `
            <div class="impact-dep-card">
              <div class="impact-dep-head">
                <span class="impact-dep-name">${escapeHtml(dep.namespace)}</span>
                <span class="${isDepIngested ? 'badge-ingested' : 'badge-available'}">
                  ${isDepIngested ? 'Ingested' : 'Pending'}
                </span>
              </div>
              <div class="impact-dep-reason">${escapeHtml(dep.reason || "")}</div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;">
                <span class="impact-dep-category">${escapeHtml(dep.category || "")}</span>
                <button class="btn btn-xs ${isDepSelected ? 'btn-outline' : 'btn-accent'} btn-add-dep" data-dep="${escapeHtml(dep.namespace)}">
                  ${isDepSelected ? 'Selected' : '+ Add to Selection'}
                </button>
              </div>
            </div>
          `;
        }).join("");
      } else {
        depsHtml = `
          <div style="color:var(--text-muted);font-size:12px;padding:12px;background:var(--bg-input);border-radius:var(--radius-sm);line-height:1.4;">
            No explicit downstream dependencies defined. This component operates independently or interacts dynamically via the cluster API.
          </div>
        `;
      }

      el.selectiveImpactPanel.innerHTML = `
        <div class="impact-header">
          <div class="impact-target-title">
            <span class="impact-target-name">${escapeHtml(data.namespace)}</span>
            <span class="${data.ingested ? 'badge-ingested' : 'badge-available'}">
              ${data.ingested ? `Ingested (${data.event_count.toLocaleString()} events)` : 'Available on-demand'}
            </span>
          </div>
          <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px;">${escapeHtml(data.category)}</div>
          <div class="impact-desc">${escapeHtml(data.description || "Platform component")}</div>
        </div>

        <div class="impact-section-title">
          <span>Affected Dependencies & Related Services (${related.length})</span>
          ${hasDeps ? `<button class="btn btn-xs btn-outline" id="btnAddAllDeps">+ Add All to Ingestion</button>` : ""}
        </div>

        <div class="impact-dep-list">
          ${depsHtml}
        </div>
      `;

      el.selectiveImpactPanel.querySelectorAll(".btn-add-dep").forEach((btn) => {
        btn.onclick = () => {
          const depNs = btn.dataset.dep;
          selectedNamespacesForIngest.add(depNs);
          renderSelectiveList(el.inputSelectiveFilter ? el.inputSelectiveFilter.value : "");
          showNamespaceImpact(nsName);
        };
      });

      const btnAddAll = el.selectiveImpactPanel.querySelector("#btnAddAllDeps");
      if (btnAddAll) {
        btnAddAll.onclick = () => {
          related.forEach((r) => selectedNamespacesForIngest.add(r.namespace));
          selectedNamespacesForIngest.add(nsName);
          renderSelectiveList(el.inputSelectiveFilter ? el.inputSelectiveFilter.value : "");
          showNamespaceImpact(nsName);
        };
      }
    } catch (err) {
      console.error("Failed to load impact details:", err);
      el.selectiveImpactPanel.innerHTML = `<div class="table-empty">Could not load dependency impact.</div>`;
    }
  }

  async function executeSelectiveIngest(namespacesList) {
    if (!state.activeSessionId || !namespacesList || namespacesList.length === 0) return;
    const units = namespacesList.map((ns) => (ns.startsWith("cluster:") || ns.startsWith("host") ? ns : `cluster:services:${ns}`));
    addToQueue(units);
    await openExplorerModal(null, namespacesList[0]);
    await startParsingQueue();
  }

  // --- Bundle Explorer & Priority Queue Controller ----------------------------

  async function openExplorerModal(preloadedInventory = null, initialFilter = null) {
    if (!el.modalBundleExplorer) return;
    closeModals();
    el.modalBundleExplorer.classList.add("active");

    if (preloadedInventory) {
      state.inventory = preloadedInventory;
    } else {
      await fetchInventory();
    }

    if (!state.inventory || !state.inventory.parts || state.inventory.parts.length === 0) {
      if (el.explorerUnitsList) {
        el.explorerUnitsList.innerHTML = `<div class="table-empty">No bundle parts discovered. Please upload or analyze a bundle.</div>`;
      }
      return;
    }

    if (el.explorerBundleName) {
      el.explorerBundleName.textContent = state.activeSessionId || "Active Bundle";
    }

    if (!state.activePartId || !state.inventory.parts.some((p) => p.id === state.activePartId)) {
      state.activePartId = state.inventory.parts[0].id;
    }

    if (initialFilter && el.inputExplorerFilter) {
      el.inputExplorerFilter.value = initialFilter;
    }

    await checkQueueStatus();

    renderExplorerParts();
    renderExplorerUnits(el.inputExplorerFilter ? el.inputExplorerFilter.value : "");
    updateExplorerSummary();

    if (el.explorerUnitsList) el.explorerUnitsList.scrollTop = 0;
    const rightCol = el.modalBundleExplorer ? el.modalBundleExplorer.querySelector(".explorer-col-right") : null;
    if (rightCol) rightCol.scrollTop = 0;
  }

  function closeExplorerModal() {
    if (el.modalBundleExplorer) el.modalBundleExplorer.classList.remove("active");
  }

  async function fetchInventory() {
    try {
      if (el.explorerUnitsList) {
        el.explorerUnitsList.innerHTML = `<div class="loading-state">Inspecting bundle inventory...</div>`;
      }
      const res = await fetch(`/api/inventory?session_id=${encodeURIComponent(state.activeSessionId || "")}`);
      if (!res.ok) throw new Error("Failed to load inventory");
      state.inventory = await res.json();
    } catch (err) {
      console.error("Inventory fetch error:", err);
      state.inventory = null;
    }
  }

  function updateExplorerSummary() {
    if (!state.inventory || !el.explorerSummaryStats) return;
    const partsCount = state.inventory.parts ? state.inventory.parts.length : 0;
    let unitsCount = 0;
    (state.inventory.parts || []).forEach((p) => {
      (p.groups || []).forEach((g) => {
        unitsCount += (g.units || []).length;
      });
    });
    el.explorerSummaryStats.innerHTML = `<strong>${partsCount}</strong> parts discovered &bull; <strong>${unitsCount}</strong> units mapped dynamically`;
  }

  function renderExplorerParts() {
    if (!el.partTabsBar || !state.inventory || !state.inventory.parts) return;
    el.partTabsBar.innerHTML = "";

    state.inventory.parts.forEach((part) => {
      const btn = document.createElement("button");
      btn.className = `part-tab-btn ${part.id === state.activePartId ? "active" : ""}`;
      btn.dataset.partId = part.id;

      let totalUnits = 0;
      (part.groups || []).forEach((g) => { totalUnits += (g.units || []).length; });

      btn.innerHTML = `
        <span>${escapeHtml(part.name)}</span>
        <span class="part-tab-count">${totalUnits} units</span>
      `;

      btn.onclick = () => {
        state.activePartId = part.id;
        document.querySelectorAll(".part-tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.partId === part.id));
        renderExplorerUnits(el.inputExplorerFilter ? el.inputExplorerFilter.value : "");
      };

      el.partTabsBar.appendChild(btn);
    });
  }

  function renderExplorerUnits(filterText = "") {
    if (!el.explorerUnitsList || !state.inventory || !state.inventory.parts) return;
    el.explorerUnitsList.innerHTML = "";

    const currentPart = state.inventory.parts.find((p) => p.id === state.activePartId);
    if (!currentPart || !currentPart.groups) {
      el.explorerUnitsList.innerHTML = `<div class="table-empty">No components found in this part.</div>`;
      return;
    }

    const term = filterText.toLowerCase().trim();
    let totalRendered = 0;

    currentPart.groups.forEach((group) => {
      const filteredUnits = (group.units || []).filter((u) => {
        if (!term) return true;
        return (
          u.name.toLowerCase().includes(term) ||
          u.id.toLowerCase().includes(term) ||
          (u.namespace && u.namespace.toLowerCase().includes(term)) ||
          (u.source_type && u.source_type.toLowerCase().includes(term)) ||
          (u.containers && u.containers.some((c) => c.toLowerCase().includes(term)))
        );
      });

      if (filteredUnits.length === 0) return;
      totalRendered += filteredUnits.length;

      const groupCard = document.createElement("div");
      groupCard.className = "explorer-group-card";

      const head = document.createElement("div");
      head.className = "explorer-group-head";
      head.innerHTML = `
        <span class="explorer-group-title">
          <span>${escapeHtml(group.name)}</span>
          <span class="badge">${filteredUnits.length}</span>
        </span>
      `;
      groupCard.appendChild(head);

      filteredUnits.forEach((unit) => {
        const isSelected = state.selectedExplorerUnits.has(unit.id);
        const isFocused = state.focusedExplorerUnit && state.focusedExplorerUnit.id === unit.id;
        const inQueue = state.parseQueue.includes(unit.id);
        const queueIdx = state.parseQueue.indexOf(unit.id);

        const card = document.createElement("div");
        card.className = `explorer-unit-card ${isSelected ? "selected" : ""} ${isFocused ? "active-focus" : ""}`;
        card.dataset.unitId = unit.id;

        const sizeMb = (unit.total_bytes / (1024 * 1024)).toFixed(2);
        const sizeStr = sizeMb >= 0.1 ? `${sizeMb} MB` : `${Math.round(unit.total_bytes / 1024)} KB`;

        let containersHtml = "";
        if (unit.containers && unit.containers.length > 0) {
          const sample = unit.containers.slice(0, 3);
          const more = unit.containers.length > 3 ? `+${unit.containers.length - 3}` : "";
          containersHtml = `
            <div class="unit-containers-preview">
              ${sample.map((c) => `<span class="unit-container-tag">${escapeHtml(c)}</span>`).join("")}
              ${more ? `<span class="unit-container-tag" style="background:rgba(255,255,255,0.08);">${more}</span>` : ""}
            </div>
          `;
        }

        let statusBadge = "";
        if (unit.parsed) {
          statusBadge = `<span class="status-badge completed" title="Parsed into DuckDB">${unit.event_count.toLocaleString()} evts</span>`;
        } else if (inQueue) {
          statusBadge = `<span class="status-badge pending" title="In Priority Queue at #${queueIdx + 1}">#${queueIdx + 1} Queued</span>`;
        } else {
          statusBadge = `<span class="status-badge" style="background:rgba(255,255,255,0.06);color:var(--text-dim);">Available</span>`;
        }

        card.innerHTML = `
          <div class="unit-left-wrap">
            <input type="checkbox" class="explorer-cb" ${isSelected ? "checked" : ""} />
            <div class="unit-name-box">
              <div class="unit-title-line">
                <span class="unit-name">${escapeHtml(unit.name)}</span>
                ${statusBadge}
              </div>
              <div class="unit-badges-line">
                <span class="unit-meta-pill">${escapeHtml(unit.source_type || "log")}</span>
                <span class="unit-meta-pill">${unit.file_count.toLocaleString()} files</span>
                <span class="unit-meta-pill">${sizeStr}</span>
                ${unit.date_min ? `<span class="unit-date-pill">${unit.date_min}..${unit.date_max || ""}</span>` : ""}
              </div>
              ${containersHtml}
            </div>
          </div>
          <div class="unit-right-actions">
            <button class="btn btn-xs ${inQueue ? 'btn-outline' : 'btn-accent'} btn-add-single" title="${inQueue ? 'Already in queue' : 'Add to Parse Queue'}">
              ${inQueue ? 'Queued' : '+ Add'}
            </button>
          </div>
        `;

        const cb = card.querySelector(".explorer-cb");
        cb.addEventListener("change", (e) => {
          e.stopPropagation();
          if (cb.checked) {
            state.selectedExplorerUnits.add(unit.id);
            card.classList.add("selected");
          } else {
            state.selectedExplorerUnits.delete(unit.id);
            card.classList.remove("selected");
          }
        });

        const btnAdd = card.querySelector(".btn-add-single");
        btnAdd.addEventListener("click", (e) => {
          e.stopPropagation();
          if (!state.parseQueue.includes(unit.id)) {
            addToQueue([unit.id]);
          }
        });

        card.addEventListener("click", () => {
          state.focusedExplorerUnit = unit;
          document.querySelectorAll(".explorer-unit-card").forEach((c) => c.classList.remove("active-focus"));
          card.classList.add("active-focus");
          showUnitDetail(unit);
        });

        groupCard.appendChild(card);
      });

      el.explorerUnitsList.appendChild(groupCard);
    });

    if (totalRendered === 0) {
      el.explorerUnitsList.innerHTML = `<div class="table-empty">No components match "${escapeHtml(filterText)}"</div>`;
    }
  }

  async function showUnitDetail(unit) {
    if (!el.unitDetailContent) return;
    const sizeMb = (unit.total_bytes / (1024 * 1024)).toFixed(2);
    const inQueue = state.parseQueue.includes(unit.id);

    let relatedHtml = "";
    if (unit.namespace) {
      try {
        const res = await fetch(`/api/topology/related?namespace=${encodeURIComponent(unit.namespace)}`);
        if (res.ok) {
          const topo = await res.json();
          if (topo.related && topo.related.length > 0) {
            relatedHtml = `
              <div class="detail-deps-box">
                <span class="detail-label" style="font-weight:600;">Downstream / Upstream Dependencies:</span>
                <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">
                  ${topo.related.map((r) => `
                    <span class="unit-container-tag" style="background:rgba(99,102,241,0.15);color:#a5b4fc;">
                      ${escapeHtml(r.namespace)} (${escapeHtml(r.direction)})
                    </span>
                  `).join("")}
                </div>
              </div>
            `;
          }
        }
      } catch (e) {
        // ignore
      }
    }

    el.unitDetailContent.innerHTML = `
      <div class="unit-detail-grid">
        <div class="detail-row">
          <span class="detail-label">Name:</span>
          <span class="detail-val">${escapeHtml(unit.name)}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">Unit ID:</span>
          <span class="detail-val" style="font-family:var(--font-mono);font-size:11px;">${escapeHtml(unit.id)}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">Source Type:</span>
          <span class="detail-val">${escapeHtml(unit.source_type)}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">Discovered Files:</span>
          <span class="detail-val">${unit.file_count.toLocaleString()} (${sizeMb} MB uncompressed)</span>
        </div>
        ${unit.date_min ? `
        <div class="detail-row">
          <span class="detail-label">Date Window:</span>
          <span class="detail-val" style="font-family:var(--font-mono);">${unit.date_min} &rarr; ${unit.date_max || ""}</span>
        </div>` : ""}
        ${unit.containers && unit.containers.length > 0 ? `
        <div class="detail-row">
          <span class="detail-label">Containers:</span>
          <span class="detail-val">${unit.containers.join(", ")}</span>
        </div>` : ""}
        ${unit.description ? `
        <div style="margin-top:4px;">
          <span class="detail-label">Description:</span>
          <p style="color:#cbd5e1;margin-top:2px;">${escapeHtml(unit.description)}</p>
        </div>` : ""}
        ${relatedHtml}
        <div style="margin-top:10px;">
          <button class="btn btn-sm ${inQueue ? 'btn-outline' : 'btn-accent'} btn-block" id="btnDetailToggleQueue">
            ${inQueue ? 'Remove from Queue' : '+ Add to Parse Queue (Parse Next)'}
          </button>
        </div>
      </div>
    `;

    const btnToggle = el.unitDetailContent.querySelector("#btnDetailToggleQueue");
    if (btnToggle) {
      btnToggle.onclick = () => {
        if (state.parseQueue.includes(unit.id)) {
          removeFromQueue(unit.id);
        } else {
          addToQueue([unit.id]);
        }
        showUnitDetail(unit);
      };
    }
  }

  function addToQueue(unitIds) {
    unitIds.forEach((uid) => {
      if (!state.parseQueue.includes(uid)) {
        state.parseQueue.push(uid);
      }
    });
    renderQueueItems();
    renderExplorerUnits(el.inputExplorerFilter ? el.inputExplorerFilter.value : "");
  }

  function removeFromQueue(unitId) {
    state.parseQueue = state.parseQueue.filter((uid) => uid !== unitId);
    renderQueueItems();
    renderExplorerUnits(el.inputExplorerFilter ? el.inputExplorerFilter.value : "");
  }

  function moveQueueItem(index, direction) {
    const targetIdx = index + direction;
    if (targetIdx < 0 || targetIdx >= state.parseQueue.length) return;
    const temp = state.parseQueue[index];
    state.parseQueue[index] = state.parseQueue[targetIdx];
    state.parseQueue[targetIdx] = temp;

    if (state.activeSessionId) {
      fetch("/api/jobs", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.activeSessionId, order: state.parseQueue }),
      }).catch((e) => console.error(e));
    }

    renderQueueItems();
  }

  function renderQueueItems(jobStatus = null) {
    if (!el.queueItemsContainer) return;
    el.queueItemsContainer.innerHTML = "";

    const items = jobStatus && jobStatus.items && jobStatus.items.length > 0
      ? jobStatus.items
      : state.parseQueue.map((uid) => ({ unit_id: uid, status: "pending" }));

    state.parseQueue = items.map((i) => i.unit_id);

    if (el.queueCountBadge) {
      el.queueCountBadge.textContent = `${items.length} queued`;
    }
    if (el.queueNavBadge) {
      el.queueNavBadge.textContent = items.length;
      el.queueNavBadge.style.display = items.length > 0 ? "inline-block" : "none";
    }

    if (items.length === 0) {
      el.queueItemsContainer.innerHTML = `
        <div class="queue-empty-state">
          <svg class="icon icon-lg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <line x1="8" y1="6" x2="21" y2="6"></line>
            <line x1="8" y1="12" x2="21" y2="12"></line>
            <line x1="8" y1="18" x2="21" y2="18"></line>
            <circle cx="4" cy="6" r="1.5"></circle>
            <circle cx="4" cy="12" r="1.5"></circle>
            <circle cx="4" cy="18" r="1.5"></circle>
          </svg>
          <p>Queue is empty. Select services or host logs on the left and click <strong>Add to Queue</strong> to define what to parse first.</p>
        </div>
      `;
      if (el.btnStartParsingQueue) el.btnStartParsingQueue.disabled = true;
      if (el.btnCancelQueue) el.btnCancelQueue.style.display = "none";
      return;
    }

    const isRunning = jobStatus && jobStatus.is_running;
    if (el.btnCancelQueue) {
      el.btnCancelQueue.style.display = isRunning ? "inline-block" : "none";
    }

    const hasPending = items.some((i) => i.status === "pending");
    if (el.btnStartParsingQueue) {
      el.btnStartParsingQueue.disabled = (!hasPending || isRunning);
      if (el.btnStartParsingQueueText) {
        el.btnStartParsingQueueText.textContent = isRunning
          ? "Parsing Queue Active..."
          : `Start Parsing (${items.filter((i) => i.status === 'pending').length} Pending)`;
      }
    }

    items.forEach((item, idx) => {
      const card = document.createElement("div");
      card.className = `queue-item-card ${item.status || "pending"}`;

      let unitObj = null;
      if (state.inventory && state.inventory.parts) {
        for (const p of state.inventory.parts) {
          for (const g of (p.groups || [])) {
            const found = (g.units || []).find((u) => u.id === item.unit_id);
            if (found) { unitObj = found; break; }
          }
          if (unitObj) break;
        }
      }

      const displayName = unitObj ? unitObj.name : item.name || item.unit_id;
      const partId = unitObj ? unitObj.part_id : item.part_id || "";

      let statusBadge = "";
      if (item.status === "parsing") {
        statusBadge = `<span class="status-badge parsing">Parsing...</span>`;
      } else if (item.status === "completed") {
        statusBadge = `<span class="status-badge completed">${item.events || 0} evts</span>`;
      } else if (item.status === "failed") {
        statusBadge = `<span class="status-badge failed">Failed</span>`;
      } else {
        statusBadge = `<span class="status-badge pending">Pending</span>`;
      }

      card.innerHTML = `
        <div class="queue-item-left">
          <div class="queue-priority-num">#${idx + 1}</div>
          <div class="queue-item-info">
            <span class="queue-item-name">${escapeHtml(displayName)}</span>
            <span class="queue-item-part">${escapeHtml(partId)}</span>
          </div>
        </div>
        <div class="queue-item-right">
          ${statusBadge}
          ${item.status === "pending" ? `
            <button class="queue-order-btn btn-up" title="Move Up">▲</button>
            <button class="queue-order-btn btn-down" title="Move Down">▼</button>
            <button class="queue-remove-btn" title="Remove">&times;</button>
          ` : ""}
        </div>
      `;

      const btnUp = card.querySelector(".btn-up");
      if (btnUp) btnUp.onclick = () => moveQueueItem(idx, -1);

      const btnDown = card.querySelector(".btn-down");
      if (btnDown) btnDown.onclick = () => moveQueueItem(idx, 1);

      const btnRemove = card.querySelector(".queue-remove-btn");
      if (btnRemove) btnRemove.onclick = () => removeFromQueue(item.unit_id);

      el.queueItemsContainer.appendChild(card);
    });
  }

  let lastCompletedCount = 0;
  let wasQueueRunning = false;

  async function checkQueueStatus() {
    if (!state.activeSessionId) return null;
    try {
      const res = await fetch(`/api/jobs?session_id=${encodeURIComponent(state.activeSessionId)}`);
      if (!res.ok) return null;
      const data = await res.json();
      renderQueueItems(data);

      if (data.is_running) {
        wasQueueRunning = true;
        if (!state.queuePollingTimer) {
          state.queuePollingTimer = setInterval(pollJobQueue, 1000);
        }
        updateQueueProgressBar(data);
      } else {
        if (state.queuePollingTimer) {
          clearInterval(state.queuePollingTimer);
          state.queuePollingTimer = null;
        }
        if (wasQueueRunning) {
          wasQueueRunning = false;
          updateQueueProgressBar(data);
          await refreshDashboardAfterParse(data);
        }
      }
      return data;
    } catch (err) {
      return null;
    }
  }

  function updateQueueProgressBar(data) {
    if (!el.queueProgressBarContainer) return;
    if (state.queueProgressHideTimeout) {
      clearTimeout(state.queueProgressHideTimeout);
      state.queueProgressHideTimeout = null;
    }
    el.queueProgressBarContainer.style.display = "block";

    const total = data.total_queued || 1;
    const completed = data.completed_count || 0;
    const failed = data.failed_count || 0;
    const pct = data.overall_pct != null ? data.overall_pct : Math.round((completed / total) * 100);

    const fillEl = el.queueProgressFill;
    const labelEl = el.queueProgressLabel;
    const pctEl = el.queueProgressPercent;

    if (data.is_running) {
      if (fillEl) {
        fillEl.className = "progress-bar-fill progress-fill-animated";
        fillEl.style.width = `${Math.max(4, pct)}%`;
      }
      if (pctEl) {
        pctEl.textContent = `${pct}%`;
      }
      if (labelEl) {
        const uName = data.current_unit_name || data.current_unit || "service";
        const filesDone = data.current_unit_files_done || 0;
        const filesTotal = data.current_unit_total_files || 0;
        const fileInfo = filesTotal > 0 ? ` (${filesDone}/${filesTotal} files)` : "";
        labelEl.textContent = `Parsing: ${uName}${fileInfo} • [${completed}/${total} units completed]`;
      }
      if (el.queueNavBadge) {
        el.queueNavBadge.textContent = `${pct}%`;
        el.queueNavBadge.style.display = "inline-block";
      }
    } else {
      // Completed or idle
      if (completed > 0 || failed > 0) {
        if (fillEl) {
          fillEl.className = "progress-bar-fill" + (failed === 0 ? " progress-fill-success" : "");
          fillEl.style.width = "100%";
        }
        if (pctEl) {
          pctEl.textContent = "100%";
        }
        if (labelEl) {
          if (failed > 0) {
            labelEl.textContent = `Finished with warnings: ${completed}/${total} units parsed, ${failed} failed.`;
          } else {
            labelEl.textContent = `Completed parsing ${completed} unit${completed === 1 ? '' : 's'} successfully!`;
          }
        }
        if (el.queueNavBadge) {
          el.queueNavBadge.textContent = "0";
          el.queueNavBadge.style.display = "none";
        }
        state.queueProgressHideTimeout = setTimeout(() => {
          if (!state.queuePollingTimer && el.queueProgressBarContainer) {
            el.queueProgressBarContainer.style.display = "none";
          }
        }, 6000);
      } else {
        el.queueProgressBarContainer.style.display = "none";
      }
    }
  }

  async function refreshDashboardAfterParse(data) {
    (data.items || []).forEach((item) => {
      if (item.status === "completed" && state.inventory) {
        for (const p of (state.inventory.parts || [])) {
          for (const g of (p.groups || [])) {
            const u = (g.units || []).find((x) => x.id === item.unit_id);
            if (u) {
              u.parsed = true;
              u.event_count = item.events || u.event_count;
            }
          }
        }
      }
    });
    renderExplorerUnits(el.inputExplorerFilter ? el.inputExplorerFilter.value : "");
    await loadStats();
    await loadFindings();
    await loadEvents();
    await loadK8sFacts();
    await loadTimeline();
  }

  async function startParsingQueue() {
    if (!state.activeSessionId || state.parseQueue.length === 0) return;
    try {
      if (el.btnStartParsingQueue) el.btnStartParsingQueue.disabled = true;
      if (el.btnStartParsingQueueText) el.btnStartParsingQueueText.textContent = "Starting parse queue...";

      // Immediately display progress bar in active start state
      updateQueueProgressBar({
        is_running: true,
        current_unit: state.parseQueue[0],
        current_unit_name: state.parseQueue[0],
        current_unit_files_done: 0,
        current_unit_total_files: 1,
        overall_pct: 2,
        total_queued: state.parseQueue.length,
        completed_count: 0,
        failed_count: 0,
      });

      const res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.activeSessionId,
          units: state.parseQueue,
        }),
      });
      if (!res.ok) throw new Error("Failed to start parse queue");

      const data = await res.json();
      renderQueueItems(data.queue);
      wasQueueRunning = true;
      updateQueueProgressBar(data.queue);

      if (!state.queuePollingTimer) {
        state.queuePollingTimer = setInterval(pollJobQueue, 1000);
      }
    } catch (err) {
      alert("Error starting parse queue: " + err.message);
      if (el.btnStartParsingQueue) el.btnStartParsingQueue.disabled = false;
      if (el.queueProgressBarContainer) el.queueProgressBarContainer.style.display = "none";
    }
  }

  async function cancelJobQueue() {
    if (!state.activeSessionId) return;
    try {
      const res = await fetch("/api/jobs", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.activeSessionId }),
      });
      if (res.ok) {
        const data = await res.json();
        renderQueueItems(data.queue);
        updateQueueProgressBar(data.queue);
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function pollJobQueue() {
    const data = await checkQueueStatus();
    if (!data) return;

    if (data.completed_count > lastCompletedCount) {
      lastCompletedCount = data.completed_count;
      await refreshDashboardAfterParse(data);
    }
  }

  // --- Enhanced Hybrid Telemetry Timeline & Anomaly Heatmap -------------------

  async function loadTimeline(queryParams) {
    try {
      // 48 buckets provides optimal resolution for smooth spline & heatmap
      const res = await fetch(`/api/timeline?${queryParams}&buckets=48`);
      if (!res.ok) return;
      const data = await res.json();
      state.timelineBuckets = data.buckets || [];
      state.timelineFromMicros = data.from_micros;
      state.timelineToMicros = data.to_micros;

      updateTimelineHeaderInfo();
      renderActiveTimeline();
    } catch (err) {
      console.error("Failed to load timeline:", err);
    }
  }

  function updateTimelineHeaderInfo() {
    if (!el.timelineRangeLabel) return;
    const isZoomed = state.fromMicros && state.toMicros && state.minTsMicros && state.maxTsMicros &&
      (state.fromMicros > state.minTsMicros || state.toMicros < state.maxTsMicros);

    if (isZoomed) {
      if (el.btnTimelineResetZoom) el.btnTimelineResetZoom.style.display = "inline-flex";
      const fromStr = formatUtcDatetime(state.fromMicros).slice(5, 16);
      const toStr = formatUtcDatetime(state.toMicros).slice(5, 16);
      el.timelineRangeLabel.textContent = `Zoomed: ${fromStr} → ${toStr} UTC`;
    } else {
      if (el.btnTimelineResetZoom) el.btnTimelineResetZoom.style.display = "none";
      const totalEvents = state.timelineBuckets.reduce((acc, b) => acc + (b.total || 0), 0);
      el.timelineRangeLabel.textContent = `Complete session (${totalEvents.toLocaleString()} events)`;
    }
  }

  function renderActiveTimeline() {
    if (!el.timelineChart) return;
    el.timelineChart.innerHTML = "";

    const buckets = state.timelineBuckets || [];
    if (buckets.length === 0) {
      el.timelineChart.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);font-size:12px;font-family:var(--font-mono);">
          <span>No timeline events recorded in this time range</span>
        </div>`;
      return;
    }

    if (state.timelineMode === "matrix") {
      renderTimelineMatrix(buckets, el.timelineChart);
    } else {
      renderTimelineWave(buckets, el.timelineChart);
    }
  }

  // Catmull-Rom to Cubic Bezier spline generator
  function getSplinePath(points) {
    if (!points || points.length === 0) return "";
    if (points.length === 1) return `M ${points[0][0]},${points[0][1]}`;
    let d = `M ${points[0][0]},${points[0][1]}`;
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = points[i === 0 ? i : i - 1];
      const p1 = points[i];
      const p2 = points[i + 1];
      const p3 = points[i + 2 < points.length ? i + 2 : i + 1];

      const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
      const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
      const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
      const cp2y = p2[1] - (p3[1] - p1[1]) / 6;

      d += ` C ${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
    }
    return d;
  }

  // Build a closed area polygon between a top spline and a horizontal bottom baseline
  function getClosedAreaPath(topPoints, bottomY) {
    if (!topPoints || topPoints.length === 0) return "";
    const spline = getSplinePath(topPoints);
    const lastX = topPoints[topPoints.length - 1][0];
    const firstX = topPoints[0][0];
    return `${spline} L ${lastX},${bottomY} L ${firstX},${bottomY} Z`;
  }

  // Build a closed band area between two curves
  function getBandAreaPath(topPoints, bottomPoints) {
    if (!topPoints || topPoints.length < 2 || !bottomPoints || bottomPoints.length < 2) return "";
    let d = `M ${topPoints[0][0]},${topPoints[0][1]}`;
    for (let i = 0; i < topPoints.length - 1; i++) {
      const p0 = topPoints[i === 0 ? i : i - 1];
      const p1 = topPoints[i];
      const p2 = topPoints[i + 1];
      const p3 = topPoints[i + 2 < topPoints.length ? i + 2 : i + 1];

      const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
      const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
      const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
      const cp2y = p2[1] - (p3[1] - p1[1]) / 6;

      d += ` C ${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
    }
    const lastBot = bottomPoints[bottomPoints.length - 1];
    d += ` L ${lastBot[0]},${lastBot[1]}`;
    const rev = [...bottomPoints].reverse();
    for (let i = 0; i < rev.length - 1; i++) {
      const p0 = rev[i === 0 ? i : i - 1];
      const p1 = rev[i];
      const p2 = rev[i + 1];
      const p3 = rev[i + 2 < rev.length ? i + 2 : i + 1];

      const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
      const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
      const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
      const cp2y = p2[1] - (p3[1] - p1[1]) / 6;

      d += ` C ${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
    }
    d += " Z";
    return d;
  }

  function renderTimelineWave(buckets, container) {
    const N = buckets.length;
    const width = container.clientWidth || 800;
    const height = 76;
    const usableH = 62;
    const yBase = 74;

    const wrap = document.createElement("div");
    wrap.className = "timeline-wave-wrap";

    const maxTotal = Math.max(...buckets.map((b) => b.total), 1);

    // Compute stacked coordinates for each bucket
    const totalPoints = [];
    const errBottomPoints = [];
    const warnBottomPoints = [];

    const bucketCoords = [];

    for (let i = 0; i < N; i++) {
      const b = buckets[i];
      const x = Math.round((i + 0.5) * (width / N));
      const tot = b.total;
      const err = (b.error || 0) + (b.fatal || 0);
      const warn = b.warn || 0;
      const info = b.info || 0;

      const hTot = Math.max(2, (tot / maxTotal) * usableH);
      const rInfo = tot > 0 ? (info / tot) : 1;
      const rWarn = tot > 0 ? (warn / tot) : 0;
      const rErr = tot > 0 ? (err / tot) : 0;

      const hInfo = hTot * rInfo;
      const hWarn = hTot * rWarn;
      const hErr = hTot * rErr;

      const yTop = yBase - hTot;
      const yWarn = yTop + hErr;
      const yInfo = yWarn + hWarn;

      totalPoints.push([x, yTop]);
      errBottomPoints.push([x, yWarn]);
      warnBottomPoints.push([x, yInfo]);

      bucketCoords.push({ x, yTop, b, i });
    }

    // Statistical 2.2σ Anomaly Threshold Line
    const totals = buckets.map((b) => b.total);
    const mean = totals.reduce((a, b) => a + b, 0) / N;
    const variance = totals.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / N;
    const stdDev = Math.sqrt(variance);
    const surgeThreshold = mean + 2.2 * stdDev;
    const hasThreshold = surgeThreshold < maxTotal && surgeThreshold > mean * 1.05;
    const yThresh = hasThreshold ? (yBase - (surgeThreshold / maxTotal) * usableH) : null;

    // SVG element
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "timeline-svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "none");

    // Definitions (Gradients and Glow filters)
    svg.innerHTML = `
      <defs>
        <linearGradient id="tl-grad-info" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#10b981" stop-opacity="0.45" />
          <stop offset="100%" stop-color="#10b981" stop-opacity="0.05" />
        </linearGradient>
        <linearGradient id="tl-grad-warn" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#f59e0b" stop-opacity="0.75" />
          <stop offset="100%" stop-color="#f59e0b" stop-opacity="0.2" />
        </linearGradient>
        <linearGradient id="tl-grad-err" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#f43f5e" stop-opacity="0.9" />
          <stop offset="100%" stop-color="#f43f5e" stop-opacity="0.3" />
        </linearGradient>
        <filter id="tl-glow" x="-10%" y="-10%" width="120%" height="120%">
          <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#00c781" flood-opacity="0.4" />
        </filter>
      </defs>
    `;

    // 1. Info Area (Base layer)
    const infoAreaPath = getClosedAreaPath(warnBottomPoints, yBase);
    if (infoAreaPath) {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", infoAreaPath);
      p.setAttribute("fill", "url(#tl-grad-info)");
      svg.appendChild(p);
    }

    // 2. Warn Area (Middle stacked layer)
    const warnAreaPath = getBandAreaPath(errBottomPoints, warnBottomPoints);
    if (warnAreaPath) {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", warnAreaPath);
      p.setAttribute("fill", "url(#tl-grad-warn)");
      svg.appendChild(p);
    }

    // 3. Error Area (Top crest layer)
    const errAreaPath = getBandAreaPath(totalPoints, errBottomPoints);
    if (errAreaPath) {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", errAreaPath);
      p.setAttribute("fill", "url(#tl-grad-err)");
      svg.appendChild(p);
    }

    // 4. Threshold Line (if variance exists)
    if (hasThreshold && yThresh !== null) {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("class", "timeline-threshold-line");
      line.setAttribute("x1", "0");
      line.setAttribute("y1", yThresh.toFixed(1));
      line.setAttribute("x2", width.toString());
      line.setAttribute("y2", yThresh.toFixed(1));
      svg.appendChild(line);

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "timeline-threshold-label");
      label.setAttribute("x", (width - 10).toString());
      label.setAttribute("y", (yThresh - 3).toFixed(1));
      label.textContent = "2.2σ Surge Alert";
      svg.appendChild(label);
    }

    // 5. Total Silhouette Crest Spline
    const crestPath = getSplinePath(totalPoints);
    if (crestPath) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", crestPath);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#34d399");
      path.setAttribute("stroke-width", "1.75");
      path.setAttribute("filter", "url(#tl-glow)");
      svg.appendChild(path);
    }

    // 6. Anomaly Pulse Pins on anomalous buckets
    bucketCoords.forEach(({ x, yTop, b }) => {
      const errCount = (b.error || 0) + (b.fatal || 0);
      const isSurge = hasThreshold && b.total >= surgeThreshold;
      const isIncident = errCount >= 10 || (b.total > 0 && errCount / b.total >= 0.15);

      if (isSurge || isIncident) {
        const pin = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        pin.setAttribute("cx", x.toString());
        pin.setAttribute("cy", yTop.toString());
        pin.setAttribute("r", "3.5");
        pin.setAttribute("fill", isIncident ? "#f43f5e" : "#38bdf8");
        pin.setAttribute("stroke", "#ffffff");
        pin.setAttribute("stroke-width", "1.5");
        pin.setAttribute("class", "timeline-spike-pin");
        svg.appendChild(pin);
      }
    });

    // 7. Interactive Crosshair & Brush Elements
    const scrubLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
    scrubLine.setAttribute("class", "timeline-scrub-line");
    scrubLine.setAttribute("y1", "0");
    scrubLine.setAttribute("y2", height.toString());
    scrubLine.style.display = "none";
    svg.appendChild(scrubLine);

    const scrubDot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    scrubDot.setAttribute("class", "timeline-scrub-dot");
    scrubDot.setAttribute("r", "4");
    scrubDot.style.display = "none";
    svg.appendChild(scrubDot);

    const brushRect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    brushRect.setAttribute("class", "timeline-brush-rect");
    brushRect.setAttribute("y", "0");
    brushRect.setAttribute("height", height.toString());
    brushRect.style.display = "none";
    svg.appendChild(brushRect);

    wrap.appendChild(svg);

    // 8. Bottom Thermal Intensity Ribbon
    const ribbon = document.createElement("div");
    ribbon.className = "timeline-ribbon";
    buckets.forEach((b) => {
      const cell = document.createElement("div");
      cell.className = "timeline-ribbon-cell";
      const errCount = (b.error || 0) + (b.fatal || 0);
      const errRatio = b.total > 0 ? (errCount / b.total) : 0;

      let bg = "rgba(16, 185, 129, 0.4)";
      if (errRatio >= 0.15 || errCount >= 50) {
        bg = "#ef4444";
      } else if (errRatio >= 0.03 || errCount >= 10) {
        bg = "#f43f5e";
      } else if (errRatio > 0 || (b.warn || 0) >= 15) {
        bg = "#f59e0b";
      } else if ((b.warn || 0) > 0) {
        bg = "rgba(245, 158, 11, 0.5)";
      }
      cell.style.backgroundColor = bg;
      ribbon.appendChild(cell);
    });
    wrap.appendChild(ribbon);

    container.appendChild(wrap);

    // --- Interactive Mouse & Drag-Brush Handlers ---
    let isDragging = false;
    let dragStartX = 0;
    let dragStartTime = 0;

    function getBucketAtX(clientX) {
      const rect = container.getBoundingClientRect();
      const relX = clientX - rect.left;
      const idx = Math.max(0, Math.min(N - 1, Math.floor((relX / rect.width) * N)));
      return { idx, b: buckets[idx], coords: bucketCoords[idx], relX, rect };
    }

    container.onmousemove = (e) => {
      const { idx, b, coords, relX, rect } = getBucketAtX(e.clientX);
      if (!b) return;

      if (isDragging) {
        // Dragging time brush selection
        const left = Math.min(dragStartX, relX);
        const w = Math.abs(relX - dragStartX);
        brushRect.setAttribute("x", left.toString());
        brushRect.setAttribute("width", w.toString());
        brushRect.style.display = "block";
        scrubLine.style.display = "none";
        scrubDot.style.display = "none";
      } else {
        // Moving crosshair scrubber
        scrubLine.setAttribute("x1", coords.x.toString());
        scrubLine.setAttribute("x2", coords.x.toString());
        scrubLine.style.display = "block";

        scrubDot.setAttribute("cx", coords.x.toString());
        scrubDot.setAttribute("cy", coords.yTop.toString());
        scrubDot.style.display = "block";
      }

      showTimelineTooltip(e, b, rect);
    };

    container.onmouseleave = () => {
      if (!isDragging) {
        scrubLine.style.display = "none";
        scrubDot.style.display = "none";
        hideTimelineTooltip();
      }
    };

    container.onmousedown = (e) => {
      if (e.button !== 0) return;
      const rect = container.getBoundingClientRect();
      isDragging = true;
      dragStartX = e.clientX - rect.left;
      dragStartTime = Date.now();
      brushRect.setAttribute("x", dragStartX.toString());
      brushRect.setAttribute("width", "0");
      brushRect.style.display = "block";
    };

    window.addEventListener("mouseup", (e) => {
      if (!isDragging) return;
      isDragging = false;
      brushRect.style.display = "none";
      hideTimelineTooltip();

      const rect = container.getBoundingClientRect();
      const currentRelX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
      const dragDist = Math.abs(currentRelX - dragStartX);

      if (dragDist > 12) {
        // Apply brush range selection
        const minX = Math.min(dragStartX, currentRelX);
        const maxX = Math.max(dragStartX, currentRelX);
        const span = (state.timelineToMicros || state.maxTsMicros) - (state.timelineFromMicros || state.minTsMicros);
        const baseFrom = state.timelineFromMicros || state.minTsMicros;
        const newFrom = Math.round(baseFrom + (minX / rect.width) * span);
        const newTo = Math.round(baseFrom + (maxX / rect.width) * span);

        state.fromMicros = Math.max(state.minTsMicros, newFrom);
        state.toMicros = Math.min(state.maxTsMicros, newTo);
        syncTimeInputFields();
        refreshAllViews();
      } else {
        // Single bucket click zoom
        const { b } = getBucketAtX(e.clientX);
        if (b) {
          state.fromMicros = b.start_micros;
          state.toMicros = b.end_micros;
          syncTimeInputFields();
          refreshAllViews();
        }
      }
    });
  }

  function renderTimelineMatrix(buckets, container) {
    const N = buckets.length;
    const wrap = document.createElement("div");
    wrap.className = "timeline-matrix-wrap";

    const maxErr = Math.max(...buckets.map((b) => (b.error || 0) + (b.fatal || 0)), 1);
    const maxWarn = Math.max(...buckets.map((b) => b.warn || 0), 1);
    const maxInfo = Math.max(...buckets.map((b) => b.info || 0), 1);

    const rows = [
      { key: "err", label: "Fatal / Error", dot: "#f43f5e", max: maxErr, color: "#f43f5e" },
      { key: "warn", label: "Warnings", dot: "#f59e0b", max: maxWarn, color: "#f59e0b" },
      { key: "info", label: "Normal Logs", dot: "#10b981", max: maxInfo, color: "#10b981" },
      { key: "heat", label: "Incident Heat", dot: "#c084fc", max: 1, color: "#ef4444" },
    ];

    rows.forEach((r) => {
      const rowEl = document.createElement("div");
      rowEl.className = "timeline-matrix-row";

      const labelEl = document.createElement("div");
      labelEl.className = "timeline-matrix-label";
      labelEl.innerHTML = `<span class="dot" style="background:${r.dot}"></span><span>${r.label}</span>`;
      rowEl.appendChild(labelEl);

      const cellsEl = document.createElement("div");
      cellsEl.className = "timeline-matrix-cells";

      buckets.forEach((b, idx) => {
        const cell = document.createElement("div");
        cell.className = "timeline-matrix-cell";
        cell.dataset.col = idx;

        let bg = "rgba(30, 41, 59, 0.4)";
        const errCount = (b.error || 0) + (b.fatal || 0);
        const errRatio = b.total > 0 ? (errCount / b.total) : 0;

        if (r.key === "err") {
          if (errCount > 0) {
            const opacity = Math.max(0.3, Math.min(1.0, 0.35 + 0.65 * (Math.log10(errCount + 1) / Math.log10(r.max + 1))));
            bg = `rgba(244, 63, 94, ${opacity.toFixed(2)})`;
          }
        } else if (r.key === "warn") {
          const w = b.warn || 0;
          if (w > 0) {
            const opacity = Math.max(0.3, Math.min(1.0, 0.35 + 0.65 * (Math.log10(w + 1) / Math.log10(r.max + 1))));
            bg = `rgba(245, 158, 11, ${opacity.toFixed(2)})`;
          }
        } else if (r.key === "info") {
          const inf = b.info || 0;
          if (inf > 0) {
            const opacity = Math.max(0.2, Math.min(1.0, 0.25 + 0.75 * (Math.log10(inf + 1) / Math.log10(r.max + 1))));
            bg = `rgba(16, 185, 129, ${opacity.toFixed(2)})`;
          }
        } else if (r.key === "heat") {
          if (errRatio >= 0.15 || errCount >= 50) {
            bg = "#ef4444";
          } else if (errRatio >= 0.03 || errCount >= 10) {
            bg = "#f43f5e";
          } else if (errRatio > 0) {
            bg = "#f59e0b";
          } else if ((b.warn || 0) > 0) {
            bg = "rgba(245, 158, 11, 0.5)";
          }
        }

        cell.style.backgroundColor = bg;

        cell.onmouseenter = (e) => {
          document.querySelectorAll(`.timeline-matrix-cell[data-col='${idx}']`).forEach((c) => c.classList.add("col-highlight"));
          const rect = container.getBoundingClientRect();
          showTimelineTooltip(e, b, rect);
        };
        cell.onmouseleave = () => {
          document.querySelectorAll(`.timeline-matrix-cell[data-col='${idx}']`).forEach((c) => c.classList.remove("col-highlight"));
          hideTimelineTooltip();
        };
        cell.onclick = () => {
          state.fromMicros = b.start_micros;
          state.toMicros = b.end_micros;
          syncTimeInputFields();
          refreshAllViews();
        };

        cellsEl.appendChild(cell);
      });

      rowEl.appendChild(cellsEl);
      wrap.appendChild(rowEl);
    });

    container.appendChild(wrap);
  }

  function showTimelineTooltip(e, b, containerRect) {
    if (!el.timelineTooltip) return;
    const startIso = formatUtcDatetime(b.start_micros);
    const endIso = formatUtcDatetime(b.end_micros).slice(11, 19);
    const errCount = (b.error || 0) + (b.fatal || 0);
    const errRatio = b.total > 0 ? (errCount / b.total) : 0;

    let badgeHtml = "";
    if (errRatio >= 0.1 || errCount >= 30) {
      badgeHtml = `<span style="background:rgba(239,68,68,0.25);color:#fca5a5;padding:1px 6px;border-radius:3px;font-weight:700;">🚨 ${(errRatio * 100).toFixed(1)}% Critical Spike</span>`;
    } else if (errRatio > 0 || (b.warn || 0) > 10) {
      badgeHtml = `<span style="background:rgba(245,158,11,0.2);color:#fcd34d;padding:1px 6px;border-radius:3px;">⚠️ ${(errRatio * 100).toFixed(1)}% Error Rate</span>`;
    } else {
      badgeHtml = `<span style="background:rgba(16,185,129,0.2);color:#6ee7b7;padding:1px 6px;border-radius:3px;">✓ Healthy Window</span>`;
    }

    el.timelineTooltip.innerHTML = `
      <div class="tooltip-time-header">
        <span>${startIso} → ${endIso} UTC</span>
        ${badgeHtml}
      </div>
      <div class="tooltip-stat-grid">
        <div class="tooltip-stat-item"><span class="dot" style="background:#38bdf8;"></span><span>Total: <strong>${b.total.toLocaleString()}</strong></span></div>
        <div class="tooltip-stat-item"><span class="dot" style="background:#f43f5e;"></span><span>Errors: <strong style="color:${errCount > 0 ? '#f43f5e' : 'inherit'}">${errCount.toLocaleString()}</strong></span></div>
        <div class="tooltip-stat-item"><span class="dot" style="background:#f59e0b;"></span><span>Warn: <strong>${(b.warn || 0).toLocaleString()}</strong></span></div>
        <div class="tooltip-stat-item"><span class="dot" style="background:#10b981;"></span><span>Info: <strong>${(b.info || 0).toLocaleString()}</strong></span></div>
      </div>
      <div class="tooltip-footer-hint">
        <span>Click to isolate 5m window</span>
        <span>Drag to zoom</span>
      </div>
    `;

    el.timelineTooltip.style.display = "block";

    const tooltipWidth = 230;
    const relX = e.clientX - containerRect.left;
    const boundedLeft = Math.max(tooltipWidth / 2 + 10, Math.min(containerRect.width - tooltipWidth / 2 - 10, relX));

    el.timelineTooltip.style.left = `${boundedLeft}px`;
    el.timelineTooltip.style.top = `-8px`;
  }

  function hideTimelineTooltip() {
    if (el.timelineTooltip) el.timelineTooltip.style.display = "none";
  }

  async function loadFindings(queryParams) {
    try {
      const res = await fetch(`/api/findings?${queryParams}`);
      if (!res.ok) return;
      const data = await res.json();
      const findings = data.findings || [];

      el.tabCountFindings.textContent = findings.length;
      el.findingsList.innerHTML = "";

      if (findings.length === 0) {
        el.findingsList.innerHTML = `
          <div class="table-empty">
            <svg class="icon" style="width:32px;height:32px;color:var(--color-success);margin-bottom:8px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            <p>No deterministic failure signatures matched in the active time window.</p>
          </div>`;
        return;
      }

      findings.forEach((f) => {
        const card = document.createElement("div");
        card.className = `finding-card severity-${f.severity}`;
        const sevClass = f.severity === "CRITICAL" ? "badge-fatal" : (f.severity === "HIGH" ? "badge-warn" : "badge-info");

        let samplesHtml = "";
        if (f.samples && f.samples.length > 0) {
          const sampleText = f.samples.map((s) => s.message || s.detail || JSON.stringify(s)).slice(0, 2).join("\n");
          samplesHtml = `<div class="finding-evidence"><code>${escapeHtml(sampleText)}</code></div>`;
        }

        card.innerHTML = `
          <div class="finding-header">
            <div class="finding-title-row">
              <span class="badge ${sevClass}">${f.severity}</span>
              <span class="finding-title">${escapeHtml(f.title)}</span>
            </div>
            <div class="finding-meta">
              <span>Category: <strong>${escapeHtml(f.category)}</strong></span>
              <span>Matched: <strong>${f.count.toLocaleString()} occurrences</strong></span>
            </div>
          </div>
          <p class="finding-desc">${escapeHtml(f.description)}</p>
          ${f.remediation ? `
          <div class="finding-remediation">
            <svg class="icon" style="color:#60a5fa;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="16" x2="12" y2="12"></line>
              <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
            <div><strong>Remediation:</strong> ${escapeHtml(f.remediation)}</div>
          </div>` : ""}
          ${samplesHtml}
        `;
        el.findingsList.appendChild(card);
      });
    } catch (err) {
      console.error("Failed to load findings:", err);
    }
  }

  async function loadK8sFacts() {
    try {
      const res = await fetch("/api/k8s_facts");
      if (!res.ok) return;
      const data = await res.json();
      const facts = data.k8s_facts || [];

      el.tabCountK8s.textContent = facts.length;
      el.k8sTableBody.innerHTML = "";

      if (facts.length === 0) {
        el.k8sTableBody.innerHTML = `<tr><td colspan="6" class="table-empty">No terminated pods or warning events recorded in this bundle.</td></tr>`;
        return;
      }

      facts.forEach((fact) => {
        const row = document.createElement("tr");
        const ts = fact.ts_micros ? new Date(fact.ts_micros / 1000).toISOString().replace("T", " ").slice(0, 19) : "N/A";
        const reasonBadge = (fact.reason === "OOMKilled" || fact.exit_code === 137)
          ? `<span class="badge-fatal">OOMKilled</span>`
          : `<span>${escapeHtml(fact.reason)}</span>`;

        row.innerHTML = `
          <td>${ts}</td>
          <td>${escapeHtml(fact.kind)}</td>
          <td>${escapeHtml(fact.namespace)}</td>
          <td><strong>${escapeHtml(fact.name)}</strong></td>
          <td>${reasonBadge} ${fact.exit_code ? `(${fact.exit_code})` : ""}</td>
          <td>${escapeHtml(fact.detail)}</td>
        `;
        el.k8sTableBody.appendChild(row);
      });
    } catch (err) {
      console.error("Failed to load k8s facts:", err);
    }
  }

  async function loadAnomalies(queryParams) {
    try {
      const res = await fetch(`/api/anomalies?${queryParams}`);
      if (!res.ok) return;
      const data = await res.json();
      const anomalies = data.anomalies || [];

      el.tabCountAnomalies.textContent = anomalies.length;
      el.anomaliesList.innerHTML = "";

      if (anomalies.length === 0) {
        el.anomaliesList.innerHTML = `<div class="table-empty">No volumetric spikes or cascading outages detected.</div>`;
        return;
      }

      anomalies.forEach((a) => {
        const card = document.createElement("div");
        card.className = "finding-card severity-HIGH";
        card.innerHTML = `
          <div class="finding-header">
            <div class="finding-title-row">
              <span class="badge ${a.kind === "CASCADE" ? "badge-fatal" : "badge-warn"}">${a.kind}</span>
              <span class="finding-title">${escapeHtml(a.detail)}</span>
            </div>
            <div class="finding-meta">
              <span>Time: <strong>${a.start_iso.slice(11, 19)} - ${a.end_iso.slice(11, 19)} UTC</strong></span>
              <span>Dev: <strong>${a.score.toFixed(1)}σ</strong></span>
            </div>
          </div>
          <p class="finding-desc">Impacted Namespaces: <strong>${escapeHtml(a.namespaces.join(", "))}</strong></p>
        `;
        el.anomaliesList.appendChild(card);
      });
    } catch (err) {
      console.error("Failed to load anomalies:", err);
    }
  }

  async function loadEvents() {
    try {
      const p = new URLSearchParams();
      if (state.fromMicros) p.set("from", state.fromMicros);
      if (state.toMicros) p.set("to", state.toMicros);
      if (state.activeOrigin && state.activeOrigin !== "all") p.set("origin", state.activeOrigin);
      if (state.activeLevel !== "all") p.set("level", state.activeLevel);
      if (state.activeNamespace !== "all") p.set("namespace", state.activeNamespace);
      if (state.activeIssueType !== "all") p.set("issue_type", state.activeIssueType);
      if (state.searchQuery) p.set("search", state.searchQuery);
      p.set("limit", state.pageLimit);
      p.set("offset", state.pageOffset);

      const res = await fetch(`/api/events?${p.toString()}`);
      if (!res.ok) return;
      const data = await res.json();
      const events = data.events || [];

      el.logTableBody.innerHTML = "";
      if (events.length === 0) {
        el.logTableBody.innerHTML = `<tr><td colspan="7" class="table-empty">No events match the selected filters.</td></tr>`;
        el.logCountDisplay.textContent = `Showing 0 events`;
        el.btnPrevPage.disabled = (state.pageOffset === 0);
        el.btnNextPage.disabled = true;
        return;
      }

      el.logCountDisplay.textContent = `Offset ${state.pageOffset + 1} - ${state.pageOffset + events.length}`;
      el.btnPrevPage.disabled = (state.pageOffset === 0);
      el.btnNextPage.disabled = (events.length < state.pageLimit);

      events.forEach((ev) => {
        const row = document.createElement("tr");
        const lvlClass = `badge-${ev.level.toLowerCase()}`;
        const issueBadge = ev.issue_type && ev.issue_type !== "Info"
          ? `<span class="badge ${lvlClass}">${escapeHtml(ev.issue_type)}</span>`
          : "";

        const isCluster = ev.origin === "cluster";
        const scopeBadge = isCluster
          ? `<span class="badge-scope-cluster" title="Kubernetes Cluster Microservice Log">Cluster</span>`
          : `<span class="badge-scope-host" title="Host Snapshot Log (${escapeHtml(ev.node || 'node')})">Host</span>`;

        const podOrNode = isCluster
          ? `${escapeHtml(ev.pod || ev.container || "-")}`
          : `${escapeHtml(ev.container || ev.node || "-")}`;

        row.innerHTML = `
          <td><code class="log-ts">${escapeHtml(formatTimestamp(ev.ts_iso, ev.ts_micros))}</code></td>
          <td>${scopeBadge}</td>
          <td><span class="${lvlClass}">${escapeHtml(ev.level)}</span></td>
          <td>${escapeHtml(ev.namespace)}</td>
          <td>${podOrNode}</td>
          <td>${issueBadge}</td>
          <td class="log-msg-content">${escapeHtml(ev.message)}</td>
        `;
        el.logTableBody.appendChild(row);
      });
    } catch (err) {
      console.error("Failed to load events:", err);
    }
  }

  // --- Modal Controllers -----------------------------------------------------

  async function openSwitchSessionModal() {
    el.modalSwitchSession.classList.add("active");
    el.sessionList.innerHTML = `<div class="loading-state">Fetching saved sessions...</div>`;
    try {
      const res = await fetch("/api/sessions");
      const data = await res.json();
      const sessions = data.sessions || [];

      el.sessionList.innerHTML = "";
      if (sessions.length === 0) {
        el.sessionList.innerHTML = `<div class="table-empty">No sessions saved yet.</div>`;
        return;
      }

      sessions.forEach((s) => {
        const item = document.createElement("div");
        item.className = `session-item ${s.is_active ? "active" : ""}`;
        const countDisplay = (s.total_events > 0)
          ? `${s.total_events.toLocaleString()} events`
          : (s.is_active ? `Ingesting...` : `Building session...`);
        const statusText = s.scanned_at || "Parsing in progress";
        item.innerHTML = `
          <div>
            <strong>${escapeHtml(s.filename || s.id)}</strong>
            <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">
              ${countDisplay} • ${s.size_mb} MB • ${statusText}
            </div>
          </div>
          <div>
            ${s.is_active
              ? `<span class="badge airgap-badge">ACTIVE</span>`
              : `<button class="btn btn-sm btn-outline">Switch</button>`}
          </div>
        `;
        item.onclick = () => switchSession(s.id);
        el.sessionList.appendChild(item);
      });
    } catch (err) {
      el.sessionList.innerHTML = `<div class="table-empty">Error loading sessions.</div>`;
    }
  }

  async function openUploadModal() {
    el.modalUploadBundle.classList.add("active");
    await loadDropFolderBundles();
  }

  async function loadDropFolderBundles() {
    try {
      const res = await fetch("/api/bundles");
      const data = await res.json();
      const bundles = data.bundles || [];

      el.bundlesList.innerHTML = "";
      if (bundles.length === 0) {
        el.bundlesList.innerHTML = `
          <div class="table-empty">
            No bundle archives found in <code>./bundles/</code>.<br>
            Copy your large support bundle tarballs into that folder to analyze them without browser limits.
          </div>`;
        return;
      }

      bundles.forEach((b) => {
        const item = document.createElement("div");
        item.className = "bundle-item";
        item.innerHTML = `
          <div>
            <strong>${escapeHtml(b.name)}</strong>
            <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">Size: ${b.size_mb} MB</div>
          </div>
          <button class="btn btn-sm btn-primary">Analyze</button>
        `;
        item.onclick = async () => {
          item.innerHTML = `<em>Inspecting bundle hierarchy...</em>`;
          const res = await fetch("/api/bundles/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ bundle_name: b.name, mode: "inventory" }),
          });
          if (res.ok) {
            const data = await res.json();
            closeModals();
            await checkSessions();
            openExplorerModal(data.inventory);
          } else {
            alert("Bundle inspection failed.");
          }
        };
        el.bundlesList.appendChild(item);
      });
    } catch (err) {
      el.bundlesList.innerHTML = `<div class="table-empty">Failed to list drop folder.</div>`;
    }
  }

  function handleFileSelected(file) {
    if (!file) return;
    el.uploadProgressContainer.style.display = "block";
    el.uploadProgressText.textContent = `Uploading ${file.name}...`;

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload?mode=inventory");
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.setRequestHeader("X-Inventory-Only", "true");

    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable) {
        const pct = Math.round((evt.loaded / evt.total) * 100);
        el.progressBarFill.style.width = `${pct}%`;
        el.uploadProgressPercent.textContent = `${pct}%`;
        if (pct >= 100) {
          el.uploadProgressText.textContent = "Upload complete. Discovering cluster & host log hierarchy...";
        }
      }
    };

    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const resp = JSON.parse(xhr.responseText);
          closeModals();
          await checkSessions();
          openExplorerModal(resp.inventory);
        } catch (e) {
          closeModals();
          await checkSessions();
          openExplorerModal();
        }
      } else {
        alert("Upload error: " + xhr.responseText);
      }
      el.uploadProgressContainer.style.display = "none";
    };

    xhr.onerror = async () => {
      el.uploadProgressContainer.style.display = "none";
      try {
        const res = await fetch("/api/sessions");
        const data = await res.json();
        const base = file.name.replace(/\.(tar|gz|bz2|xz|zip|tgz)+$/i, "");
        const matched = (data.sessions || []).find((s) => s.id === base || s.filename.includes(base));
        if (matched) {
          closeModals();
          await switchSession(matched.id);
          openExplorerModal();
          return;
        }
      } catch (e) {
        // ignore
      }
      closeModals();
      await checkSessions();
    };

    xhr.send(file);
  }

  function closeModals() {
    if (el.modalSwitchSession) el.modalSwitchSession.classList.remove("active");
    if (el.modalUploadBundle) el.modalUploadBundle.classList.remove("active");
    if (el.modalBundleExplorer) el.modalBundleExplorer.classList.remove("active");
  }

  // --- Event Listeners Setup ------------------------------------------------

  function setupEventListeners() {
    function on(elem, evt, fn) {
      if (elem) elem.addEventListener(evt, fn);
    }

    // Session buttons
    on(el.btnSwitchSession, "click", openSwitchSessionModal);
    on(el.btnUploadBundle, "click", openUploadModal);
    on(el.bundlePill, "click", openSwitchSessionModal);
    on(el.btnOpenExplorerModal, "click", () => openExplorerModal());

    // Modal Close and Cancel buttons (X mark and Cancel)
    on(el.btnCloseSwitchModal, "click", closeModals);
    on(el.btnCancelSwitchModal, "click", closeModals);
    on(el.btnCloseUploadModal, "click", closeModals);
    on(el.btnCancelUploadModal, "click", closeModals);
    on(el.btnCloseExplorerModal, "click", closeExplorerModal);
    on(el.btnDoneExplorerModal, "click", closeExplorerModal);

    // Explorer Modal Controls
    on(el.inputExplorerFilter, "input", () => {
      renderExplorerUnits(el.inputExplorerFilter.value);
    });

    on(el.btnSelectAllExplorer, "click", () => {
      const currentPart = state.inventory && state.inventory.parts
        ? state.inventory.parts.find((p) => p.id === state.activePartId)
        : null;
      if (!currentPart || !currentPart.groups) return;
      const term = (el.inputExplorerFilter.value || "").toLowerCase().trim();
      currentPart.groups.forEach((g) => {
        (g.units || []).forEach((u) => {
          if (!term || u.name.toLowerCase().includes(term) || u.id.toLowerCase().includes(term)) {
            state.selectedExplorerUnits.add(u.id);
          }
        });
      });
      renderExplorerUnits(el.inputExplorerFilter.value);
    });

    on(el.btnClearExplorer, "click", () => {
      state.selectedExplorerUnits.clear();
      renderExplorerUnits(el.inputExplorerFilter ? el.inputExplorerFilter.value : "");
    });

    on(el.btnAddSelectedToQueue, "click", () => {
      if (state.selectedExplorerUnits.size > 0) {
        addToQueue(Array.from(state.selectedExplorerUnits));
        state.selectedExplorerUnits.clear();
        renderExplorerUnits(el.inputExplorerFilter ? el.inputExplorerFilter.value : "");
      }
    });

    on(el.btnClearQueue, "click", () => {
      state.parseQueue = [];
      renderQueueItems();
      renderExplorerUnits(el.inputExplorerFilter ? el.inputExplorerFilter.value : "");
    });

    on(el.btnCancelQueue, "click", cancelJobQueue);
    on(el.btnStartParsingQueue, "click", startParsingQueue);

    // Forward wheel scrolling from left column headers/toolbars to explorerUnitsList
    const explorerColLeft = el.modalBundleExplorer ? el.modalBundleExplorer.querySelector(".explorer-col-left") : null;
    if (explorerColLeft) {
      explorerColLeft.addEventListener("wheel", (e) => {
        if (el.explorerUnitsList && !el.explorerUnitsList.contains(e.target)) {
          el.explorerUnitsList.scrollTop += e.deltaY;
        }
      }, { passive: true });
    }

    // Click outside modal card to close
    on(el.modalSwitchSession, "click", (e) => {
      if (e.target === el.modalSwitchSession) closeModals();
    });
    on(el.modalUploadBundle, "click", (e) => {
      if (e.target === el.modalUploadBundle) closeModals();
    });
    on(el.modalBundleExplorer, "click", (e) => {
      if (e.target === el.modalBundleExplorer) closeModals();
    });

    // Developer keyboard shortcuts: Escape closes modals, / focuses log search
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeModals();
      } else if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
        e.preventDefault();
        switchTab("logs");
        if (el.inputSearch) el.inputSearch.focus();
      }
    });

    // Time filter buttons
    on(el.btnApplyTimeFilter, "click", applyTimeFilter);
    on(el.btnResetTimeFilter, "click", resetTimeFilter);
    on(el.btnAlertResetRange, "click", resetTimeFilter);
    on(el.inputFromTime, "keydown", (e) => { if (e.key === "Enter") applyTimeFilter(); });
    on(el.inputToTime, "keydown", (e) => { if (e.key === "Enter") applyTimeFilter(); });

    // Timeline View Mode Toggle & Reset Zoom
    if (el.btnTimelineModeWave) {
      el.btnTimelineModeWave.addEventListener("click", () => {
        state.timelineMode = "wave";
        el.btnTimelineModeWave.classList.add("active");
        if (el.btnTimelineModeMatrix) el.btnTimelineModeMatrix.classList.remove("active");
        renderActiveTimeline();
      });
    }
    if (el.btnTimelineModeMatrix) {
      el.btnTimelineModeMatrix.addEventListener("click", () => {
        state.timelineMode = "matrix";
        el.btnTimelineModeMatrix.classList.add("active");
        if (el.btnTimelineModeWave) el.btnTimelineModeWave.classList.remove("active");
        renderActiveTimeline();
      });
    }
    if (el.btnTimelineResetZoom) {
      el.btnTimelineResetZoom.addEventListener("click", () => {
        state.fromMicros = state.minTsMicros;
        state.toMicros = state.maxTsMicros;
        syncTimeInputFields();
        refreshAllViews();
      });
    }

    // Debounced window resize for timeline re-render
    let timelineResizeTimeout;
    window.addEventListener("resize", () => {
      clearTimeout(timelineResizeTimeout);
      timelineResizeTimeout = setTimeout(() => {
        if (state.timelineBuckets && state.timelineBuckets.length > 0) {
          renderActiveTimeline();
        }
      }, 120);
    });

    // Presets
    on(el.timePresets, "click", (evt) => {
      const btn = evt.target.closest(".preset-btn");
      if (btn) applyPresetWindow(btn.dataset.window);
    });

    // Scope / Origin quick pills filter
    document.querySelectorAll(".origin-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        const orig = pill.dataset.origin;
        state.activeOrigin = (state.activeOrigin === orig && orig !== "all") ? "all" : orig;
        document.querySelectorAll(".origin-pill").forEach((p) => p.classList.toggle("active", p.dataset.origin === state.activeOrigin));
        if (el.selectOrigin) el.selectOrigin.value = state.activeOrigin;
        switchTab("logs");
        state.pageOffset = 0;
        loadEvents();
      });
    });

    // Level quick pills filter
    document.querySelectorAll(".level-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        const lvl = pill.dataset.level;
        state.activeLevel = (state.activeLevel === lvl) ? "all" : lvl;
        if (el.selectLevel) el.selectLevel.value = state.activeLevel;
        switchTab("logs");
        state.pageOffset = 0;
        loadEvents();
      });
    });

    if (el.selectOrigin) {
      on(el.selectOrigin, "change", () => {
        state.activeOrigin = el.selectOrigin.value;
        document.querySelectorAll(".origin-pill").forEach((p) => p.classList.toggle("active", p.dataset.origin === state.activeOrigin));
        state.pageOffset = 0;
        loadEvents();
      });
    }

    // Tab buttons
    on(el.viewTabs, "click", (evt) => {
      const btn = evt.target.closest(".tab-btn");
      if (btn) switchTab(btn.dataset.view);
    });

    // Overview Hub Quick-Jump & Full-Screen Navigation Cards
    document.addEventListener("click", (evt) => {
      const jumpElem = evt.target.closest("[data-jump]");
      if (jumpElem) {
        const targetView = jumpElem.dataset.jump;
        if (targetView === "timeline") {
          const filterToolbar = document.getElementById("filterToolbar");
          const btnToggleFilter = document.getElementById("btnToggleFilterToolbar");
          const filterChevron = document.getElementById("filterChevron");
          if (filterToolbar && filterToolbar.classList.contains("collapsed")) {
            filterToolbar.classList.remove("collapsed");
            if (btnToggleFilter) btnToggleFilter.classList.add("active");
            if (filterChevron) filterChevron.style.transform = "rotate(0deg)";
          }
          filterToolbar?.scrollIntoView({ behavior: "smooth" });
        } else if (targetView === "ai") {
          switchTab("ai");
          openAiDrawer();
        } else if (targetView) {
          switchTab(targetView);
        }
      }
    });

    // Collapsible Telemetry Filter Bar Toggle
    const btnToggleFilter = document.getElementById("btnToggleFilterToolbar");
    const filterToolbar = document.getElementById("filterToolbar");
    const filterChevron = document.getElementById("filterChevron");
    if (btnToggleFilter && filterToolbar) {
      btnToggleFilter.addEventListener("click", () => {
        const isCollapsed = filterToolbar.classList.toggle("collapsed");
        btnToggleFilter.classList.toggle("active", !isCollapsed);
        if (filterChevron) {
          filterChevron.style.transform = isCollapsed ? "rotate(-90deg)" : "rotate(0deg)";
        }
      });
    }

    // Log search & selects
    let searchDebounce = null;
    on(el.inputSearch, "input", () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        state.searchQuery = el.inputSearch.value.trim();
        state.pageOffset = 0;
        loadEvents();
      }, 300);
    });

    on(el.selectNamespace, "change", () => {
      state.activeNamespace = el.selectNamespace.value;
      state.pageOffset = 0;
      checkUnparsedAlert();
      loadEvents();
    });

    on(el.selectLevel, "change", () => {
      state.activeLevel = el.selectLevel.value;
      state.pageOffset = 0;
      loadEvents();
    });

    on(el.selectIssueType, "change", () => {
      state.activeIssueType = el.selectIssueType.value;
      state.pageOffset = 0;
      loadEvents();
    });

    // Pagination
    on(el.btnPrevPage, "click", () => {
      if (state.pageOffset >= state.pageLimit) {
        state.pageOffset -= state.pageLimit;
        loadEvents();
      }
    });
    on(el.btnNextPage, "click", () => {
      state.pageOffset += state.pageLimit;
      loadEvents();
    });

    // Upload tabs
    on(el.tabUploadBrowser, "click", () => {
      el.tabUploadBrowser.classList.add("active");
      el.tabUploadDropFolder.classList.remove("active");
      el.panelUploadBrowser.classList.add("active");
      el.panelUploadDropFolder.classList.remove("active");
    });
    on(el.tabUploadDropFolder, "click", () => {
      el.tabUploadDropFolder.classList.add("active");
      el.tabUploadBrowser.classList.remove("active");
      el.panelUploadDropFolder.classList.add("active");
      el.panelUploadBrowser.classList.remove("active");
      loadDropFolderBundles();
    });

    // Drag and drop zone & file browsing
    if (el.dropZone && el.fileInput) {
      el.dropZone.addEventListener("click", () => el.fileInput.click());
      if (el.btnBrowseFiles) {
        el.btnBrowseFiles.addEventListener("click", (e) => {
          e.stopPropagation();
          el.fileInput.click();
        });
      }
      el.fileInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) handleFileSelected(e.target.files[0]);
      });
      el.dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        el.dropZone.classList.add("dragover");
      });
      el.dropZone.addEventListener("dragleave", () => el.dropZone.classList.remove("dragover"));
      el.dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        el.dropZone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) handleFileSelected(e.dataTransfer.files[0]);
      });
    }

    // AI Copilot Drawer & Controls
    on(el.btnOpenAiChat, "click", openAiDrawer);
    on(el.btnCloseAiDrawer, "click", closeAiDrawer);
    on(el.aiDrawerBackdrop, "click", closeAiDrawer);
    on(el.btnClearAiChat, "click", clearAiChat);

    // Agentic Triage Controls
    on(el.btnOpenAgenticTriageTop, "click", () => switchTab("agentic"));
    on(el.btnTriggerAgenticTriage, "click", triggerAgenticTriage);
    if (el.btnCopyAgenticPlaybook) {
      el.btnCopyAgenticPlaybook.addEventListener("click", () => {
        if (el.agenticRunbookContent) {
          navigator.clipboard.writeText(el.agenticRunbookContent.textContent || "");
          el.btnCopyAgenticPlaybook.textContent = "Copied!";
          setTimeout(() => { if (el.btnCopyAgenticPlaybook) el.btnCopyAgenticPlaybook.textContent = "Copy Playbook"; }, 1800);
        }
      });
    }
    on(el.btnCloseAgenticModal, "click", () => el.modalAgenticEvidence?.classList.remove("active"));
    on(el.btnDismissAgenticModal, "click", () => el.modalAgenticEvidence?.classList.remove("active"));

    // Delegate clicks on agent cards to inspect evidence
    document.addEventListener("click", (evt) => {
      const card = evt.target.closest(".agent-card[data-agent-id]");
      if (card && card.dataset.agentId) {
        openAgenticEvidenceModal(card.dataset.agentId);
      }
    });

    if (el.aiModelSelect) {
      el.aiModelSelect.addEventListener("change", (e) => {
        state.selectedAiModel = e.target.value;
        if (el.aiStatusText) el.aiStatusText.textContent = state.selectedAiModel;
        if (el.aiStatusDot) el.aiStatusDot.title = `Ollama ${state.selectedAiModel} active`;
      });
    }

    if (el.btnAiSend) {
      el.btnAiSend.addEventListener("click", (e) => {
        if (state.aiIsStreaming) {
          e.preventDefault();
          e.stopPropagation();
          stopAiStream();
        }
      });
    }

    if (el.aiChatForm) {
      el.aiChatForm.addEventListener("submit", (e) => {
        e.preventDefault();
        if (state.aiIsStreaming) {
          stopAiStream();
          return;
        }
        if (el.aiChatInput) sendAiMessage(el.aiChatInput.value);
      });
    }

    if (el.aiChatInput) {
      el.aiChatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          if (state.aiIsStreaming) {
            stopAiStream();
            return;
          }
          if (el.aiChatForm) {
            if (el.aiChatForm.requestSubmit) {
              el.aiChatForm.requestSubmit();
            } else {
              sendAiMessage(el.aiChatInput.value);
            }
          }
        }
      });
      el.aiChatInput.addEventListener("input", () => {
        el.aiChatInput.style.height = "auto";
        el.aiChatInput.style.height = Math.min(el.aiChatInput.scrollHeight, 120) + "px";
      });
    }

    if (el.aiQuickPrompts) {
      el.aiQuickPrompts.addEventListener("click", (e) => {
        const chip = e.target.closest(".ai-chip");
        if (chip && chip.dataset.prompt) {
          sendAiMessage(chip.dataset.prompt);
        }
      });
    }

    const btnLaunchAiTab = document.getElementById("btnLaunchAiDrawerFromTab");
    if (btnLaunchAiTab) {
      btnLaunchAiTab.addEventListener("click", openAiDrawer);
    }

    document.addEventListener("click", (e) => {
      const card = e.target.closest(".ai-prompt-card");
      if (card && card.dataset.prompt) {
        openAiDrawer();
        sendAiMessage(card.dataset.prompt);
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && el.aiDrawer && el.aiDrawer.classList.contains("open")) {
        closeAiDrawer();
      }
    });
  }

  // --- AI Log Copilot (qwen3.5:0.8b via local Ollama) -------------------------

  function stopAiStream() {
    if (state.aiAbortController) {
      state.aiAbortController.abort();
      state.aiAbortController = null;
    }
    state.aiIsStreaming = false;
    updateAiSendButton(false);
  }

  function updateAiSendButton(isStreaming) {
    if (!el.btnAiSend) return;
    if (isStreaming) {
      el.btnAiSend.disabled = false;
      el.btnAiSend.className = "btn btn-danger ai-send-btn";
      el.btnAiSend.title = "Stop generation";
      el.btnAiSend.innerHTML = `
        <svg class="icon" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2"></rect>
        </svg>
      `;
    } else {
      el.btnAiSend.disabled = false;
      el.btnAiSend.className = "btn btn-primary ai-send-btn";
      el.btnAiSend.title = "Send message";
      el.btnAiSend.innerHTML = `
        <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="22" y1="2" x2="11" y2="13"></line>
          <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
        </svg>
      `;
    }
  }

  async function checkAiStatus() {
    try {
      const res = await fetch("/api/ai/status");
      const data = await res.json();
      state.aiStatus = data;
      const activeModel = data.model || "qwen2.5:3b";
      if (!state.selectedAiModel) {
        state.selectedAiModel = activeModel;
      }

      if (el.aiHeaderModelBadge) el.aiHeaderModelBadge.textContent = state.selectedAiModel;
      if (el.aiCardModelCode) el.aiCardModelCode.textContent = state.selectedAiModel;
      if (el.aiWelcomeModelCode) el.aiWelcomeModelCode.textContent = state.selectedAiModel;

      // Populate model dropdown if models list is available
      if (el.aiModelSelect && Array.isArray(data.installed_models) && data.installed_models.length > 0) {
        const currentVal = state.selectedAiModel;
        el.aiModelSelect.innerHTML = "";

        const sorted = [...data.installed_models].sort((a, b) => {
          if (a.includes("3b")) return -1;
          if (b.includes("3b")) return 1;
          return a.localeCompare(b);
        });

        for (const m of sorted) {
          const opt = document.createElement("option");
          opt.value = m;
          opt.textContent = m === "qwen2.5:3b" ? `${m} (Recommended)` : m;
          el.aiModelSelect.appendChild(opt);
        }
        if (sorted.includes(currentVal)) {
          el.aiModelSelect.value = currentVal;
        } else {
          el.aiModelSelect.value = activeModel;
          state.selectedAiModel = activeModel;
        }
      }

      if (el.aiStatusDot) {
        if (data.available && data.has_model) {
          el.aiStatusDot.className = "ai-status-dot online";
          el.aiStatusDot.title = `Ollama ${state.selectedAiModel} ready (Local Air-Gapped)`;
          if (el.aiStatusText) el.aiStatusText.textContent = state.selectedAiModel;
        } else if (data.available) {
          el.aiStatusDot.className = "ai-status-dot";
          el.aiStatusDot.style.background = "#eab308";
          el.aiStatusDot.title = `Ollama running, but model ${activeModel} not installed`;
          if (el.aiStatusText) el.aiStatusText.textContent = "pulling...";
        } else {
          el.aiStatusDot.className = "ai-status-dot offline";
          el.aiStatusDot.title = "Ollama offline on host:11434";
          if (el.aiStatusText) el.aiStatusText.textContent = "offline";
        }
      }
    } catch (err) {
      console.warn("AI Status check failed:", err);
      if (el.aiStatusDot) {
        el.aiStatusDot.className = "ai-status-dot offline";
        el.aiStatusDot.title = "Ollama endpoint unreachable";
        if (el.aiStatusText) el.aiStatusText.textContent = "offline";
      }
    }
  }

  function openAiDrawer() {
    if (!el.aiDrawer) return;
    el.aiDrawer.classList.add("open");
    if (el.aiDrawerBackdrop) el.aiDrawerBackdrop.classList.add("active");
    if (el.aiActiveSessionName) {
      el.aiActiveSessionName.textContent = state.activeSessionId || "Default";
    }
    if (el.aiChatInput) {
      setTimeout(() => el.aiChatInput.focus(), 150);
    }
    checkAiStatus();
  }

  function closeAiDrawer() {
    if (!el.aiDrawer) return;
    el.aiDrawer.classList.remove("open");
    if (el.aiDrawerBackdrop) el.aiDrawerBackdrop.classList.remove("active");
  }

  function clearAiChat() {
    state.aiChatHistory = [];
    if (!el.aiMessagesContainer) return;
    el.aiMessagesContainer.innerHTML = `
      <div class="ai-message ai-message-assistant" id="aiWelcomeMessage">
        <div class="ai-msg-avatar">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
          </svg>
        </div>
        <div class="ai-msg-content">
          <div class="ai-msg-header">
            <strong>AI Copilot</strong>
            <span class="ai-msg-time">Ready</span>
          </div>
          <div class="ai-msg-body">
            <p>Conversation cleared. Ready to analyze <strong>${escapeHtml(state.activeSessionId || "current bundle")}</strong>!</p>
          </div>
        </div>
      </div>
    `;
    if (el.aiTokenSpeed) el.aiTokenSpeed.textContent = "";
  }

  function formatAiMarkdown(text) {
    if (!text) return "";
    let str = escapeHtml(text);

    // Code blocks: ```lang ... ```
    str = str.replace(/```([a-zA-Z0-9_\-\+]*)\n([\s\S]*?)```/g, function (_, lang, code) {
      return `<pre><code>${code.trim()}</code></pre>`;
    });

    // Inline code: `code`
    str = str.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Bold: **text**
    str = str.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

    // Headings
    str = str.replace(/^### (.*$)/gim, '<h4 style="margin:12px 0 6px 0; color:#cbd5e1; font-size:13px; font-weight:700;">$1</h4>');
    str = str.replace(/^## (.*$)/gim, '<h3 style="margin:14px 0 8px 0; color:#f1f5f9; font-size:14px; font-weight:700;">$1</h3>');
    str = str.replace(/^# (.*$)/gim, '<h2 style="margin:16px 0 10px 0; color:#ffffff; font-size:15px; font-weight:700;">$1</h2>');

    // Bullet lists: * item or - item
    str = str.replace(/^\s*[\*\-]\s+(.*$)/gim, '<li>$1</li>');
    str = str.replace(/(<li>.*<\/li>)/gim, '<ul>$1</ul>');
    str = str.replace(/<\/ul>\s*<ul>/g, "");

    // Paragraph line breaks
    const paras = str.split(/\n{2,}/);
    if (paras.length > 1) {
      str = paras.map(p => {
        p = p.trim();
        if (p.startsWith('<pre') || p.startsWith('<ul') || p.startsWith('<h2') || p.startsWith('<h3') || p.startsWith('<h4')) {
          return p;
        }
        return `<p>${p.replace(/\n/g, '<br>')}</p>`;
      }).join('');
    } else {
      str = str.replace(/\n/g, '<br>');
    }

    return str;
  }

  async function sendAiMessage(promptText) {
    if (!promptText || !promptText.trim()) return;
    if (state.aiIsStreaming) return;

    promptText = promptText.trim();
    if (el.aiChatInput) {
      el.aiChatInput.value = "";
      el.aiChatInput.style.height = "auto";
    }

    // Append User Message to UI
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const userMsgHtml = `
      <div class="ai-message ai-message-user">
        <div class="ai-msg-avatar">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
            <circle cx="12" cy="7" r="4"></circle>
          </svg>
        </div>
        <div class="ai-msg-content">
          <div class="ai-msg-header">
            <strong>You</strong>
            <span class="ai-msg-time">${timeStr}</span>
          </div>
          <div class="ai-msg-body">
            <p>${escapeHtml(promptText)}</p>
          </div>
        </div>
      </div>
    `;
    el.aiMessagesContainer.insertAdjacentHTML("beforeend", userMsgHtml);
    el.aiMessagesContainer.scrollTop = el.aiMessagesContainer.scrollHeight;

    // Create Assistant Message Placeholder
    const assistantMsgId = `aiMsg_${Date.now()}`;
    const assistantMsgHtml = `
      <div class="ai-message ai-message-assistant" id="${assistantMsgId}">
        <div class="ai-msg-avatar">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
          </svg>
        </div>
        <div class="ai-msg-content">
          <div class="ai-msg-header">
            <strong>AI Copilot</strong>
            <span class="ai-msg-time">Analyzing...</span>
          </div>
          <!-- Reasoning Accordion (shown if thinking tokens present) -->
          <details class="ai-thinking-box" id="${assistantMsgId}_thinkingBox" style="display:none;" open>
            <summary class="ai-thinking-summary">
              <span class="ai-thinking-indicator" id="${assistantMsgId}_thinkingInd"></span>
              <span>Reasoning Chain (${escapeHtml(state.selectedAiModel || state.aiStatus?.model || 'qwen2.5:3b')})</span>
            </summary>
            <div class="ai-thinking-content" id="${assistantMsgId}_thinkingContent"></div>
          </details>
          <div class="ai-msg-body" id="${assistantMsgId}_body">
            <span class="ai-streaming-cursor" id="${assistantMsgId}_cursor"></span>
          </div>
        </div>
      </div>
    `;
    el.aiMessagesContainer.insertAdjacentHTML("beforeend", assistantMsgHtml);
    el.aiMessagesContainer.scrollTop = el.aiMessagesContainer.scrollHeight;

    const thinkingBoxEl = document.getElementById(`${assistantMsgId}_thinkingBox`);
    const thinkingContentEl = document.getElementById(`${assistantMsgId}_thinkingContent`);
    const thinkingIndEl = document.getElementById(`${assistantMsgId}_thinkingInd`);
    const bodyEl = document.getElementById(`${assistantMsgId}_body`);

    state.aiAbortController = new AbortController();
    state.aiIsStreaming = true;
    updateAiSendButton(true);
    if (el.aiTokenSpeed) el.aiTokenSpeed.textContent = "Connecting...";

    let fullThinking = "";
    let fullContent = "";
    let tokenCount = 0;
    const startTime = Date.now();

    try {
      const res = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: state.aiAbortController.signal,
        body: JSON.stringify({
          message: promptText,
          session_id: state.activeSessionId,
          namespace: state.activeNamespace !== "all" ? state.activeNamespace : null,
          from: state.fromMicros,
          to: state.toMicros,
          model: state.selectedAiModel || state.aiStatus?.model || "qwen2.5:3b",
          history: (state.aiChatHistory || []).slice(-6)
        })
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`HTTP ${res.status}: ${errText}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // Keep partial line in buffer

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data: ")) continue;
          const jsonStr = trimmed.slice(6).trim();
          if (!jsonStr) continue;

          try {
            const data = JSON.parse(jsonStr);
            if (data.status === "connected") {
              if (el.aiTokenSpeed) el.aiTokenSpeed.textContent = "Analyzing logs...";
              continue;
            }

            if (data.error) {
              bodyEl.innerHTML = `<div style="color:var(--color-error);"><p><strong>Error:</strong> ${escapeHtml(data.error)}</p></div>`;
              break;
            }

            // Handle reasoning thinking stream (if enabled)
            if (data.thinking) {
              if (thinkingBoxEl.style.display === "none") {
                thinkingBoxEl.style.display = "block";
              }
              fullThinking += data.thinking;
              thinkingContentEl.textContent = fullThinking;
              thinkingContentEl.scrollTop = thinkingContentEl.scrollHeight;
              tokenCount++;
            }

            // Handle main response text
            if (data.content) {
              fullContent += data.content;
              bodyEl.innerHTML = formatAiMarkdown(fullContent) + '<span class="ai-streaming-cursor"></span>';
              tokenCount++;
              if (thinkingIndEl) thinkingIndEl.style.animation = "none";
            }

            // Update token speed
            const elapsed = (Date.now() - startTime) / 1000;
            if (elapsed > 0 && tokenCount > 0 && el.aiTokenSpeed) {
              const tps = (tokenCount / elapsed).toFixed(1);
              el.aiTokenSpeed.textContent = `${tps} tokens/s`;
            }

            el.aiMessagesContainer.scrollTop = el.aiMessagesContainer.scrollHeight;

            if (data.done) {
              break;
            }
          } catch (e) {
            console.warn("Error parsing SSE JSON line:", e, jsonStr);
          }
        }
      }

      // Finalize display
      bodyEl.innerHTML = formatAiMarkdown(fullContent || (fullThinking ? "Thinking complete." : "Diagnosis complete."));
      if (thinkingIndEl) thinkingIndEl.style.display = "none";
      const durationSec = ((Date.now() - startTime) / 1000).toFixed(1);
      if (el.aiTokenSpeed) {
        el.aiTokenSpeed.textContent = `${tokenCount} tokens in ${durationSec}s`;
      }

      // Record to history
      state.aiChatHistory.push({ role: "user", content: promptText });
      state.aiChatHistory.push({ role: "assistant", content: fullContent });

    } catch (err) {
      if (err.name === "AbortError") {
        bodyEl.innerHTML = formatAiMarkdown(fullContent) + `<p style="font-size:11px;color:var(--text-dim);margin-top:6px;"><em>(Generation stopped by user)</em></p>`;
        if (el.aiTokenSpeed) el.aiTokenSpeed.textContent = "Stopped";
      } else {
        console.error("AI Chat stream failed:", err);
        bodyEl.innerHTML = `<div style="color:var(--color-error);"><p><strong>AI Execution Failed:</strong> ${escapeHtml(err.message)}</p><p style="font-size:11px;color:var(--text-dim);">Verify that Ollama is running on your machine with model <code>${escapeHtml(state.selectedAiModel || state.aiStatus?.model || 'qwen2.5:3b')}</code>.</p></div>`;
        if (el.aiTokenSpeed) el.aiTokenSpeed.textContent = "Error";
      }
    } finally {
      state.aiIsStreaming = false;
      state.aiAbortController = null;
      updateAiSendButton(false);
      const cursor = bodyEl.querySelector(".ai-streaming-cursor");
      if (cursor) cursor.remove();
      if (el.aiChatInput) el.aiChatInput.focus();
    }
  }

  function switchTab(viewName) {
    state.currentTab = viewName;
    document.querySelectorAll(".tab-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === viewName);
    });
    document.querySelectorAll(".tab-view").forEach((v) => {
      const match = (v.id.toLowerCase() === `view${viewName.toLowerCase()}`);
      v.classList.toggle("active", match);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (viewName === "agentic") {
      loadAgenticTreeOrReport();
    }
  }

  // --- Agentic Multi-Agent Triage (H-MAT) Controller ------------------------

  async function loadAgenticTreeOrReport() {
    if (!state.activeSessionId) return;
    try {
      const repResp = await fetch(`/api/agentic/report?session_id=${encodeURIComponent(state.activeSessionId)}`);
      if (repResp.ok) {
        const repData = await repResp.json();
        if (repData && repData.report) {
          renderAgenticReport(repData.report);
          return;
        }
      }
      const treeResp = await fetch(`/api/agentic/tree?session_id=${encodeURIComponent(state.activeSessionId)}`);
      if (treeResp.ok) {
        const treeData = await treeResp.json();
        renderDiscoveredAgentTree(treeData);
      }
    } catch (err) {
      console.warn("Failed to load agentic tree:", err);
    }
  }

  function renderDiscoveredAgentTree(treeData) {
    if (!el.agenticSubAgentsGrid) return;
    const namespaces = treeData.namespaces || [];
    const hostUnits = treeData.host_units || [];
    if (!namespaces.length && !hostUnits.length) {
      el.agenticSubAgentsGrid.innerHTML = `
        <div class="loading-state" style="grid-column: 1/-1;">
          No critical log faults or active namespaces discovered yet in this session.
        </div>`;
      return;
    }
    let html = "";
    namespaces.forEach((ns) => {
      html += `
        <div class="agent-card glass" id="card_ns_${escapeHtml(ns)}" data-agent-id="ns_${escapeHtml(ns)}">
          <div class="agent-card-header">
            <span class="agent-card-title">Namespace: ${escapeHtml(ns)}</span>
            <span class="agent-status-badge idle" id="badge_ns_${escapeHtml(ns)}">READY</span>
          </div>
          <div class="agent-card-body" id="body_ns_${escapeHtml(ns)}">
            Specialist agent queued to analyze error clusters and pod lifecycles in <code>${escapeHtml(ns)}</code>.
          </div>
          <div class="agent-card-footer">
            <span>Role: Namespace Specialist</span>
            <span id="dur_ns_${escapeHtml(ns)}">-- ms</span>
          </div>
        </div>`;
    });
    hostUnits.forEach((hu) => {
      html += `
        <div class="agent-card glass" id="card_host_${escapeHtml(hu)}" data-agent-id="host_${escapeHtml(hu)}">
          <div class="agent-card-header">
            <span class="agent-card-title">Host: ${escapeHtml(hu)}</span>
            <span class="agent-status-badge idle" id="badge_host_${escapeHtml(hu)}">READY</span>
          </div>
          <div class="agent-card-body" id="body_host_${escapeHtml(hu)}">
            Specialist agent queued to analyze storage drives, systemd, and Fluent-Bit in <code>${escapeHtml(hu)}</code>.
          </div>
          <div class="agent-card-footer">
            <span>Role: Host Specialist</span>
            <span id="dur_host_${escapeHtml(hu)}">-- ms</span>
          </div>
        </div>`;
    });
    el.agenticSubAgentsGrid.innerHTML = html;
  }

  async function triggerAgenticTriage() {
    if (state.agenticIsRunning || !state.activeSessionId) return;
    state.agenticIsRunning = true;
    state.agenticVerdicts = {};

    if (el.btnTriggerAgenticTriage) {
      el.btnTriggerAgenticTriage.disabled = true;
      if (el.btnTriggerAgenticTriageText) el.btnTriggerAgenticTriageText.textContent = "Swarm Analyzing...";
    }
    if (el.agenticProgressBar) el.agenticProgressBar.style.display = "block";
    if (el.agenticProgressStatus) el.agenticProgressStatus.style.display = "flex";
    if (el.agenticProgressFill) el.agenticProgressFill.style.width = "5%";
    if (el.agenticCurrentStep) el.agenticCurrentStep.textContent = "Spawning agent hierarchy...";

    // Reset domain badges
    ["Commander", "ClusterLead", "HostLead"].forEach((name) => {
      const b = document.getElementById(`badge${name}`);
      if (b) { b.className = "agent-status-badge running"; b.textContent = "QUEUED"; }
    });

    try {
      const resp = await fetch("/api/agentic/triage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.activeSessionId,
          from: state.fromMicros,
          to: state.toMicros,
          model: state.selectedAiModel || "qwen2.5:3b",
        }),
      });

      if (!resp.ok) throw new Error(`HTTP error ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let totalAgents = 1;
      let completedAgents = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop();

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data: ")) continue;
          const jsonStr = trimmed.slice(6).trim();
          if (!jsonStr) continue;
          try {
            const evt = JSON.parse(jsonStr);
            if (evt.type === "tree_discovered") {
              totalAgents = evt.total_agents || 1;
              renderDiscoveredAgentTree({ namespaces: evt.cluster_namespaces, host_units: evt.host_units });
              if (el.agenticCompletedCount) el.agenticCompletedCount.textContent = `0/${totalAgents} agents`;
            } else if (evt.type === "agent_started") {
              const b = document.getElementById(`badge_${evt.agent_id}`) || document.getElementById(`badge${evt.agent_id}`);
              if (b) { b.className = "agent-status-badge running"; b.textContent = "ANALYZING"; }
              if (el.agenticCurrentStep) el.agenticCurrentStep.textContent = `Analyzing: ${evt.title}`;
            } else if (evt.type === "agent_completed") {
              completedAgents++;
              const verdict = evt.verdict;
              state.agenticVerdicts[evt.agent_id] = verdict;
              const pct = Math.min(95, Math.round((completedAgents / totalAgents) * 100));
              if (el.agenticProgressFill) el.agenticProgressFill.style.width = `${pct}%`;
              if (el.agenticCompletedCount) el.agenticCompletedCount.textContent = `${completedAgents}/${totalAgents} agents`;
              updateAgentCardVerdict(evt.agent_id, verdict);
            } else if (evt.type === "triage_completed") {
              if (el.agenticProgressFill) el.agenticProgressFill.style.width = "100%";
              if (el.agenticCurrentStep) el.agenticCurrentStep.textContent = `Triage completed in ${evt.report.total_duration_ms}ms`;
              renderAgenticReport(evt.report);
            }
          } catch (err) {
            console.warn("Failed parsing agent SSE event:", err);
          }
        }
      }
    } catch (err) {
      console.error("Agentic triage failed:", err);
      if (el.agenticCurrentStep) el.agenticCurrentStep.textContent = `Error: ${err.message}`;
    } finally {
      state.agenticIsRunning = false;
      if (el.btnTriggerAgenticTriage) {
        el.btnTriggerAgenticTriage.disabled = false;
        if (el.btnTriggerAgenticTriageText) el.btnTriggerAgenticTriageText.textContent = "Re-Run Multi-Agent Triage";
      }
    }
  }

  function updateAgentCardVerdict(agentId, verdict) {
    let badgeId = `badge_${agentId}`;
    let bodyId = `body_${agentId}`;
    let durId = `dur_${agentId}`;
    if (agentId === "commander") { badgeId = "badgeCommander"; bodyId = "descCommander"; durId = "durCommander"; }
    else if (agentId === "lead_cluster") { badgeId = "badgeClusterLead"; bodyId = "descClusterLead"; durId = "durClusterLead"; }
    else if (agentId === "lead_host") { badgeId = "badgeHostLead"; bodyId = "descHostLead"; durId = "durHostLead"; }

    const b = document.getElementById(badgeId);
    if (b) {
      b.className = `agent-status-badge ${verdict.status.toLowerCase()}`;
      b.textContent = verdict.status.toUpperCase();
    }
    const bodyEl = document.getElementById(bodyId);
    if (bodyEl) {
      bodyEl.textContent = verdict.primary_issue || verdict.causal_hypothesis || "Diagnosis completed.";
    }
    const durEl = document.getElementById(durId);
    if (durEl) {
      durEl.textContent = `${verdict.duration_ms || 0} ms`;
    }
  }

  function renderAgenticReport(report) {
    if (!report) return;
    state.agenticReport = report;

    if (report.agent_verdicts) {
      report.agent_verdicts.forEach((v) => {
        state.agenticVerdicts[v.agent_id] = v;
        updateAgentCardVerdict(v.agent_id, v);
      });
    }

    // 1. Patient Zero Spotlight Card
    if (el.agenticPatientZeroCard) {
      el.agenticPatientZeroCard.style.display = "block";
      const isCritical = report.overall_status === "critical" || report.overall_status === "degraded";
      el.agenticPatientZeroCard.className = `patient-zero-card ${isCritical ? "" : "nominal"}`;

      if (el.pzBadge) {
        el.pzBadge.className = `pz-tag ${isCritical ? "critical" : "nominal"}`;
        el.pzBadge.textContent = isCritical ? "PATIENT ZERO ROOT TRIGGER" : "SYSTEM OPERATING NOMINALLY";
      }
      if (el.pzTimestamp) {
        el.pzTimestamp.textContent = report.patient_zero_iso || "T0 Baseline";
      }
      if (el.pzComponent) {
        el.pzComponent.textContent = report.patient_zero_component ? `Root Component: ${report.patient_zero_component}` : "No Service Failures Detected";
      }
      if (el.pzSummary) {
        el.pzSummary.textContent = report.executive_summary;
      }
    }

    // 2. Causal Cascade Timeline
    if (el.agenticCascadeContainer && el.agenticCascadeList) {
      if (report.causal_chain && report.causal_chain.length > 0) {
        el.agenticCascadeContainer.style.display = "block";
        let html = "";
        report.causal_chain.forEach((step, idx) => {
          const isPz = step.is_patient_zero;
          html += `
            <div class="cascade-step-item">
              <div class="cascade-dot ${isPz ? "pz" : ""}" title="${isPz ? "Patient Zero (Earliest Fault)" : "Downstream Ripple"}"></div>
              <div class="cascade-step-body">
                <div>
                  <strong style="color: ${isPz ? "#f87171" : "#38bdf8"}; font-size: 13px;">${isPz ? "T0 Root Trigger" : `Cascade Step ${idx + 1}`}: [${escapeHtml(step.domain)}] ${escapeHtml(step.component)}</strong>
                  <div class="cascade-step-text">${escapeHtml(step.description)}</div>
                </div>
                <div class="cascade-step-meta">
                  <span>${escapeHtml(step.timestamp_iso)}</span>
                </div>
              </div>
            </div>`;
        });
        el.agenticCascadeList.innerHTML = html;
      } else {
        el.agenticCascadeContainer.style.display = "none";
      }
    }

    // 3. Actionable Remediation Runbook
    if (el.agenticRunbookContainer && el.agenticRunbookContent) {
      if (report.remediation_playbook && report.remediation_playbook.length > 0) {
        el.agenticRunbookContainer.style.display = "block";
        el.agenticRunbookContent.textContent = report.remediation_playbook.join("\n");
      }
    }
  }

  function openAgenticEvidenceModal(agentId) {
    const verdict = state.agenticVerdicts[agentId];
    if (!verdict) return;
    if (el.modalAgentTitle) el.modalAgentTitle.textContent = `${verdict.title} - Diagnosis Evidence`;
    if (el.modalAgentSubtitle) el.modalAgentSubtitle.textContent = `Status: ${verdict.status.toUpperCase()} | Duration: ${verdict.duration_ms}ms | Earliest Fault: ${verdict.earliest_fault_iso || "None"}`;

    let bodyHtml = `
      <div style="margin-bottom: 16px;">
        <h4 style="font-size: 13px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 6px;">Causal Hypothesis & Reasoning</h4>
        <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-subtle); border-radius: 8px; padding: 12px; font-size: 13.5px; line-height: 1.5; color: #ffffff;">
          ${escapeHtml(verdict.causal_hypothesis || verdict.primary_issue)}
        </div>
      </div>`;

    if (verdict.evidence && verdict.evidence.length > 0) {
      bodyHtml += `
        <h4 style="font-size: 13px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px;">Grounded Log Evidence (${verdict.evidence.length} events)</h4>
        <div class="events-table-container glass" style="max-height: 340px; overflow-y: auto;">
          <table class="events-table" style="font-size: 11.5px;">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Level</th>
                <th>Pod / Unit</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>`;
      verdict.evidence.forEach((ev) => {
        const lvlClass = (ev.level || "ERROR").toLowerCase();
        bodyHtml += `
          <tr>
            <td style="font-family: var(--font-mono); white-space: nowrap;">${escapeHtml(ev.ts_iso)}</td>
            <td><span class="level-tag ${lvlClass}">${escapeHtml(ev.level)}</span></td>
            <td><code>${escapeHtml(ev.pod || ev.container || "sys")}</code></td>
            <td style="font-family: var(--font-mono); font-size: 11px;">${escapeHtml(ev.message)}</td>
          </tr>`;
      });
      bodyHtml += `</tbody></table></div>`;
    }

    if (el.modalAgentBody) el.modalAgentBody.innerHTML = bodyHtml;
    if (el.modalAgenticEvidence) el.modalAgenticEvidence.classList.add("active");
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Launch on DOM ready
  document.addEventListener("DOMContentLoaded", init);
})();
