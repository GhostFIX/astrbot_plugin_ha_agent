// HA 指令助手 · 设置页
// 与后端通过 Dashboard 的插件 bridge 通信；bridge 不可用时退化为直接请求
// /api/v1/plugins/extensions/<插件名>/<endpoint>，方便本地调试。

const PLUGIN = "astrbot_plugin_ha_agent";

const TEXT_FIELDS = [
  "ha_url",
  "event_type",
  "mqtt_host",
  "mqtt_username",
  "mqtt_topic_prefix",
  "notify_umo",
];
const SECRET_FIELDS = ["ha_token", "mqtt_password", "inbound_token"];
const NUMBER_FIELDS = ["inbound_port", "mqtt_port"];

const form = document.getElementById("form");
const banner = document.getElementById("banner");
const statusEl = document.getElementById("status");
const reportEl = document.getElementById("report");
const chipOut = document.getElementById("chip-out");
const chipIn = document.getElementById("chip-in");
const versionEl = document.getElementById("version");

let bridge = null;
let busy = false;

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

// ------------------------------------------------------------------ 表单状态

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

function fill(settings) {
  const data = settings || {};
  setRadio("outbound_transport", data.outbound_transport || "rest");
  setRadio("inbound_transport", data.inbound_transport || "http");

  for (const name of TEXT_FIELDS) {
    const el = form.elements[name];
    if (el) el.value = data[name] ?? "";
  }
  for (const name of NUMBER_FIELDS) {
    const el = form.elements[name];
    if (el) el.value = data[name] ?? "";
  }
  form.elements.verify_ssl.checked = Boolean(data.verify_ssl);
  form.elements.mqtt_tls.checked = Boolean(data.mqtt_tls);
  form.elements.allowed_umos.value = (data.allowed_umos || []).join("\n");
  form.elements.blocked_umos.value = (data.blocked_umos || []).join("\n");

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
  if (versionEl && data.version) versionEl.textContent = `v${data.version}`;
  applyVisibility();
}

function splitList(value) {
  return String(value || "")
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function collect() {
  const payload = {
    outbound_transport: checked("outbound_transport"),
    inbound_transport: checked("inbound_transport"),
    verify_ssl: form.elements.verify_ssl.checked,
    mqtt_tls: form.elements.mqtt_tls.checked,
    allowed_umos: splitList(form.elements.allowed_umos.value),
    blocked_umos: splitList(form.elements.blocked_umos.value),
  };

  for (const name of [...TEXT_FIELDS, ...SECRET_FIELDS, ...NUMBER_FIELDS]) {
    const el = form.elements[name];
    if (!el) continue;
    const value = el.value.trim();
    // 密钥与数字留空表示"不修改"，其它文本留空表示"清空"。
    if (value === "" && (SECRET_FIELDS.includes(name) || NUMBER_FIELDS.includes(name))) {
      continue;
    }
    payload[name] = value;
  }
  return payload;
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
    outbound_mode: report.outbound?.mode,
    outbound_ready: report.outbound?.ok,
    inbound_mode: report.inbound?.mode,
    inbound_running: report.inbound?.ok,
  });
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

// ------------------------------------------------------------------ 动作

async function load() {
  try {
    const data = await apiGet("settings");
    fill({ ...(data.settings || {}), version: data.version });
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
    fill(data.settings || {});
    updateChips(data.state || {});
    setStatus(data.message || "已保存并生效", "ok");
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

form.addEventListener("submit", save);
document.getElementById("test").addEventListener("click", test);
for (const radio of document.querySelectorAll(
  'input[name="outbound_transport"], input[name="inbound_transport"]',
)) {
  radio.addEventListener("change", applyVisibility);
}

load();
