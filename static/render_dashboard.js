function nowStamp() {
  return new Date().toLocaleString();
}

function setRefreshText(id, prefix) {
  const node = document.getElementById(id);
  if (node) node.textContent = `${prefix} ${nowStamp()}`;
}

function setActionBanner(message, kind = "info") {
  const banner = document.getElementById("action-banner");
  if (!banner) return;
  banner.textContent = message;
  banner.className = `action-banner action-${kind}`;
}

async function refreshStatus() {
  const endpoint = window.DASHBOARD_API_STATUS;
  if (!endpoint) return;

  try {
    const resp = await fetch(endpoint, { headers: { Accept: "application/json" } });
    const data = await resp.json();

    const statusText = document.getElementById("status-text");
    const pending = document.getElementById("pending-updates");
    const webhookUrl = document.getElementById("webhook-url");
    const dbStatus = document.getElementById("db-status");
    const pre = document.getElementById("status-json");

    if (statusText) {
      statusText.textContent = String(data.status || "UNKNOWN").toUpperCase();
      statusText.className = `stat-value status-${data.status || "disabled"}`;
    }
    if (pending) {
      pending.textContent = String(data.webhook_info?.pending_update_count ?? 0);
    }
    if (webhookUrl) {
      webhookUrl.textContent = data.webhook_info?.url || data.webhook_url || "not set";
    }
    if (dbStatus) {
      dbStatus.textContent = data.web_db_ready ? "READY" : (data.web_db_error || "NOT READY");
      dbStatus.className = `stat-value small ${data.web_db_ready ? "status-ok" : "status-error"}`;
    }
    if (pre) {
      pre.textContent = JSON.stringify(data, null, 2);
    }
    setActionBanner("Service status refreshed.", "success");
    setRefreshText("status-updated-text", "Status updated:");
    setRefreshText("last-refresh-text", "Last refresh:");
  } catch (err) {
    const pre = document.getElementById("status-json");
    if (pre) {
      pre.textContent = `Failed to load status\n${String(err)}`;
    }
    setActionBanner(`Status refresh failed: ${String(err)}`, "error");
    setRefreshText("status-updated-text", "Refresh failed at");
  }
}

function setMissionRowActive(code) {
  document.querySelectorAll(".mission-row").forEach((row) => {
    row.classList.toggle("is-active", row.dataset.code === code);
  });
  const selected = document.getElementById("selected-mission-code");
  if (selected) selected.textContent = code || "-";
}

function renderMissionDetail(detail) {
  if (!detail) return;
  const title = document.getElementById("detail-title");
  const code = document.getElementById("detail-code");
  const client = document.getElementById("detail-client");
  const tier = document.getElementById("detail-tier");
  const status = document.getElementById("detail-status");
  const priority = document.getElementById("detail-priority");
  const lang = document.getElementById("detail-lang");
  const translator = document.getElementById("detail-translator");
  const reward = document.getElementById("detail-reward");
  const xp = document.getElementById("detail-xp");
  const pickLink = document.getElementById("detail-pick-link");
  const cmdPick = document.getElementById("cmd-pick");
  const modifiers = document.getElementById("detail-modifiers");
  const roles = document.getElementById("detail-roles");
  const jsonLink = document.getElementById("cmd-json-detail");

  if (title) title.textContent = detail.title || "-";
  if (code) code.textContent = detail.code || "-";
  if (client) client.textContent = detail.client_name || "-";
  if (tier) tier.textContent = detail.client_tier || "-";
  if (status) status.textContent = detail.status || "-";
  if (priority) priority.textContent = detail.priority || "-";
  if (lang) lang.textContent = detail.lang || "-";
  if (translator) translator.textContent = detail.translator || "-";
  if (reward) reward.textContent = detail.reward ?? "-";
  if (xp) xp.textContent = detail.xp ?? "-";

  const command = `/pick ${detail.code || "-"}`;
  if (pickLink) {
    pickLink.textContent = command;
    pickLink.dataset.command = command;
  }
  if (cmdPick) {
    cmdPick.dataset.command = command;
  }
  if (jsonLink && detail.code) {
    jsonLink.href = `/api/mission/${encodeURIComponent(detail.code)}`;
  }

  if (modifiers) {
    modifiers.innerHTML = "";
    const items = detail.modifiers || [];
    if (!items.length) {
      const empty = document.createElement("span");
      empty.className = "muted";
      empty.textContent = "No modifiers";
      modifiers.appendChild(empty);
    } else {
      items.forEach((item) => {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = item;
        modifiers.appendChild(chip);
      });
    }
  }

  if (roles) {
    roles.innerHTML = "";
    (detail.roles || []).forEach((item) => {
      const wrapper = document.createElement("div");
      wrapper.className = "role-item";
      wrapper.innerHTML = `<strong>${item.role}</strong><span>${item.gender} · ${item.lines} lines</span><em>${item.assigned}</em>`;
      roles.appendChild(wrapper);
    });
    if (!(detail.roles || []).length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No role data";
      roles.appendChild(empty);
    }
  }

  setMissionRowActive(detail.code);
}

