// HA 指令助手 · 设置页（配置 / 日志 两个 Tab）
// 与后端通过 Dashboard 的插件 bridge 通信；bridge 不可用时退化为直接请求
// /api/v1/plugins/extensions/<插件名>/<endpoint>，方便本地调试。

const PLUGIN = "astrbot_plugin_ha_agent";
const LOG_REFRESH_MS = 3000;

const TEXT_FIELDS = [
  "ha_url",
  "event_type",
  "mqtt_host",
  "mqtt_username",
  "mqtt_topic_prefix",
];
const SECRET_FIELDS = ["ha_token", "mqtt_password"];
const NUMBER_FIELDS = ["inbound_port", "mqtt_port"];
const MULTI_FIELDS = ["notify_umos", "allowed_umos", "blocked_umos"];

const form = document.getElementById("form");
const banner = document.getElementById("banner");
const statusEl = document.getElementById("status");
const reportEl = document.getElementById("report");
const chipOut = document.getElementById("chip-out");
const chipIn = document.getElementById("chip-in");
const versionEl = document.getElementById("version");

const logRows = document.getElementById("log-rows");
const logEmpty = document.getElementById("log-empty");
const logCount = document.getElementById("log-count");
const logLevel = document.getElementById("log-level");
const logStatus = document.getElementById("log-status");
const logAuto = document.getElementById("log-auto");

const tokenState = document.getElementById("token-state");
const tokenReveal = document.getElementById("token-reveal");
const tokenValue = document.getElementById("token-value");

let bridge = null;
let busy = false;
let currentTab = "config";
let knownUmos = [];
let logTimer = null;
let logLoading = false;
const multi = { notify_umos: [], allowed_umos: [], blocked_umos: [] };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function getBridge() {
  if (bridge) return bridge;
  for (let i = 0; i < 30; i += 1) {
    const candidate = window.AstrBotPluginView || window.AstrBotPluginPage;
    if (candidate) {
      try {
        if (typeof candidate.ready === "function") await candidate.ready();
      } catch (error) {
        console.warn("bridge.ready failed", error);
      }
      bridge = candidate;
      return bridge;
    }
    await sleep(100);
  }
  return null;
}

function endpointUrl(endpoint, params = {}) {
  const url = new URL(
    `/api/v1/plugins/extensions/${encodeURIComponent(PLUGIN)}/${endpoint}`,
    window.location.origin,
  );
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    url.searchParams.set(key, String(value));
  }
  return url;
}

function unwrap(payload) {
  if (payload && payload.success === false) {
    throw new Error(payload.error || "请求失败");
  }
  return payload || {};
}

