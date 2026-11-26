const state = {
  selectedDate: "",
  events: [],
  taxonomy: { category_groups: [], directions: [] },
  categoryLookup: {},
  directionLookup: {},
  selectedEventId: null,
  drawerEventId: null,
  audioMode: "desensitized",
  drawerAudioMode: "desensitized",
  waveformData: null,
  drawerWaveformData: null,
  playheadRatio: 0,
  drawerPlayheadRatio: 0,
  overview: null,
  activeTab: "overview",
  toastTimer: null,
};

const VIEW_META = {
  overview: {
    eyebrow: "ACOUSTIC FORENSIC ARCHIVE",
    title: "声学存证总览",
    subtitle: "清晰掌握夜间扰民规律、加密存证完整性与外部互证进展",
  },
  evidence: {
    eyebrow: "LOSSLESS EVIDENCE VAULT",
    title: "声学证据库",
    subtitle: "双频带示波波形、点击定位试听与不可变存证核验",
  },
  corroboration: {
    eyebrow: "CORROBORATION LEDGER",
    title: "外部互证台账",
    subtitle: "物业劝阻、社区调解、110 出警回执与邻居证言时间轴",
  },
  export: {
    eyebrow: "PRIVACY-SAFE EXPORT & CORRELATION",
    title: "脱敏导出与双节点",
    subtitle: "隐私安全 ZIP 打包、OpenTimestamps 摘要与双节点方位对比",
  },
};

const BAND_LABELS = {
  low_impact_20_180hz: { text: "低频撞击 (20–180Hz)", short: "低频撞击", cls: "pill-low", color: "#1d4e6b" },
  high_vocal_300_4000hz: { text: "中高频人声 (300–4kHz)", short: "中高频人声", cls: "pill-high", color: "#a34f39" },
  dual_band_impact_and_vocal: { text: "双频混合 (撞击+人声)", short: "双频混合", cls: "pill-amber", color: "#183b32" },
};

const COR_LABELS = {
  property_notice: { text: "物业劝阻", cls: "pill-low" },
  community_mediation: { text: "社区调解", cls: "pill-low" },
  police_call: { text: "110 出警", cls: "pill-danger" },
  neighbor_witness: { text: "邻居互证", cls: "pill-ok" },
  landlord_notice: { text: "房东告知", cls: "pill-amber" },
  cma_test: { text: "CMA 检测", cls: "pill-ok" },
  other: { text: "其他记录", cls: "" },
};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function showToast(message, type = "info") {
  const bar = $("toast-island");
  const dot = $("toast-dot");
  $("toast-text").textContent = message;
  dot.className = "toast-dot" + (type === "success" ? " success" : type === "error" ? " error" : "");
  bar.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => bar.classList.remove("show"), 3200);
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || `HTTP ${res.status}`);
  }
  return res.json();
}

function formatShortTime(isoStr) {
  const d = new Date(isoStr);
  if (Number.isNaN(d.getTime())) return isoStr;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}:${ss}`;
}

/* Theme Handling */
function initTheme() {
  const saved = localStorage.getItem("ham_theme") || "light";
  applyTheme(saved);
  $("btn-theme-toggle").addEventListener("click", toggleTheme);
}

function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  localStorage.setItem("ham_theme", next);
  applyTheme(next);
  if (state.waveformData) drawWaveform("waveform-canvas", state.waveformData, state.playheadRatio);
  if (state.drawerWaveformData) drawWaveform("drawer-waveform-canvas", state.drawerWaveformData, state.drawerPlayheadRatio);
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  $("icon-moon").classList.toggle("hidden", theme === "dark");
  $("icon-sun").classList.toggle("hidden", theme !== "dark");
}

/* Tab Navigation */
function switchTab(tabName) {
  if (!VIEW_META[tabName]) return;
  state.activeTab = tabName;
  $("page-eyebrow").textContent = VIEW_META[tabName].eyebrow;
  $("view-title").textContent = VIEW_META[tabName].title;
  $("view-subtitle").textContent = VIEW_META[tabName].subtitle;

  document.querySelectorAll(".rail-item").forEach((btn) => {
    const active = btn.dataset.tab === tabName;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tabName}`);
  });
  if (tabName === "evidence" && state.selectedEventId) {
    loadVaultWaveform(state.selectedEventId);
  }
}

/* Command Palette */
const COMMANDS = [
  { label: "前往：声学存证总览", hint: "视图", run: () => switchTab("overview") },
  { label: "前往：声学证据库", hint: "视图", run: () => switchTab("evidence") },
  { label: "前往：外部互证台账", hint: "视图", run: () => switchTab("corroboration") },
  { label: "前往：脱敏导出与双节点", hint: "视图", run: () => switchTab("export") },
  { label: "执行全库哈希链与 FLAC 完整性审计", hint: "审计", run: () => handleVerifyChain() },
  { label: "一键导出隐私脱敏 ZIP 分享包", hint: "脱敏", run: () => { switchTab("export"); triggerShareExport(); } },
  { label: "切换日间卷宗纸 / 深夜森林暗色模式", hint: "主题", run: () => toggleTheme() },
];

function openCommandPalette() {
  $("cmd-backdrop").classList.remove("hidden");
  $("cmd-input").value = "";
  renderCommandList("");
  $("cmd-input").focus();
}

function closeCommandPalette() {
  $("cmd-backdrop").classList.add("hidden");
}

