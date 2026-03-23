async function refreshStatus() {
  const endpoint = window.DASHBOARD_API_STATUS;
  if (!endpoint) return;

  try {
    const resp = await fetch(endpoint, { headers: { "Accept": "application/json" } });
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
  } catch (err) {
    const pre = document.getElementById("status-json");
    if (pre) {
      pre.textContent = `Failed to load status\n${String(err)}`;
    }
  }
}

function setMissionRowActive(code) {
  document.querySelectorAll(".mission-row").forEach((row) => {
    row.classList.toggle("is-active", row.dataset.code === code);
  });
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
  const modifiers = document.getElementById("detail-modifiers");
  const roles = document.getElementById("detail-roles");

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
  if (pickLink) {
    pickLink.textContent = `/pick ${detail.code || "-"}`;
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
    const resp = await fetch(url, { headers: { "Accept": "application/json" } });
    const data = await resp.json();
    if (data.ok && data.detail) {
      renderMissionDetail(data.detail);
    }
  } catch (err) {
    console.error("Failed to load mission detail", err);
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

document.addEventListener("DOMContentLoaded", () => {
  refreshStatus();
  bindMissionRows();

  const refreshBtn = document.getElementById("refresh-status-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshStatus);

  const refreshBoardBtn = document.getElementById("refresh-board-btn");
  if (refreshBoardBtn) {
    refreshBoardBtn.addEventListener("click", () => window.location.reload());
  }

  window.setInterval(refreshStatus, 20000);
});
