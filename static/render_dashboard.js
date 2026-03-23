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

function buildMissionWorkflow(detail) {
  const code = detail?.code || "<movie_code>";
  const translator = detail?.translator && detail.translator !== "-"
    ? `/assigntr ${detail.translator}`
    : "/assigntr <translator_name>";
  return [
    `/pick ${code}`,
    "/accept",
    "/assignui",
    "/team",
    "/submit",
    translator,
  ].join("\n");
}

function buildRoleTemplate(role) {
  const roleName = role?.role || "ROLE";
  const gender = String(role?.gender || "staff").toLowerCase() || "staff";
  const assigned = String(role?.assigned || "-").trim();
  return {
    label: roleName,
    template: `/assign ${roleName} <${gender}_staff_name>`,
    assignedTemplate: assigned && assigned !== "-" ? `/assign ${roleName} ${assigned}` : "",
    meta: `${gender.toUpperCase()} · ${role?.lines ?? "-"} lines`,
  };
}

function renderRoleTemplates(detail, targetId) {
  const target = document.getElementById(targetId);
  if (!target) return;
  target.innerHTML = "";
  const roles = Array.isArray(detail?.roles) ? detail.roles : [];
  if (!roles.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "Role templates appear when role data is available.";
    target.appendChild(empty);
    return;
  }

  roles.forEach((role) => {
    const tpl = buildRoleTemplate(role);
    const wrap = document.createElement("div");
    wrap.className = "template-item";
    const current = role?.assigned && role.assigned !== "-" ? ` · current ${role.assigned}` : "";
    wrap.innerHTML = `
      <div>
        <strong>${tpl.label}</strong>
        <div class="muted tiny">${tpl.meta}${current}</div>
      </div>
      <div class="row gap-sm wrap-row">
        <button class="btn copy-command-btn" type="button" data-command="${tpl.template}">Template</button>
        ${tpl.assignedTemplate ? `<button class="btn copy-command-btn" type="button" data-command="${tpl.assignedTemplate}">Assigned</button>` : ""}
      </div>
    `;
    target.appendChild(wrap);
  });
  bindCopyButtons();
  bindMissionModal();
}

function renderRoleList(detail, targetId) {
  const target = document.getElementById(targetId);
  if (!target) return;
  target.innerHTML = "";
  const roles = Array.isArray(detail?.roles) ? detail.roles : [];
  if (!roles.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No role data";
    target.appendChild(empty);
    return;
  }

  roles.forEach((item) => {
    const wrapper = document.createElement("div");
    wrapper.className = "role-item";
    wrapper.innerHTML = `<strong>${item.role}</strong><span>${item.gender} · ${item.lines} lines</span><em>${item.assigned}</em>`;
    target.appendChild(wrapper);
  });
}

function renderCommandDeck(targetId, items) {
  const target = document.getElementById(targetId);
  if (!target) return;
  target.innerHTML = "";
  const deck = Array.isArray(items) ? items : [];
  if (!deck.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No simulator actions available.";
    target.appendChild(empty);
    return;
  }

  deck.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `btn copy-command-btn${item?.tone === "primary" || item?.active ? " btn-primary" : ""}`;
    button.dataset.command = item?.command || "";
    button.textContent = item?.label || item?.command || "Copy";
    target.appendChild(button);
  });
  bindCopyButtons();
}

function renderMissionModal(detail) {
  const title = document.getElementById("mission-modal-title");
  const code = document.getElementById("mission-modal-code");
  const workflow = document.getElementById("mission-modal-workflow");
  const copyFlow = document.getElementById("mission-modal-copy-flow");
  const highlights = document.getElementById("mission-modal-highlights");

  if (title) title.textContent = detail?.title || "Select a mission";
  if (code) code.textContent = detail?.code || "-";
  const workflowText = buildMissionWorkflow(detail);
  if (workflow) workflow.textContent = workflowText;
  if (copyFlow) copyFlow.dataset.command = workflowText;

  if (highlights) {
    highlights.innerHTML = "";
    const items = [
      ["Client", detail?.client_name || "-"],
      ["Tier", detail?.client_tier || "-"],
      ["Priority", detail?.priority || "-"],
      ["Translator", detail?.translator || "-"],
    ];
    items.forEach(([label, value]) => {
      const card = document.createElement("div");
      card.className = "highlight-card";
      card.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
      highlights.appendChild(card);
    });
  }

  renderRoleTemplates(detail, "mission-modal-templates");
  renderRoleList(detail, "mission-modal-roles");
  bindCopyButtons();
}