function renderCommandList(query) {
  const q = query.trim().toLowerCase();
  const matched = COMMANDS.filter((c) => !q || c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q));
  const list = $("cmd-list");
  if (!matched.length) {
    list.innerHTML = `<div class="empty-state" style="min-height:80px"><p>未匹配到快捷命令</p></div>`;
    return;
  }
  list.innerHTML = matched
    .map((c, idx) => `<button type="button" class="cmd-item" data-idx="${idx}"><span>${escapeHtml(c.label)}</span><span>${escapeHtml(c.hint)}</span></button>`)
    .join("");
  list.querySelectorAll(".cmd-item").forEach((btn, idx) => {
    btn.addEventListener("click", () => {
      closeCommandPalette();
      matched[idx].run();
    });
  });
}

/* SVG Donut Chart (Willowsum Style) */
function renderBandDonut(bandCounts, total) {
  const wrap = $("band-donut-wrap");
  const legend = $("band-legend");
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const entries = [
    { key: "dual_band_impact_and_vocal", label: "双频混合", color: isDark ? "#6ec49e" : "#183b32" },
    { key: "low_impact_20_180hz", label: "低频撞击", color: isDark ? "#6cb4e4" : "#1d4e6b" },
    { key: "high_vocal_300_4000hz", label: "中高频人声", color: isDark ? "#e58d6f" : "#a34f39" },
  ];

  const r = 58;
  const c = 2 * Math.PI * r;
  const denom = Math.max(1, total);
  let offset = 0;

  const segments = entries
    .map((e) => {
      const val = bandCounts[e.key] || 0;
      const frac = total > 0 ? val / denom : 0;
      const len = frac * c;
      const seg = `<circle cx="80" cy="80" r="${r}" fill="none" stroke="${e.color}" stroke-width="18" stroke-dasharray="${len.toFixed(2)} ${(c - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 80 80)" />`;
      offset += len;
      return seg;
    })
    .join("");

  wrap.innerHTML = `
    <svg width="160" height="160" viewBox="0 0 160 160" aria-label="频段分布环图">
      <circle cx="80" cy="80" r="${r}" fill="none" stroke="var(--border-rule)" stroke-width="18" opacity="0.45" />
      ${segments}
      <text x="80" y="77" text-anchor="middle" font-size="24" font-weight="600" fill="var(--ink-primary)" class="serif tabular">${total}</text>
      <text x="80" y="96" text-anchor="middle" font-size="11" fill="var(--ink-muted)">总存证次数</text>
    </svg>
  `;

  legend.innerHTML = entries
    .map((e) => {
      const val = bandCounts[e.key] || 0;
      const pct = total > 0 ? Math.round((val / denom) * 100) : 0;
      return `
        <div class="donut-legend-row">
          <span class="legend-dot" style="background:${e.color}"></span>
          <span>${e.label}</span>
          <strong class="mono">${val} 次</strong>
          <span class="mono" style="color:var(--ink-muted)">${pct}%</span>
        </div>
      `;
    })
    .join("");
}

