async function refreshStatus() {
  const endpoint = window.DASHBOARD_API_STATUS;
  if (!endpoint) return;

  try {
    const resp = await fetch(endpoint, { headers: { "Accept": "application/json" } });
    const data = await resp.json();

    const statusText = document.getElementById("status-text");
    const pending = document.getElementById("pending-updates");
    const webhookUrl = document.getElementById("webhook-url");
    const startError = document.getElementById("start-error");
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
    if (startError) {
      startError.textContent = data.start_error || "No startup error";
      startError.className = `stat-value small ${data.start_error ? "status-error" : "status-ok"}`;
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

document.addEventListener("DOMContentLoaded", () => {
  refreshStatus();
  const btn = document.getElementById("refresh-status-btn");
  if (btn) {
    btn.addEventListener("click", refreshStatus);
  }
  window.setInterval(refreshStatus, 20000);
});