async function apiGet(endpoint, params = {}) {
  const api = await getBridge();
  if (api && typeof api.apiGet === "function") {
    return unwrap(await api.apiGet(endpoint, params));
  }
  const res = await fetch(endpointUrl(endpoint, params), {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return unwrap(await res.json());
}

async function apiPost(endpoint, body = {}) {
  const api = await getBridge();
  if (api && typeof api.apiPost === "function") {
    return unwrap(await api.apiPost(endpoint, body));
  }
  const res = await fetch(endpointUrl(endpoint), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return unwrap(await res.json());
}

// ------------------------------------------------------------------ Tab

function switchTab(name) {
  currentTab = name;
  for (const button of document.querySelectorAll(".tab")) {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.hidden = panel.dataset.panel !== name;
  }
  if (name === "logs") loadLogs(true);
}

// ------------------------------------------------------------------ 配置表单

function checked(name) {
  const el = form.querySelector(`input[name="${name}"]:checked`);
  return el ? el.value : "";
}

function setRadio(name, value) {
  const el = form.querySelector(`input[name="${name}"][value="${value}"]`);
  if (el) el.checked = true;
}

function applyVisibility() {
  const out = checked("outbound_transport");
  const inbound = checked("inbound_transport");

  for (const node of document.querySelectorAll("[data-when]")) {
    const rule = node.dataset.when;
    let visible = false;
    if (rule.startsWith("outbound:")) {
      visible = out === rule.slice("outbound:".length);
    } else if (rule.startsWith("inbound:")) {
      visible = inbound === rule.slice("inbound:".length);
    } else if (rule === "any:mqtt") {
      visible = out === "mqtt" || inbound === "mqtt";
    }
    node.hidden = !visible;
  }
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[ch]);
}

// ------------------------------------------------------------------ 多选会话

function pickerEl(field) {
  return document.querySelector(`.picker[data-field="${field}"]`);
}

function addValue(field, raw) {
  const value = String(raw || "").trim();
  if (!value) return;
  if (!multi[field].includes(value)) multi[field].push(value);
  renderPicker(field);
}

function removeValue(field, value) {
  multi[field] = multi[field].filter((item) => item !== value);
  renderPicker(field);
}

function renderPicker(field) {
  const root = pickerEl(field);
  if (!root) return;

  const chips = root.querySelector("[data-chips]");
  chips.innerHTML = "";
  if (!multi[field].length) {
    const empty = document.createElement("span");
    empty.className = "chip-empty";
    empty.textContent = "未选择";
    chips.appendChild(empty);
  }
  for (const umo of multi[field]) {
    const chip = document.createElement("span");
    chip.className = "chip-item";
    chip.title = umo;
    // 独立一层文本节点，长 UMO 用省略号截断，完整值在 title 里
    const text = document.createElement("span");
    text.className = "chip-text";
    text.textContent = umo;
    chip.appendChild(text);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "chip-x";
    remove.setAttribute("aria-label", `移除 ${umo}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeValue(field, umo));
    chip.appendChild(remove);
    chips.appendChild(chip);
  }

  const select = root.querySelector("[data-select]");
  const selected = new Set(multi[field]);
  const options = ['<option value="">从已知会话中选择…</option>'];
  for (const item of knownUmos) {
    if (selected.has(item.umo)) continue;
    const label = item.label ? `${item.label} · ${item.umo}` : item.umo;
    options.push(
      `<option value="${escapeHtml(item.umo)}">${escapeHtml(label)}</option>`,
    );
  }
  select.innerHTML = options.join("");
}

function wirePickers() {
  for (const field of MULTI_FIELDS) {
    const root = pickerEl(field);
    if (!root) continue;

    const select = root.querySelector("[data-select]");
    const manual = root.querySelector("[data-manual]");

    root.querySelector("[data-add-selected]").addEventListener("click", () => {
      addValue(field, select.value);
    });
    root.querySelector("[data-add-manual]").addEventListener("click", () => {
      addValue(field, manual.value);
      manual.value = "";
    });
    manual.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      addValue(field, manual.value);
      manual.value = "";
    });
    select.addEventListener("change", () => {
      if (!select.value) return;
      addValue(field, select.value);
    });
  }
}

function renderAllPickers() {
  for (const field of MULTI_FIELDS) renderPicker(field);
}

// ------------------------------------------------------------------ 填充/收集

function fill(settings) {
  const data = settings || {};
  setRadio("outbound_transport", data.outbound_transport || "rest");
  setRadio("inbound_transport", data.inbound_transport || "http");

  for (const name of [...TEXT_FIELDS, ...NUMBER_FIELDS]) {
    const el = form.elements[name];
    if (el) el.value = data[name] ?? "";
  }
  form.elements.verify_ssl.checked = Boolean(data.verify_ssl);
  form.elements.reply_on_success.checked = data.reply_on_success !== false;
  form.elements.mqtt_tls.checked = Boolean(data.mqtt_tls);

  for (const name of SECRET_FIELDS) {
    const el = form.elements[name];
    if (!el) continue;
    el.value = "";
    if (data[`${name}_set`]) {
      el.placeholder = "已设置，留空不修改";
    } else {
      el.placeholder = name === "inbound_token" ? "留空自动生成" : "未设置";
    }
  }

  for (const field of MULTI_FIELDS) {
    multi[field] = Array.isArray(data[field]) ? [...data[field]] : [];
  }
  renderAllPickers();

  if (versionEl && data.version) versionEl.textContent = `v${data.version}`;
  renderTokenState(data.token);
  applyVisibility();
}

function collect() {
  const payload = {
    outbound_transport: checked("outbound_transport"),
    inbound_transport: checked("inbound_transport"),
    verify_ssl: form.elements.verify_ssl.checked,
    reply_on_success: form.elements.reply_on_success.checked,
    mqtt_tls: form.elements.mqtt_tls.checked,
  };

  for (const field of MULTI_FIELDS) payload[field] = [...multi[field]];

  for (const name of [...TEXT_FIELDS, ...SECRET_FIELDS, ...NUMBER_FIELDS]) {
    const el = form.elements[name];
    if (!el) continue;
    const value = el.value.trim();
    // 密钥与数字留空表示"不修改"，其它文本留空表示"清空"。
    if (
      value === "" &&
      (SECRET_FIELDS.includes(name) || NUMBER_FIELDS.includes(name))
    ) {
      continue;
    }
    payload[name] = value;
  }
  return payload;
}

// ------------------------------------------------------------------ 回传令牌

function renderTokenState(token) {
  const info = token || {};
  tokenState.textContent = info.set ? `已生成 · ${info.hint}` : "未生成";
}

async function rotateToken() {
  setStatus("生成中…", "busy");
  try {
    const data = await apiPost("settings/token", {});
    const token = data.token || "";
    tokenValue.textContent = token;
    tokenReveal.hidden = false;
    tokenState.textContent = token
      ? `已生成 · ${token.slice(0, 4)}…${token.slice(-4)}`
      : "未生成";
    setStatus("新令牌已生成，请立即复制", "ok");
  } catch (error) {
    setStatus(`生成失败：${error.message}`, "err");
  }
}

async function copyToken() {
  const text = tokenValue.textContent || "";
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    setStatus("令牌已复制到剪贴板", "ok");
  } catch (error) {
    const range = document.createRange();
    range.selectNodeContents(tokenValue);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    setStatus("已选中，请按 Ctrl+C 复制", "ok");
  }
}

// ------------------------------------------------------------------ 状态展示

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.dataset.kind = kind;
}

function showBanner(text) {
  banner.textContent = text;
  banner.hidden = !text;
}

function updateChips(state) {
  if (!state) return;
  if (state.outbound_mode || state.outbound_ready !== undefined) {
    const mode = (state.outbound_mode || checked("outbound_transport")).toUpperCase();
    chipOut.textContent = `出站：${mode}${state.outbound_ready ? " ✓" : " ✗"}`;
  }
  if (state.inbound_mode) {
    const labels = { http: "HTTP", mqtt: "MQTT", off: "关闭" };
    const label = labels[state.inbound_mode] || state.inbound_mode;
    chipIn.textContent = `入站：${label}${state.inbound_running ? " ✓" : ""}`;
  }
}

function renderReport(report) {
  const rows = [
    ["出站", report.outbound],
    ["入站", report.inbound],
  ];
  reportEl.innerHTML = rows
    .map(([label, item]) => {
      if (!item) return "";
      const mark = item.ok ? "✓" : "✗";
      const cls = item.ok ? "ok" : "err";
      return `<div class="report-row"><span class="report-label">${label}</span>
        <span class="report-mode">${item.mode || ""}</span>
        <span class="report-detail ${cls}">${mark} ${escapeHtml(item.detail || "")}</span></div>`;
    })
    .join("");
  reportEl.hidden = false;
  updateChips({
    outbound_mode: report.outbound ? report.outbound.mode : undefined,
    outbound_ready: report.outbound ? report.outbound.ok : undefined,
    inbound_mode: report.inbound ? report.inbound.mode : undefined,
    inbound_running: report.inbound ? report.inbound.ok : undefined,
  });
}

// ------------------------------------------------------------------ 日志表格

function levelClass(level) {
  const key = String(level || "").toLowerCase();
  return ["debug", "info", "warning", "error", "critical"].includes(key)
    ? key
    : "info";
}

function formatTime(value) {
  const text = String(value || "");
  // 2026-11-12T14:09:35+08:00 → 11-12 14:09:35
  return text.length >= 19 ? text.slice(5, 19).replace("T", " ") : text;
}

function renderLogs(entries) {
  logRows.textContent = "";
  for (const entry of entries) {
    const row = document.createElement("tr");
    row.className = levelClass(entry.level);
    row.title = `${entry.time || ""}  ${entry.logger || ""}`;

    const timeCell = document.createElement("td");
    timeCell.className = "col-time";
    timeCell.textContent = formatTime(entry.time);

    const levelCell = document.createElement("td");
    levelCell.className = "col-level";
    const badge = document.createElement("span");
    badge.className = `badge ${levelClass(entry.level)}`;
    badge.textContent = entry.level || "INFO";
    levelCell.appendChild(badge);

    const sourceCell = document.createElement("td");
    sourceCell.className = "col-source";
    sourceCell.textContent = entry.logger || "";

    const messageCell = document.createElement("td");
    messageCell.className = "col-message";
    messageCell.textContent = entry.message || "";

    row.append(timeCell, levelCell, sourceCell, messageCell);
    logRows.appendChild(row);
  }
  logEmpty.hidden = entries.length > 0;
}

async function loadLogs(manual = false) {
  if (logLoading) return;
  if (!manual && currentTab !== "logs") return;
  if (document.visibilityState === "hidden") return;
  logLoading = true;
  try {
    const data = await apiGet("logs", { limit: 300, level: logLevel.value });
    renderLogs(data.entries || []);
    logCount.textContent = `${data.count ?? 0} / ${data.capacity ?? 400}`;
    if (Array.isArray(data.attached)) {
      logCount.title = `监听日志器：${data.attached.join("、") || "无"}`;
    }
    if (manual) logStatus.textContent = "";
  } catch (error) {
    logStatus.textContent = `读取日志失败：${error.message}`;
    logStatus.dataset.kind = "err";
  } finally {
    logLoading = false;
  }
}

async function clearLogs() {
  try {
    await apiPost("logs/clear", {});
    logStatus.textContent = "已清空";
    logStatus.dataset.kind = "ok";
    await loadLogs(true);
  } catch (error) {
    logStatus.textContent = `清空失败：${error.message}`;
    logStatus.dataset.kind = "err";
  }
}

function scheduleLogs() {
  if (logTimer) clearInterval(logTimer);
  logTimer = null;
  if (!logAuto.checked) return;
  logTimer = setInterval(() => loadLogs(false), LOG_REFRESH_MS);
}

// ------------------------------------------------------------------ 动作

async function loadKnownUmos() {
  try {
    const data = await apiGet("settings/umos");
    knownUmos = Array.isArray(data.umos) ? data.umos : [];
  } catch (error) {
    knownUmos = [];
    console.warn("load umos failed", error);
  }
  renderAllPickers();
}

async function load() {
  await loadKnownUmos();
  try {
    const data = await apiGet("settings");
    fill({
      ...(data.settings || {}),
      version: data.version,
      token: data.token,
    });
    updateChips(data.state || {});
    showBanner("");
  } catch (error) {
    showBanner(
      `未能读取当前配置（${error.message}）。下面是默认值，保存会覆盖原有设置。`,
    );
    fill({});
  }
}

async function save(event) {
  event.preventDefault();
  if (busy) return;
  busy = true;
  setStatus("保存中…", "busy");
  try {
    const data = await apiPost("settings", collect());
    fill({ ...(data.settings || {}), token: data.token });
    updateChips(data.state || {});
    setStatus(data.message || "已保存并生效", "ok");
    await loadKnownUmos();
  } catch (error) {
    setStatus(`保存失败：${error.message}`, "err");
  } finally {
    busy = false;
  }
}

async function test() {
  if (busy) return;
  busy = true;
  setStatus("自检中…", "busy");
  try {
    const report = await apiPost("settings/test", {});
    renderReport(report);
    setStatus("自检完成", "ok");
  } catch (error) {
    setStatus(`自检失败：${error.message}`, "err");
  } finally {
    busy = false;
  }
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
}

form.addEventListener("submit", save);
document.getElementById("test").addEventListener("click", test);
for (const radio of document.querySelectorAll(
  'input[name="outbound_transport"], input[name="inbound_transport"]',
)) {
  radio.addEventListener("change", applyVisibility);
}

document.getElementById("token-rotate").addEventListener("click", rotateToken);
document.getElementById("token-copy").addEventListener("click", copyToken);

document.getElementById("log-refresh").addEventListener("click", () => loadLogs(true));
document.getElementById("log-clear").addEventListener("click", clearLogs);
logLevel.addEventListener("change", () => loadLogs(true));
logAuto.addEventListener("change", scheduleLogs);
document.addEventListener("visibilitychange", () => loadLogs(false));

wirePickers();
scheduleLogs();
load();