/* Nocturnal Bar Chart with Dashed Average Guide */
function renderHourlyChart(buckets) {
  const wrap = $("hourly-chart-wrap");
  const maxCount = Math.max(1, ...buckets.map((b) => b.count));
  const width = 680;
  const height = 210;
  const padL = 28;
  const padR = 12;
  const padT = 20;
  const padB = 30;
  const chartW = width - padL - padR;
  const chartH = height - padT - padB;
  const step = chartW / buckets.length;
  const barW = Math.max(9, step - 9);

  const nightStart = padL + 2 * step;
  const nightW = 9 * step;

  let grid = "";
  [0.33, 0.66, 1.0].forEach((r) => {
    const y = (padT + chartH * (1 - r)).toFixed(1);
    grid += `<line x1="${padL}" y1="${y}" x2="${width - padR}" y2="${y}" stroke="var(--border-rule)" stroke-width="1" />`;
  });

  let bars = "";
  buckets.forEach((b, idx) => {
    const x = padL + idx * step + (step - barW) / 2;
    const h = b.count > 0 ? Math.max(8, (b.count / maxCount) * chartH) : 2;
    const y = padT + chartH - h;
    const isNight = b.hour >= 22 || b.hour < 7;
    const fill = b.count === 0 ? "var(--border-rule)" : isNight ? "var(--accent)" : "var(--ink-muted)";
    bars += `
      <g>
        <title>${b.label} | ${b.count} 次 | 累计 ${b.total_duration_sec}s | 最大 +${b.max_delta_db} dB</title>
        <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="3" fill="${fill}" opacity="${b.count === 0 ? 0.5 : 0.9}" />
        ${b.count > 0 ? `<text x="${(x + barW / 2).toFixed(1)}" y="${(y - 6).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="600" fill="var(--ink-primary)" class="mono">${b.count}</text>` : ""}
        <text x="${(x + barW / 2).toFixed(1)}" y="${height - 10}" text-anchor="middle" font-size="10.5" fill="var(--ink-muted)" class="mono">${idx % 2 === 0 ? String(b.hour).padStart(2, "0") : ""}</text>
      </g>
    `;
  });

  wrap.innerHTML = `
    <svg class="hourly-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
      <rect x="${nightStart.toFixed(1)}" y="${padT}" width="${nightW.toFixed(1)}" height="${chartH}" rx="4" fill="var(--accent-soft)" opacity="0.55" />
      ${grid}
      <line x1="${padL}" y1="${padT + chartH}" x2="${width - padR}" y2="${padT + chartH}" stroke="var(--border-strong)" stroke-width="1" />
      ${bars}
    </svg>
  `;
}

/* Taxonomy Loading & Select Population */
async function loadTaxonomy() {
  const tax = await apiFetch("/api/taxonomy");
  state.taxonomy = tax;
  state.categoryLookup = {};
  state.directionLookup = {};

  tax.category_groups.forEach((g) => {
    g.items.forEach((item) => {
      state.categoryLookup[item.code] = { ...item, group: g.group, group_label: g.group_label };
    });
  });
  tax.directions.forEach((d) => {
    state.directionLookup[d.code] = d;
  });

  const buildCategoryOptions = (placeholder, includeUnclassified = false) => {
    let html = `<option value="">${placeholder}</option>`;
    if (includeUnclassified) {
      html += `<option value="unclassified">未分类（待打标）</option>`;
    }
    tax.category_groups.forEach((g) => {
      html += `<optgroup label="${escapeHtml(g.group_label)}">`;
      g.items.forEach((item) => {
        html += `<option value="${escapeHtml(item.code)}">${escapeHtml(item.label)}</option>`;
      });
      html += `</optgroup>`;
    });
    return html;
  };

  const buildDirectionOptions = (placeholder) => {
    let html = `<option value="">${placeholder}</option>`;
    tax.directions.forEach((d) => {
      html += `<option value="${escapeHtml(d.code)}">${escapeHtml(d.label)}</option>`;
    });
    return html;
  };

  $("rev-label").innerHTML = buildCategoryOptions("-- 选择细化噪声类型 --");
  $("drw-rev-label").innerHTML = buildCategoryOptions("-- 选择细化噪声类型 --");
  $("filter-label").innerHTML = buildCategoryOptions("全部噪声类型", true);

  $("rev-direction").innerHTML = buildDirectionOptions("-- 选择声源方位 --");
  $("drw-rev-direction").innerHTML = buildDirectionOptions("-- 声源方位归属 --");
  $("filter-direction").innerHTML = buildDirectionOptions("全部方位归属");
}

function getCategoryDisplay(code) {
  if (!code) return "未分类";
  return state.categoryLookup[code]?.label || code;
}

function getDirectionDisplay(code, short = true) {
  if (!code) return "方位待定";
  const d = state.directionLookup[code];
  return d ? (short ? d.short : d.label) : code;
}

function renderTaxonomyCharts(categoryCounts, directionCounts, total) {
  const denom = Math.max(1, total);
  const groupFill = {
    impact: "fill-low",
    vocal: "fill-high",
    media: "fill-amber",
    domestic: "",
    ambient: "fill-muted",
    unclassified: "fill-muted",
  };

  const catWrap = $("category-rank-list");
  if (!categoryCounts || !categoryCounts.length) {
    catWrap.innerHTML = `<div class="empty-state" style="min-height:110px"><p>暂无分类统计数据</p></div>`;
  } else {
    catWrap.innerHTML = categoryCounts
      .slice(0, 6)
      .map((item) => {
        const pct = Math.round((item.count / denom) * 100);
        const fillCls = groupFill[item.group] || "";
        return `
          <button type="button" class="rank-item" data-filter-cat="${escapeHtml(item.code)}">
            <div class="rank-top">
              <span class="rank-name">
                <span>${escapeHtml(item.label)}</span>
                <span class="pill-tag" style="font-size:11px; padding:1px 7px">${escapeHtml(item.group_label)}</span>
              </span>
              <span class="rank-nums mono">${item.count} 次 · ${item.total_duration_sec.toFixed(1)}s (${pct}%)</span>
            </div>
            <div class="rank-track"><div class="rank-fill ${fillCls}" style="width:${pct}%"></div></div>
          </button>
        `;
      })
      .join("");
    catWrap.querySelectorAll("button[data-filter-cat]").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("filter-label").value = btn.dataset.filterCat;
        $("filter-direction").value = "";
        switchTab("evidence");
        loadEvents();
      });
    });
  }

  const dirFill = {
    upstairs: "",
    diagonal_upstairs: "fill-low",
    downstairs: "fill-amber",
    adjacent: "fill-high",
    uncertain: "fill-muted",
  };

  const dirWrap = $("direction-rank-list");
  if (!directionCounts || !directionCounts.length) {
    dirWrap.innerHTML = `<div class="empty-state" style="min-height:110px"><p>暂无方位归属统计</p></div>`;
  } else {
    dirWrap.innerHTML = directionCounts
      .slice(0, 6)
      .map((item) => {
        const pct = Math.round((item.count / denom) * 100);
        const fillCls = dirFill[item.code] ?? "fill-muted";
        return `
          <button type="button" class="rank-item" data-filter-dir="${escapeHtml(item.code)}">
            <div class="rank-top">
              <span class="rank-name">${escapeHtml(item.label)}</span>
              <span class="rank-nums mono">${item.count} 次 (${pct}%)</span>
            </div>
            <div class="rank-track"><div class="rank-fill ${fillCls}" style="width:${pct}%"></div></div>
          </button>
        `;
      })
      .join("");
    dirWrap.querySelectorAll("button[data-filter-dir]").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("filter-direction").value = btn.dataset.filterDir;
        $("filter-label").value = "";
        switchTab("evidence");
        loadEvents();
      });
    });
  }
}

/* Load Overview */
async function loadOverview() {
  const q = state.selectedDate ? `?date=${encodeURIComponent(state.selectedDate)}` : "";
  const ov = await apiFetch(`/api/overview${q}`);
  state.overview = ov;

  $("demo-badge").classList.toggle("hidden", !ov.demo_mode);
  $("rail-node-id").textContent = ov.node_id;
  $("rail-avatar").textContent = ov.node_id.slice(0, 2).toUpperCase();
  $("rail-cal-schedule").textContent = `${ov.device_calibrated ? "已校准 SPL" : "未校准 dBFS"} · ${ov.schedule_start}–${ov.schedule_end}`;

  const unverified = Math.max(0, ov.total_events - ov.verified_events);
  const navCount = $("nav-unverified-count");
  navCount.textContent = String(unverified);
  navCount.classList.toggle("hidden", unverified === 0);

  const dateSelect = $("global-date-select");
  const prevVal = state.selectedDate;
  dateSelect.innerHTML = `<option value="">全部监测日期 (${ov.available_dates.length}天)</option>` +
    ov.available_dates.map((d) => `<option value="${d}">${d}</option>`).join("");
  dateSelect.value = prevVal;

  $("kpi-total-events").textContent = String(ov.total_events);
  $("kpi-verified-ratio").textContent = `${ov.verified_events} / ${ov.total_events} 已人工复核`;
  $("kpi-total-duration").textContent = `${ov.total_duration_sec.toFixed(1)} s`;
  $("kpi-schedule-window").textContent = `监测窗 ${ov.schedule_start}–${ov.schedule_end}`;
  $("kpi-max-delta").textContent = `+${ov.max_delta_db.toFixed(1)} dB`;
  $("kpi-cal-status").textContent = ov.device_calibrated ? "已校准声压级 dB SPL" : "相对静音基线（未校准 dBFS）";
  $("kpi-corroboration-count").textContent = `${ov.corroboration_count} 条`;

  const dot = $("topbar-chain-dot");
  dot.className = "live-dot" + (ov.chain_valid ? "" : " danger");
  $("summary-pill-text").textContent = ov.chain_valid
    ? (unverified > 0 ? `哈希链完整 · ${unverified} 条待复核` : "全部证据已复核且哈希链完整")
    : `哈希链异常 (${ov.chain_errors.length})`;

  renderBandDonut(ov.band_counts, ov.total_events);
  renderHourlyChart(ov.hourly_histogram);
  renderTaxonomyCharts(ov.category_counts, ov.direction_counts, ov.total_events);
}

/* Load Events (Overview Table + Evidence Vault List) */
async function loadEvents() {
  const params = new URLSearchParams();
  if (state.selectedDate) params.set("date", state.selectedDate);
  const band = $("filter-band").value;
  const verified = $("filter-verified").value;
  const label = $("filter-label").value;
  const direction = $("filter-direction").value;
  if (band) params.set("band", band);
  if (verified) params.set("verified", verified);
  if (label) params.set("label", label);
  if (direction) params.set("direction", direction);

  const events = await apiFetch(`/api/events?${params.toString()}`);
  state.events = events;
  $("vault-count-pill").textContent = `${events.length} 条`;

  // Overview Table
  const tbody = $("overview-events-tbody");
  if (!events.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--ink-muted); padding:28px">暂无声学存证记录</td></tr>`;
  } else {
    tbody.innerHTML = events
      .slice(0, 8)
      .map((ev) => {
        const r = ev.raw_evidence;
        const m = ev.algo_metrics;
        const h = ev.human_review;
        const bMeta = BAND_LABELS[m.trigger_band] || { short: m.trigger_band, cls: "" };
        const catText = h.label ? getCategoryDisplay(h.label) : bMeta.short;
        const dirText = getDirectionDisplay(h.direction, true);
        const dirCls = h.direction === "upstairs" ? "pill-low" : h.direction === "uncertain" || !h.direction ? "" : "pill-amber";
        const revPill = h.verified
          ? `<span class="pill-tag pill-ok">已复核</span>`
          : `<span class="pill-tag pill-amber">待复核</span>`;
        return `
          <tr data-drawer-id="${ev.event_id}">
            <td class="mono" style="font-weight:600">${formatShortTime(r.trigger_time)}</td>
            <td><span class="pill-tag ${bMeta.cls}">${escapeHtml(catText)}</span></td>
            <td><span class="pill-tag ${dirCls}">${escapeHtml(dirText)}</span></td>
            <td>${revPill}</td>
            <td class="mono num-col">${m.duration_sec.toFixed(1)} s</td>
            <td class="mono num-col" style="font-weight:600">+${m.delta_db.toFixed(1)} dB</td>
          </tr>
        `;
      })
      .join("");
    tbody.querySelectorAll("tr[data-drawer-id]").forEach((tr) => {
      tr.addEventListener("click", () => openEvidenceDrawer(tr.dataset.drawerId));
    });
  }

  // Corroboration event select
  const corSel = $("cor-event-id");
  const curCorVal = corSel.value;
  corSel.innerHTML = `<option value="">-- 不关联特定音频 --</option>` +
    events.map((e) => `<option value="${e.event_id}">${formatShortTime(e.raw_evidence.trigger_time)} · ${e.event_id}</option>`).join("");
  corSel.value = curCorVal;

  // Vault List
  const listEl = $("event-list");
  if (!events.length) {
    listEl.innerHTML = `<div class="empty-state"><p>当前筛选条件下暂无记录。</p></div>`;
    $("inspector-empty").classList.remove("hidden");
    $("inspector-content").classList.add("hidden");
    state.selectedEventId = null;
    return;
  }

  listEl.innerHTML = events
    .map((ev) => {
      const m = ev.algo_metrics;
      const h = ev.human_review;
      const bMeta = BAND_LABELS[m.trigger_band] || { short: m.trigger_band, cls: "" };
      const selected = ev.event_id === state.selectedEventId ? "selected" : "";
      const catText = h.label ? getCategoryDisplay(h.label) : bMeta.short;
      const dirText = h.direction ? ` · ${getDirectionDisplay(h.direction, true)}` : "";
      const revPill = h.verified
        ? `<span class="pill-tag pill-ok">已复核</span>`
        : `<span class="pill-tag pill-amber">待复核</span>`;
      return `
        <button type="button" class="vault-item ${selected}" data-id="${ev.event_id}">
          <div class="vault-item-top">
            <span class="mono">${formatShortTime(ev.raw_evidence.trigger_time)}</span>
            ${revPill}
          </div>
          <div class="vault-item-bottom">
            <span>${escapeHtml(catText)}${escapeHtml(dirText)}</span>
            <strong class="mono">+${m.delta_db.toFixed(1)} dB · ${m.duration_sec.toFixed(1)}s</strong>
          </div>
        </button>
      `;
    })
    .join("");

  listEl.querySelectorAll(".vault-item").forEach((btn) => {
    btn.addEventListener("click", () => selectVaultEvent(btn.dataset.id));
  });

  if (!state.selectedEventId || !events.some((e) => e.event_id === state.selectedEventId)) {
    selectVaultEvent(events[0].event_id);
  } else {
    renderVaultInspector(events.find((e) => e.event_id === state.selectedEventId));
  }
}

/* Vault Inspector */
function selectVaultEvent(eventId) {
  state.selectedEventId = eventId;
  state.playheadRatio = 0;
  document.querySelectorAll(".vault-item").forEach((el) => {
    el.classList.toggle("selected", el.dataset.id === eventId);
  });
  const ev = state.events.find((e) => e.event_id === eventId);
  if (ev) {
    renderVaultInspector(ev);
    loadVaultWaveform(eventId);
  }
}

function setVaultAudioMode(mode) {
  state.audioMode = mode === "raw" ? "raw" : "desensitized";
  $("btn-audio-desens").className = "mini-seg-btn" + (state.audioMode === "desensitized" ? " active" : "");
  $("btn-audio-raw").className = "mini-seg-btn" + (state.audioMode === "raw" ? " active warn-active" : "");
  if (!state.selectedEventId) return;
  const isRaw = state.audioMode === "raw";
  $("audio-mode-hint").textContent = isRaw
    ? "警告：本地原始无损音频（仅限个人核实，严禁外发）"
    : "当前播放：≤250 Hz 低通脱敏副本（消除清晰人声对话）";
  $("event-audio-player").src = `/api/events/${encodeURIComponent(state.selectedEventId)}/audio?mode=${isRaw ? "raw" : "desensitized"}`;
}

function renderVaultInspector(ev) {
  $("inspector-empty").classList.add("hidden");
  $("inspector-content").classList.remove("hidden");

  const r = ev.raw_evidence;
  const m = ev.algo_metrics;
  const h = ev.human_review;
  const bMeta = BAND_LABELS[m.trigger_band] || { text: m.trigger_band, cls: "" };

  $("ins-event-id").textContent = ev.event_id;
  const pill = $("ins-band-pill");
  pill.textContent = bMeta.text;
  pill.className = `pill-tag ${bMeta.cls}`;

  $("ins-time-range").textContent = `${formatShortTime(r.start_time)} → ${formatShortTime(r.end_time)}（触发: ${formatShortTime(r.trigger_time)}）`;
  $("ins-peak").textContent = `${m.peak_dbfs.toFixed(1)} dBFS`;
  $("ins-delta").textContent = `+${m.delta_db.toFixed(1)} dB`;
  $("ins-duration").textContent = `${m.duration_sec.toFixed(1)} s`;

  $("ins-node").textContent = `${r.node_id} (${r.location_tag})`;
  $("ins-sr-cal").textContent = `${r.sample_rate} Hz · ${r.device_calibrated ? "已校准" : "未校准 (dBFS)"}`;
  $("ins-sha256").textContent = r.sha256;
  $("ins-chain-hash").textContent = r.chain_hash || "-";

  $("rev-label").value = h.label || "";
  $("rev-direction").value = h.direction || "";
  $("rev-verified").checked = Boolean(h.verified);
  $("rev-notes").value = h.notes || "";

  setVaultAudioMode("desensitized");
}

/* Slide-Over Evidence Drawer */
async function openEvidenceDrawer(eventId) {
  const ev = state.events.find((e) => e.event_id === eventId);
  if (!ev) return;
  state.drawerEventId = eventId;
  state.drawerPlayheadRatio = 0;

  const r = ev.raw_evidence;
  const m = ev.algo_metrics;
  const h = ev.human_review;
  const bMeta = BAND_LABELS[m.trigger_band] || { short: m.trigger_band, cls: "" };

  $("drw-node-meta").textContent = `${r.node_id} · ${r.location_tag} · ${r.sample_rate} Hz`;
  $("drw-event-id").textContent = ev.event_id;
  $("drw-time").textContent = formatShortTime(r.trigger_time);
  $("drw-peak").textContent = `${m.peak_dbfs.toFixed(1)} dBFS`;
  $("drw-delta").textContent = `+${m.delta_db.toFixed(1)} dB`;
  $("drw-dur").textContent = `${m.duration_sec.toFixed(1)} s`;

  const tag = $("drw-band-tag");
  tag.textContent = bMeta.short;
  tag.className = `pill-tag ${bMeta.cls}`;

  $("drw-sha256").textContent = r.sha256;
  $("drw-rev-label").value = h.label || "";
  $("drw-rev-direction").value = h.direction || "";
  $("drw-rev-verified").checked = Boolean(h.verified);
  $("drw-rev-notes").value = h.notes || "";

  setDrawerAudioMode("desensitized");
  $("drawer-scrim").classList.remove("hidden");
  $("evidence-drawer").classList.add("open");

  try {
    state.drawerWaveformData = await apiFetch(`/api/events/${encodeURIComponent(eventId)}/waveform?buckets=120`);
    drawWaveform("drawer-waveform-canvas", state.drawerWaveformData, state.drawerPlayheadRatio);
  } catch {
    state.drawerWaveformData = null;
  }
}

function closeEvidenceDrawer() {
  $("evidence-drawer").classList.remove("open");
  $("drawer-scrim").classList.add("hidden");
  $("drawer-audio-player").pause();
}

function setDrawerAudioMode(mode) {
  state.drawerAudioMode = mode === "raw" ? "raw" : "desensitized";
  $("drw-audio-desens").className = "mini-seg-btn" + (state.drawerAudioMode === "desensitized" ? " active" : "");
  $("drw-audio-raw").className = "mini-seg-btn" + (state.drawerAudioMode === "raw" ? " active warn-active" : "");
  if (!state.drawerEventId) return;
  const isRaw = state.drawerAudioMode === "raw";
  $("drw-audio-hint").textContent = isRaw ? "本地原件（严禁外发）" : "≤250 Hz 低通脱敏副本";
  $("drawer-audio-player").src = `/api/events/${encodeURIComponent(state.drawerEventId)}/audio?mode=${isRaw ? "raw" : "desensitized"}`;
}

/* Shared Oscilloscope Waveform Renderer */
async function loadVaultWaveform(eventId) {
  try {
    state.waveformData = await apiFetch(`/api/events/${encodeURIComponent(eventId)}/waveform?buckets=160`);
    drawWaveform("waveform-canvas", state.waveformData, state.playheadRatio);
  } catch {
    state.waveformData = null;
  }
}

function drawWaveform(canvasId, wf, playheadRatio) {
  if (!wf) return;
  const canvas = $(canvasId);
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 420;
  const cssH = canvasId === "drawer-waveform-canvas" ? 128 : 148;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, cssW, cssH);

  const styles = getComputedStyle(document.documentElement);
  const lowColor = styles.getPropertyValue("--signal-low").trim();
  const highColor = styles.getPropertyValue("--signal-high").trim();
  const ruleColor = styles.getPropertyValue("--border-rule").trim();
  const accentColor = styles.getPropertyValue("--accent").trim();
  const mutedInk = styles.getPropertyValue("--ink-muted").trim();

  const midY = cssH / 2;

  ctx.strokeStyle = ruleColor;
  ctx.lineWidth = 1;
  [0.25, 0.5, 0.75].forEach((r) => {
    const y = Math.round(cssH * r) + 0.5;
    ctx.beginPath();
    ctx.setLineDash(r === 0.5 ? [] : [3, 3]);
    ctx.moveTo(0, y);
    ctx.lineTo(cssW, y);
    ctx.stroke();
  });
  ctx.setLineDash([]);

  ctx.fillStyle = mutedInk;
  ctx.font = "10px monospace";
  [0.25, 0.5, 0.75].forEach((r) => {
    const x = Math.round(cssW * r) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, cssH);
    ctx.stroke();
    ctx.fillText(`${(wf.duration_sec * r).toFixed(1)}s`, x + 4, 12);
  });

  const n = wf.buckets;
  const step = cssW / Math.max(1, n);
  for (let i = 0; i < n; i++) {
    const x = i * step + 0.8;
    const w = Math.max(1.4, step - 1.4);
    const lowH = wf.low_energy[i] * (midY - 12);
    const highH = wf.high_energy[i] * (midY - 12);

    ctx.fillStyle = lowColor;
    ctx.fillRect(x, midY - lowH, w, Math.max(1, lowH));
    ctx.fillStyle = highColor;
    ctx.fillRect(x, midY + 1, w, Math.max(1, highH));
  }

  if (playheadRatio > 0) {
    const px = Math.min(cssW - 1, Math.max(1, playheadRatio * cssW));
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, cssH);
    ctx.stroke();

    ctx.fillStyle = accentColor;
    ctx.beginPath();
    ctx.moveTo(px - 5, 0);
    ctx.lineTo(px + 5, 0);
    ctx.lineTo(px, 7);
    ctx.closePath();
    ctx.fill();

    const secLabel = `${(wf.duration_sec * playheadRatio).toFixed(1)}s`;
    ctx.font = "bold 11px monospace";
    const labelX = px > cssW - 48 ? px - 40 : px + 5;
    ctx.fillText(secLabel, labelX, cssH - 6);
  }
}