async function loadMissionDetail(url) {
  if (!url) return;
  try {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await resp.json();
    if (data.ok && data.detail) {
      renderMissionDetail(data.detail);
      setActionBanner(`Loaded mission ${data.detail.code}.`, "success");
    }
  } catch (err) {
    console.error("Failed to load mission detail", err);
    setActionBanner(`Failed to load mission detail: ${String(err)}`, "error");
  }
}

function bindMissionRows() {
  document.querySelectorAll(".mission-row").forEach((row) => {
    row.addEventListener("click", () => {
      const detailUrl = row.dataset.detailUrl;
      if (detailUrl) {
        loadMissionDetail(detailUrl);
        const url = new URL(window.location.href);
        url.searchParams.set("selected", row.dataset.code || "");
        window.history.replaceState({}, "", url);
      }
    });
  });
}

function updateVisibleMissionCount() {
  const rows = Array.from(document.querySelectorAll(".mission-row"));
  const visible = rows.filter((row) => !row.classList.contains("is-hidden-search"));
  const counter = document.getElementById("mission-visible-count");
  const total = document.getElementById("mission-total-count");
  const summary = document.getElementById("board-summary-text");
  const emptyState = document.getElementById("client-empty-state");
  const feedback = document.getElementById("client-filter-feedback");

  if (counter) counter.textContent = String(visible.length);
  if (total) total.textContent = String(rows.length);
  if (summary) summary.textContent = `Showing ${visible.length} of ${rows.length} rows on this page.`;
  if (emptyState) emptyState.classList.toggle("hidden", visible.length > 0);
  if (feedback) {
    feedback.textContent = visible.length > 0
      ? "Quick search is filtering only the current page results."
      : "No matches on the current page. Clear the quick search or change the filters.";
  }
}

function applyMissionSearch() {
  const input = document.getElementById("mission-search-input");
  const term = String(input?.value || "").trim().toLowerCase();
  document.querySelectorAll(".mission-row").forEach((row) => {
    const hay = row.dataset.search || "";
    const show = !term || hay.includes(term);
    row.classList.toggle("is-hidden-search", !show);
  });
  updateVisibleMissionCount();
}

function bindMissionSearch() {
  const input = document.getElementById("mission-search-input");
  if (!input) return;
  input.addEventListener("input", applyMissionSearch);
  applyMissionSearch();
}

function bindQuickFilters() {
  document.querySelectorAll(".quick-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const type = btn.dataset.filterType;
      const value = btn.dataset.filterValue || "";
      if (type === "status") {
        const select = document.getElementById("status-filter-select");
        if (select) select.value = value;
      }
      if (type === "priority") {
        const select = document.getElementById("priority-filter-select");
        if (select) select.value = value;
      }
      const form = btn.closest("#mission-board")?.querySelector("form.filters");
      if (form) form.requestSubmit();
    });
  });
}

async function runAction(endpoint, label, button) {
  const target = document.getElementById("action-result");
  if (!endpoint) return;
  const original = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "Working…";
  }
  if (target) target.textContent = `Running ${label || endpoint}...`;
  setActionBanner(`Running ${label || endpoint}...`, "working");

  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json"
      },
      body: JSON.stringify({})
    });
    const data = await resp.json();
    if (target) target.textContent = JSON.stringify(data, null, 2);
    setActionBanner(data.ok === false ? `Action failed: ${data.message || label}` : `Action completed: ${label}`, data.ok === false ? "error" : "success");
    await refreshStatus();
  } catch (err) {
    if (target) target.textContent = `Action failed\n${String(err)}`;
    setActionBanner(`Action failed: ${String(err)}`, "error");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

function bindActionButtons() {
  document.querySelectorAll(".js-action-btn").forEach((btn) => {
    btn.addEventListener("click", () => runAction(btn.dataset.endpoint, btn.dataset.label, btn));
  });
}

async function copyCommand(text) {
  const feedback = document.getElementById("copy-feedback");
  try {
    await navigator.clipboard.writeText(text);
    if (feedback) feedback.textContent = `Copied to clipboard: ${text}`;
    setActionBanner(`Copied command: ${text}`, "success");
  } catch (err) {
    if (feedback) feedback.textContent = `Copy failed: ${String(err)}`;
    setActionBanner(`Copy failed: ${String(err)}`, "error");
  }
}

function bindCopyButtons() {
  document.querySelectorAll(".copy-command-btn").forEach((btn) => {
    btn.addEventListener("click", () => copyCommand(btn.dataset.command || btn.textContent || ""));
  });
}

document.addEventListener("DOMContentLoaded", () => {
  refreshStatus();
  bindMissionRows();
  bindMissionSearch();
  bindQuickFilters();
  bindActionButtons();
  bindCopyButtons();

  const refreshBtn = document.getElementById("refresh-status-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshStatus);

  const refreshBoardBtn = document.getElementById("refresh-board-btn");
  if (refreshBoardBtn) {
    refreshBoardBtn.addEventListener("click", () => window.location.reload());
  }

  window.setInterval(refreshStatus, 20000);
});
