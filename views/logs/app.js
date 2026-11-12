// HA 指令助手 · 日志页
// 从内存缓冲拉取最近日志；bridge 不可用时退化为直接请求扩展接口。

const PLUGIN = "astrbot_plugin_ha_agent";
const REFRESH_MS = 3000;

const listEl = document.getElementById("list");
const emptyEl = document.getElementById("empty");
const countEl = document.getElementById("count");
const levelEl = document.getElementById("level");
const statusEl = document.getElementById("status");
const banner = document.getElementById("banner");
const autoEl = document.getElementById("auto");

let bridge = null;
let loading = false;
let timer = null;

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

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.dataset.kind = kind;
}

function showBanner(text) {
  banner.textContent = text;
  banner.hidden = !text;
}

function isNearBottom() {
  return listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight < 40;
}

function levelClass(level) {
  const key = String(level || "").toLowerCase();
  return ["debug", "info", "warning", "error", "critical"].includes(key)
    ? key
    : "info";
}

function render(entries) {
  const stick = isNearBottom();
  listEl.textContent = "";

  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = `log-row ${levelClass(entry.level)}`;

    const time = document.createElement("span");
    time.className = "log-time";
    time.textContent = String(entry.time || "").replace("T", " ").slice(5);

    const level = document.createElement("span");
    level.className = "log-level";
    level.textContent = entry.level || "INFO";

    const message = document.createElement("span");
    message.className = "log-message";
    message.textContent = entry.message || "";

    row.append(time, level, message);
    listEl.appendChild(row);
  }

  emptyEl.hidden = entries.length > 0;
  if (stick) listEl.scrollTop = listEl.scrollHeight;
}

async function loadLogs(manual = false) {
  if (loading) return;
  if (!manual && document.visibilityState === "hidden") return;
  loading = true;
  try {
    const data = await apiGet("logs", { limit: 300, level: levelEl.value });
    render(data.entries || []);
    countEl.textContent = `${data.count ?? 0} / ${data.capacity ?? 400}`;
    if (Array.isArray(data.attached)) {
      countEl.title = `监听日志器：${data.attached.join("、") || "无"}`;
    }
    showBanner("");
    if (manual) setStatus("已刷新", "ok");
  } catch (error) {
    showBanner(`读取日志失败：${error.message}`);
    setStatus("", "");
  } finally {
    loading = false;
  }
}

async function clearLogs() {
  try {
    await apiPost("logs/clear", {});
    setStatus("已清空", "ok");
    await loadLogs(true);
  } catch (error) {
    setStatus(`清空失败：${error.message}`, "err");
  }
}

function schedule() {
  if (timer) clearInterval(timer);
  timer = null;
  if (!autoEl.checked) return;
  timer = setInterval(() => loadLogs(false), REFRESH_MS);
}

document.getElementById("refresh").addEventListener("click", () => loadLogs(true));
document.getElementById("clear").addEventListener("click", clearLogs);
levelEl.addEventListener("change", () => loadLogs(true));
autoEl.addEventListener("change", schedule);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") loadLogs(false);
});

schedule();
loadLogs(true);