function wireWaveformSeek(canvasId, playerId, getWf, setRatio) {
  const player = $(playerId);
  const canvas = $(canvasId);

  player.addEventListener("timeupdate", () => {
    if (player.duration && Number.isFinite(player.duration) && (!player.paused || player.currentTime > 0)) {
      const ratio = player.currentTime / player.duration;
      setRatio(ratio);
      drawWaveform(canvasId, getWf(), ratio);
    }
  });

  canvas.addEventListener("click", (e) => {
    const wf = getWf();
    if (!wf) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.max(0.005, Math.min(0.995, (e.clientX - rect.left) / rect.width));
    setRatio(ratio);
    drawWaveform(canvasId, wf, ratio);
    const applySeek = () => {
      const targetDur = player.duration && Number.isFinite(player.duration) ? player.duration : wf.duration_sec;
      if (targetDur > 0) player.currentTime = ratio * targetDur;
    };
    if (player.readyState >= 1) applySeek();
    else {
      player.addEventListener("loadedmetadata", applySeek, { once: true });
      player.load();
    }
  });
}

/* Corroboration & Dual-Node */
async function loadCorroborations() {
  const q = state.selectedDate ? `?date=${encodeURIComponent(state.selectedDate)}` : "";
  const logs = await apiFetch(`/api/corroborations${q}`);
  const wrap = $("corroboration-list");

  if (!logs.length) {
    wrap.innerHTML = `<div class="empty-state"><p>暂无外部互证记录。建议在联系物业、社区或报警后及时录入留痕。</p></div>`;
    return;
  }

  wrap.innerHTML = logs
    .map((item) => {
      const meta = COR_LABELS[item.category] || { text: item.category, cls: "" };
      return `
        <div class="timeline-card">
          <div class="section-row-between">
            <span class="pill-tag ${meta.cls}">${meta.text}</span>
            <span class="mono micro-caption">${formatShortTime(item.logged_at)}</span>
          </div>
          <p class="timeline-summary">${escapeHtml(item.summary)}</p>
          <div class="timeline-meta mono">
            <span>编号: ${escapeHtml(item.reference_code || "-")}</span>
            <span>关联事件: ${escapeHtml(item.related_event_id || "未指定")}</span>
          </div>
        </div>
      `;
    })
    .join("");
}