function renderMissionSimulation(simulation) {
  if (!simulation) return;
  const preset = document.getElementById("sim-preset");
  const urgency = document.getElementById("sim-urgency");
  const polish = document.getElementById("sim-polish");
  const staffing = document.getElementById("sim-staffing");
  const trFocus = document.getElementById("sim-translator-focus");
  const voFocus = document.getElementById("sim-vo-focus");
  const operatorSummary = document.getElementById("sim-operator-summary");
  const warnList = document.getElementById("sim-warning-list");
  const workflow = document.getElementById("sim-workflow-script");
  const copyBtn = document.getElementById("cmd-copy-sim-flow");

  if (preset) preset.textContent = simulation.preset || "-";
  if (urgency) urgency.textContent = String(simulation.urgency_score ?? "-");
  if (polish) polish.textContent = String(simulation.polish_score ?? "-");
  if (staffing) staffing.textContent = String(simulation.staffing_score ?? "-");
  if (trFocus) trFocus.textContent = simulation.translator_focus || "-";
  if (voFocus) voFocus.textContent = simulation.vo_focus || "-";
  if (operatorSummary) operatorSummary.textContent = simulation.operator_summary || "-";
  if (workflow) workflow.textContent = simulation.workflow_text || "";
  if (copyBtn) copyBtn.dataset.command = simulation.workflow_text || "";
  renderCommandDeck("sim-action-deck", simulation.action_deck);
  renderCommandDeck("sim-preset-deck", simulation.preset_deck);
  if (warnList) {
    warnList.innerHTML = "";
    const warnings = Array.isArray(simulation.warnings) ? simulation.warnings : [];
    if (!warnings.length) {
      const li = document.createElement("li");
      li.textContent = "No warnings.";
      warnList.appendChild(li);
    } else {
      warnings.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = item;
        warnList.appendChild(li);
      });
    }
  }
  bindCopyButtons();
}

async function loadMissionSimulation(code) {
  const base = window.DASHBOARD_API_SIMULATE_BASE;
  if (!base || !code) return;
  const url = base.replace("__CODE__", encodeURIComponent(code));
  try {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await resp.json();
    if (data.ok && data.simulation) {
      renderMissionSimulation(data.simulation);
    }
  } catch (err) {
    console.error("Failed to load mission simulation", err);
  }
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
  const jsonLink = document.getElementById("cmd-json-detail");
  const cmdAccept = document.getElementById("cmd-accept");
  const cmdAssign = document.getElementById("cmd-assignui");
  const cmdTeam = document.getElementById("cmd-team");
  const cmdSubmit = document.getElementById("cmd-submit");
  const cmdCopyWorkflow = document.getElementById("cmd-copy-workflow");
  const workflowScript = document.getElementById("workflow-script");
  const translatorTemplateBtn = document.getElementById("cmd-translator-template");

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
  if (cmdPick) cmdPick.dataset.command = command;
  if (cmdAccept) cmdAccept.dataset.command = "/accept";
  if (cmdAssign) cmdAssign.dataset.command = "/assignui";
  if (cmdTeam) cmdTeam.dataset.command = "/team";
  if (cmdSubmit) cmdSubmit.dataset.command = "/submit";
  if (translatorTemplateBtn) {
    translatorTemplateBtn.dataset.command = detail.translator && detail.translator !== "-"
      ? `/assigntr ${detail.translator}`
      : "/assigntr <translator_name>";
  }
  const workflowText = buildMissionWorkflow(detail);
  if (cmdCopyWorkflow) cmdCopyWorkflow.dataset.command = workflowText;
  if (workflowScript) workflowScript.textContent = workflowText;
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

  renderRoleList(detail, "detail-roles");
  renderRoleTemplates(detail, "detail-role-templates");
  renderMissionModal(detail);
  setMissionRowActive(detail.code);
  bindCopyButtons();
  loadMissionSimulation(detail.code);
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


function openMissionModal() {
  const node = document.getElementById("mission-modal-backdrop");
  if (node) node.classList.remove("hidden");
}

function closeMissionModal() {
  const node = document.getElementById("mission-modal-backdrop");
  if (node) node.classList.add("hidden");
}

function bindMissionModal() {
  document.querySelectorAll(".js-open-mission-modal").forEach((btn) => {
    btn.addEventListener("click", openMissionModal);
  });
  document.querySelectorAll(".js-close-mission-modal").forEach((btn) => {
    btn.addEventListener("click", closeMissionModal);
  });
  const backdrop = document.getElementById("mission-modal-backdrop");
  if (backdrop) {
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) closeMissionModal();
    });
  }
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

  const existingCode = (document.getElementById("detail-code")?.textContent || "").trim();
  if (existingCode && existingCode !== "-") {
    loadMissionSimulation(existingCode);
  }

  window.setInterval(refreshStatus, 20000);
});