async function loadCorrelateAndConfig() {
  const q = state.selectedDate ? `?date=${encodeURIComponent(state.selectedDate)}` : "";
  const [pairs, cfg] = await Promise.all([
    apiFetch(`/api/correlate${q}`),
    apiFetch("/api/config"),
  ]);

  const tableWrap = $("correlate-table-wrap");
  if (!pairs.length) {
    tableWrap.innerHTML = `<div class="empty-state"><p>当前未检测到可匹配的 ceiling 与 room_center 双节点事件。</p></div>`;
  } else {
    const assessLabel = {
      likely_ceiling_source: `<span class="pill-tag pill-low">指向正上方/天花板</span>`,
      likely_room_or_floor_source: `<span class="pill-tag pill-amber">偏向室内/本户源</span>`,
      ambiguous_diffuse: `<span class="pill-tag">漫反射/环境声</span>`,
    };
    tableWrap.innerHTML = `
      <table class="archival-table">
        <thead>
          <tr>
            <th>顶部节点</th>
            <th>中部节点</th>
            <th>时差</th>
            <th>Δ低频</th>
            <th>Δ高频</th>
            <th>方位推断</th>
          </tr>
        </thead>
        <tbody class="mono">
          ${pairs
            .map(
              (p) => `
            <tr>
              <td><span class="cell-truncate" title="${escapeHtml(p.ceiling_event_id)}">${escapeHtml(p.ceiling_event_id)}</span></td>
              <td><span class="cell-truncate" title="${escapeHtml(p.center_event_id)}">${escapeHtml(p.center_event_id)}</span></td>
              <td>${p.time_diff_sec.toFixed(2)}s</td>
              <td>${p.low_band_diff_db >= 0 ? "+" : ""}${p.low_band_diff_db.toFixed(1)} dB</td>
              <td>${p.high_band_diff_db >= 0 ? "+" : ""}${p.high_band_diff_db.toFixed(1)} dB</td>
              <td>${assessLabel[p.assessment] || p.assessment}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    `;
  }

  $("config-grid").innerHTML = `
    <div><span class="meta-label">节点 ID / 位置</span><strong>${escapeHtml(cfg.node_id)} (${escapeHtml(cfg.location_tag)})</strong></div>
    <div><span class="meta-label">采样率 / 计量状态</span><strong>${cfg.sample_rate} Hz · ${cfg.calibrated ? "已校准" : "未校准 dBFS"}</strong></div>
    <div><span class="meta-label">低频撞击带</span><strong>${cfg.low_band_hz[0]}–${cfg.low_band_hz[1]} Hz (Δ≥${cfg.low_delta_db} dB)</strong></div>
    <div><span class="meta-label">中高频人声带</span><strong>${cfg.high_band_hz[0]}–${cfg.high_band_hz[1]} Hz (Δ≥${cfg.high_delta_db} dB)</strong></div>
    <div><span class="meta-label">夜间自动窗口</span><strong>${cfg.schedule_enabled ? `${cfg.schedule_start} – ${cfg.schedule_end}` : "未启用"}</strong></div>
    <div><span class="meta-label">预录 / 尾录 / 清理</span><strong>${cfg.pre_roll_sec}s / ${cfg.post_roll_sec}s / raw ${cfg.raw_retention_days} 天</strong></div>
  `;
}

/* Actions */
async function handleVerifyChain() {
  const btn = $("btn-verify-chain-top");
  btn.disabled = true;
  $("chain-badge-text").textContent = "校验中...";
  try {
    const res = await apiFetch("/api/chain/verify", { method: "POST" });
    if (res.valid) {
      $("chain-badge-text").textContent = `哈希链完整 (${res.checked_events})`;
      showToast(`全库 ${res.checked_events} 条记录与 FLAC SHA-256 校验通过`, "success");
    } else {
      $("chain-badge-text").textContent = `异常 (${res.errors.length})`;
      showToast(`检测到 ${res.errors.length} 处完整性异常，请核查`, "error");
    }
    await loadOverview();
  } catch (err) {
    showToast(`校验失败: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function saveReviewFor(eventId, verified, label, direction, notes, btnId) {
  if (!eventId) return;
  const btn = $(btnId);
  btn.disabled = true;
  try {
    await apiFetch(`/api/events/${encodeURIComponent(eventId)}/review`, {
      method: "PATCH",
      body: JSON.stringify({ verified, label: label || null, direction: direction || "", notes }),
    });
    showToast("人工复核记录已保存（原始文件与哈希不变）", "success");
    await Promise.all([loadOverview(), loadEvents()]);
  } catch (err) {
    showToast(`保存失败: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function handleAddCorroboration(e) {
  e.preventDefault();
  const btn = $("btn-add-corroboration");
  btn.disabled = true;
  try {
    await apiFetch("/api/corroborations", {
      method: "POST",
      body: JSON.stringify({
        category: $("cor-category").value,
        reference_code: $("cor-ref").value.trim(),
        related_event_id: $("cor-event-id").value,
        summary: $("cor-summary").value.trim(),
      }),
    });
    $("cor-ref").value = "";
    $("cor-summary").value = "";
    showToast("外部互证记录已写入台账", "success");
    await Promise.all([loadOverview(), loadCorroborations()]);
  } catch (err) {
    showToast(`写入失败: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function triggerShareExport() {
  const payload = {
    date: state.selectedDate || null,
    include_lowpass_audio: $("exp-include-audio").checked,
    cutoff_hz: Number($("exp-cutoff").value),
    include_notes: $("exp-include-notes").checked,
  };
  const btn = $("btn-run-export");
  btn.disabled = true;
  try {
    const res = await apiFetch("/api/export/share", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const kb = (res.size_bytes / 1024).toFixed(1);
    const box = $("export-result");
    box.classList.remove("hidden");
    box.innerHTML = `
      <div>
        <div class="section-label mono">${escapeHtml(res.filename)}</div>
        <div class="micro-caption">大小 ${kb} KB · 范围 ${escapeHtml(res.scope)} · ${res.include_lowpass_audio ? "含低通脱敏音频" : "纯指标与哈希清单"}</div>
      </div>
      <a class="btn btn-forest" href="${res.download_url}" download>下载 ZIP</a>
    `;
    showToast(`脱敏包 ${res.filename} 已生成 (${kb} KB)`, "success");
  } catch (err) {
    showToast(`导出失败: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function refreshAll() {
  await Promise.all([
    loadOverview(),
    loadEvents(),
    loadCorroborations(),
    loadCorrelateAndConfig(),
  ]);
}

function bindEvents() {
  document.querySelector(".rail-nav").addEventListener("click", (e) => {
    const btn = e.target.closest(".rail-item");
    if (btn && btn.dataset.tab) switchTab(btn.dataset.tab);
  });

  $("btn-open-cmd").addEventListener("click", openCommandPalette);
  $("cmd-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "cmd-backdrop") closeCommandPalette();
  });
  $("cmd-input").addEventListener("input", (e) => renderCommandList(e.target.value));

  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if ($("cmd-backdrop").classList.contains("hidden")) openCommandPalette();
      else closeCommandPalette();
    } else if (e.key === "Escape") {
      closeCommandPalette();
      closeEvidenceDrawer();
    }
  });

  $("btn-close-drawer").addEventListener("click", closeEvidenceDrawer);
  $("drawer-scrim").addEventListener("click", closeEvidenceDrawer);

  wireWaveformSeek("waveform-canvas", "event-audio-player", () => state.waveformData, (r) => { state.playheadRatio = r; });
  wireWaveformSeek("drawer-waveform-canvas", "drawer-audio-player", () => state.drawerWaveformData, (r) => { state.drawerPlayheadRatio = r; });

  $("btn-audio-desens").addEventListener("click", () => setVaultAudioMode("desensitized"));
  $("btn-audio-raw").addEventListener("click", () => setVaultAudioMode("raw"));
  $("drw-audio-desens").addEventListener("click", () => setDrawerAudioMode("desensitized"));
  $("drw-audio-raw").addEventListener("click", () => setDrawerAudioMode("raw"));

  const copySha = async (elId) => {
    const sha = $(elId).textContent;
    if (!sha) return;
    try {
      await navigator.clipboard.writeText(sha);
      showToast("已复制原始 FLAC SHA-256 摘要", "success");
    } catch {
      showToast("复制失败，请手动选中复制", "error");
    }
  };
  $("btn-copy-sha").addEventListener("click", () => copySha("ins-sha256"));
  $("drw-copy-sha").addEventListener("click", () => copySha("drw-sha256"));

  $("review-form").addEventListener("submit", (e) => {
    e.preventDefault();
    saveReviewFor(
      state.selectedEventId,
      $("rev-verified").checked,
      $("rev-label").value,
      $("rev-direction").value,
      $("rev-notes").value,
      "btn-save-review"
    );
  });

  $("drawer-review-form").addEventListener("submit", (e) => {
    e.preventDefault();
    saveReviewFor(
      state.drawerEventId,
      $("drw-rev-verified").checked,
      $("drw-rev-label").value,
      $("drw-rev-direction").value,
      $("drw-rev-notes").value,
      "drw-save-review"
    );
  });

  $("global-date-select").addEventListener("change", (e) => {
    state.selectedDate = e.target.value;
    refreshAll();
  });
  $("filter-band").addEventListener("change", loadEvents);
  $("filter-verified").addEventListener("change", loadEvents);
  $("filter-label").addEventListener("change", loadEvents);
  $("filter-direction").addEventListener("change", loadEvents);
  $("btn-verify-chain-top").addEventListener("click", handleVerifyChain);
  $("corroboration-form").addEventListener("submit", handleAddCorroboration);
  $("export-form").addEventListener("submit", (e) => { e.preventDefault(); triggerShareExport(); });
  $("btn-topbar-export").addEventListener("click", () => { switchTab("export"); triggerShareExport(); });
  $("btn-goto-evidence").addEventListener("click", () => switchTab("evidence"));
  $("btn-refresh-correlate").addEventListener("click", loadCorrelateAndConfig);

  $("exp-cutoff").addEventListener("input", (e) => { $("exp-cutoff-val").textContent = `${e.target.value} Hz`; });
  $("exp-include-audio").addEventListener("change", (e) => { $("cutoff-slider-wrap").style.opacity = e.target.checked ? "1" : "0.45"; });
}

async function bootstrap() {
  initTheme();
  bindEvents();
  try {
    await loadTaxonomy();
    await refreshAll();
  } catch (err) {
    showToast(`初始化连接失败: ${err.message}`, "error");
  }
}

bootstrap();
