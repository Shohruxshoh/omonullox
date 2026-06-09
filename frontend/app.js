const TOKEN_KEY = "post_api_frontend_auth_token";
const pageMeta = {
  dashboard: ["Umumiy ko'rinish", "Dashboard"],
  tasks: ["Yuborish va kuzatish", "Tasklar"],
  sponsored: ["Telegram qidiruvi", "Reklama qidiruvi"],
  keys: ["Admin boshqaruvi", "API kalitlar"],
  users: ["Admin boshqaruvi", "Foydalanuvchilar"],
  sessions: ["Import va monitoring", "Sessionlar"],
  system: ["Holat va sozlamalar", "Tizim"],
};

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  page: "dashboard",
  taskOffset: 0,
  taskLimit: 20,
  lastTaskCount: 0,
  sponsoredResults: [],
  refreshTimer: null,
  confirmResolve: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const el = {
  sidebar: $("#sidebar"),
  topbar: $("#topbar"),
  pages: $("#pages"),
  authPage: $("#auth-page"),
  pageEyebrow: $("#page-eyebrow"),
  pageTitle: $("#page-title"),
  currentUser: $("#current-user"),
  currentRole: $("#current-role"),
  toast: $("#toast"),
  confirmDialog: $("#confirm-dialog"),
  confirmMessage: $("#confirm-message"),
  confirmOk: $("#confirm-ok"),
  confirmCancel: $("#confirm-cancel"),
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  bindNavigation();
  bindAuth();
  bindTasks();
  bindSponsored();
  bindKeys();
  bindUsers();
  bindSessions();
  bindSystem();
  bindCommon();
  syncReactionFields();

  if (!state.token) {
    setLoggedOut();
    return;
  }

  try {
    const user = await api("/auth/me");
    setLoggedIn(user);
    await loadPage(state.page);
  } catch {
    setLoggedOut();
  }
}

function bindNavigation() {
  $$("[data-page-target], [data-page-link]").forEach((button) => {
    button.addEventListener("click", () => showPage(button.dataset.pageTarget || button.dataset.pageLink));
  });

  $("#menu-btn").addEventListener("click", () => el.sidebar.classList.toggle("open"));
  $("#global-refresh-btn").addEventListener("click", () => loadPage(state.page, true));
}

function bindAuth() {
  $$("[data-auth-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      $$("[data-auth-tab]").forEach((item) => item.classList.toggle("active", item === button));
      $("#login-form").hidden = button.dataset.authTab !== "login";
      $("#register-form").hidden = button.dataset.authTab !== "register";
    });
  });

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withButton($("#login-submit"), "Kirilmoqda...", async () => {
      const data = await api("/auth/login", {
        method: "POST",
        auth: false,
        json: {
          username: $("#login-username").value.trim(),
          password: $("#login-password").value,
        },
      });
      state.token = data.token;
      localStorage.setItem(TOKEN_KEY, state.token);
      const user = await api("/auth/me");
      setLoggedIn(user);
      $("#login-password").value = "";
      showToast("Tizimga kirildi.");
      await loadPage("dashboard");
    });
  });

  $("#register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withButton($("#register-submit"), "Yaratilmoqda...", async () => {
      await api("/auth/register", {
        method: "POST",
        auth: false,
        json: {
          username: $("#register-username").value.trim(),
          password: $("#register-password").value,
          role: "user",
        },
      });
      $("#login-username").value = $("#register-username").value.trim();
      $("#register-form").reset();
      $('[data-auth-tab="login"]').click();
      showToast("Hisob yaratildi. Endi tizimga kiring.");
    });
  });

  $("#logout-btn").addEventListener("click", async () => {
    const token = state.token;
    setLoggedOut();
    if (token) {
      try {
        await fetch("/auth/logout", { method: "POST", headers: { "X-Token": token } });
      } catch {
        // Local logout is sufficient when the server is temporarily unavailable.
      }
    }
  });
}

function bindTasks() {
  $("#quick-service").addEventListener("change", syncReactionFields);
  $("#task-service").addEventListener("change", syncReactionFields);
  $("#quick-task-form").addEventListener("submit", (event) => submitTask(event, "quick"));
  $("#task-form").addEventListener("submit", (event) => submitTask(event, "task"));
  $("#tasks-refresh").addEventListener("click", () => loadTasks(true));
  $("#task-scope").addEventListener("change", () => {
    state.taskOffset = 0;
    loadTasks(true);
  });
  $("#tasks-prev").addEventListener("click", () => {
    state.taskOffset = Math.max(0, state.taskOffset - state.taskLimit);
    loadTasks(true);
  });
  $("#tasks-next").addEventListener("click", () => {
    if (state.lastTaskCount < state.taskLimit) return;
    state.taskOffset += state.taskLimit;
    loadTasks(true);
  });
  $("#status-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withButton($("#status-submit"), "Olinmoqda...", async () => {
      const taskId = $("#status-task-id").value.trim();
      const data = await api(`/status/${encodeURIComponent(taskId)}`);
      showJsonResult(data, $("#status-result"));
    });
  });
}

function bindKeys() {
  $("#key-create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const userId = Number($("#key-user-id").value);
    const payload = { priority: Number($("#key-priority").value) };
    if (Number.isInteger(userId) && userId > 0) payload.user_id = userId;

    await withButton($("#key-create-submit"), "Yaratilmoqda...", async () => {
      const data = await api("/admin/keys", { method: "POST", json: payload });
      showJsonResult(data, null);
      showToast("API kalit tayyor.");
      await loadKeys();
    });
  });
  $("#keys-refresh").addEventListener("click", () => loadKeys(true));
}

function bindSponsored() {
  $("#sponsored-download").addEventListener("click", downloadSponsoredCsv);
  $("#sponsored-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withButton($("#sponsored-search-submit"), "Qidirilmoqda...", async () => {
      const data = await api("/sponsored/search", {
        method: "POST",
        apiKey: state.user.api_key,
        json: {
          search_key: $("#sponsored-search-key").value.trim(),
          channel_username: $("#sponsored-keyword").value.trim(),
          accounts: Number($("#sponsored-accounts").value),
          parallel_sessions: Number($("#sponsored-parallel").value),
        },
      });
      renderSponsoredResults(data);
      const taskSuffix = data.task_id ? ` Task: ${shortId(data.task_id)}` : "";
      showToast(`${data.message || "Reklama qidiruvi tugadi."}${taskSuffix}`);
    });
  });
}

function bindUsers() {
  $("#user-create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withButton($("#user-create-submit"), "Yaratilmoqda...", async () => {
      await api("/admin/users", {
        method: "POST",
        json: {
          username: $("#user-username").value.trim(),
          password: $("#user-password").value,
          role: $("#user-role").value,
        },
      });
      $("#user-create-form").reset();
      showToast("Foydalanuvchi yaratildi.");
      await loadUsers();
    });
  });
  $("#users-refresh").addEventListener("click", () => loadUsers(true));
}

function bindSessions() {
  $("#sessions-refresh").addEventListener("click", () => loadSessions(true));

  $("#session-raw-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withButton($("#session-raw-submit"), "Yuklanmoqda...", async () => {
      const data = await api("/admin/sessions/import-raw", {
        method: "POST",
        json: {
          sessions: $("#session-raw-text").value.trim(),
          api_id: Number($("#session-api-id").value),
          api_hash: $("#session-api-hash").value.trim(),
          update: $("#session-raw-update").checked,
          default_status: "active",
        },
      });
      showSessionResult(data);
      await loadSessionStats();
    });
  });

  $("#session-json-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = Array.from($("#session-json-files").files || []);
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    const update = $("#session-json-update").checked;
    await sessionUpload($("#session-json-submit"), `/admin/sessions/import-json?update=${update}&default_status=active`, form);
  });

  $("#session-csv-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData();
    form.append("file", $("#session-csv-file").files[0]);
    const update = $("#session-csv-update").checked;
    await sessionUpload($("#session-csv-submit"), `/admin/sessions/import-csv?update=${update}`, form);
  });

  $("#session-folder-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const clearFirst = $("#session-folder-clear").checked;
    if (clearFirst && !(await confirmAction("DBdagi barcha eski sessionlar o'chiriladi. Davom etasizmi?"))) return;
    const params = new URLSearchParams({
      sessions_dir: $("#session-folder").value.trim(),
      clear_first: String(clearFirst),
    });
    await sessionUpload($("#session-folder-submit"), `/admin/sessions/upload-folder?${params}`);
  });

  $("#session-db-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withButton($("#session-db-submit"), "Tekshirilmoqda...", async () => {
      const data = await api(`/admin/sessions/upload-db?only_active=${$("#session-db-active").checked}`, { method: "POST" });
      showSessionResult(data);
    });
  });

  $("#session-block-check-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showSessionBlockCheckStatus("Tekshiruv boshlandi. Bo'sh sessionlar Telegram orqali tekshirilmoqda...", "running");
    await withButton($("#session-block-check-submit"), "Tekshirilmoqda...", async () => {
      try {
        const data = await api("/admin/sessions/check-recent", {
          method: "POST",
          json: { limit: Number($("#session-block-check-limit").value) },
        });
        renderSessionBlockCheckResult(data);
        showSessionResult(data);
        await loadSessionStats();
      } catch (error) {
        showSessionBlockCheckStatus(`Tekshiruv bajarilmadi: ${error.message}`, "error");
        throw error;
      }
    });
  });

  $("#clear-redis-btn").addEventListener("click", async () => {
    if (!(await confirmAction("Redisdagi barcha sessionlar o'chiriladi. Bu amalni qaytarib bo'lmaydi."))) return;
    await withButton($("#clear-redis-btn"), "Tozalanmoqda...", async () => {
      const data = await api("/admin/sessions/clear-redis", { method: "DELETE" });
      showSessionResult(data);
      showToast("Redis tozalandi.");
    });
  });
}

function bindSystem() {
  $("#system-refresh").addEventListener("click", () => loadSystem(true));
  $("#system-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withButton($("#system-settings-submit"), "Saqlanmoqda...", async () => {
      await api("/admin/system/settings", {
        method: "PATCH",
        json: { parallel_accounts: Number($("#system-parallel-input").value) },
      });
      showToast("Sozlama saqlandi.");
      await loadSystem();
    });
  });
  $("#system-resume-btn").addEventListener("click", async () => {
    await withButton($("#system-resume-btn"), "Davom ettirilmoqda...", async () => {
      await api("/admin/sessions/resume", { method: "POST" });
      showToast("Tizim davom ettirildi.");
      await loadSystem();
    });
  });
}

function bindCommon() {
  $("#copy-my-key").addEventListener("click", () => copyText($("#my-api-key").value));
  el.confirmCancel.addEventListener("click", () => closeConfirm(false));
  el.confirmOk.addEventListener("click", () => closeConfirm(true));
  el.confirmDialog.addEventListener("click", (event) => {
    if (event.target === el.confirmDialog) closeConfirm(false);
  });
}

function setLoggedIn(user) {
  state.user = user;
  el.authPage.hidden = true;
  el.sidebar.hidden = false;
  el.topbar.hidden = false;
  el.pages.hidden = false;
  el.currentUser.textContent = user.username || "user";
  el.currentRole.textContent = user.role || "user";
  $("#my-api-key").value = user.api_key || "";
  $("#profile-username").textContent = user.username || "-";
  $("#profile-role").textContent = user.role || "-";
  $("#profile-id").textContent = user.id ?? "-";
  $$(".admin-only").forEach((item) => {
    item.hidden = user.role !== "admin";
  });
  startRefreshTimer();
}

function setLoggedOut() {
  state.token = "";
  state.user = null;
  localStorage.removeItem(TOKEN_KEY);
  el.authPage.hidden = false;
  el.sidebar.hidden = true;
  el.topbar.hidden = true;
  el.pages.hidden = true;
  state.page = "dashboard";
  $$(".page").forEach((item) => item.classList.toggle("active", item.dataset.page === "dashboard"));
  $$("[data-page-target]").forEach((item) => item.classList.toggle("active", item.dataset.pageTarget === "dashboard"));
  [el.pageEyebrow.textContent, el.pageTitle.textContent] = pageMeta.dashboard;
  stopRefreshTimer();
}

function isAdmin() {
  return state.user?.role === "admin";
}

async function showPage(page) {
  if (!pageMeta[page]) page = "dashboard";
  if (!isAdmin() && !["dashboard", "tasks", "sponsored"].includes(page)) page = "dashboard";
  state.page = page;
  history.replaceState(null, "", `#${page}`);
  $$(".page").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
  $$("[data-page-target]").forEach((item) => item.classList.toggle("active", item.dataset.pageTarget === page));
  [el.pageEyebrow.textContent, el.pageTitle.textContent] = pageMeta[page];
  el.sidebar.classList.remove("open");
  await loadPage(page);
}

async function loadPage(page, notify = false) {
  if (!state.token) return;
  try {
    if (page === "dashboard") await loadDashboard();
    if (page === "tasks") await loadTasks();
    if (page === "keys") await loadKeys();
    if (page === "users") await loadUsers();
    if (page === "sessions") await loadSessions();
    if (page === "system") await loadSystem();
    if (notify) showToast("Ma'lumotlar yangilandi.");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadDashboard() {
  const requests = [
    api("/queue"),
    api("/locks"),
    api("/tasks?limit=10&offset=0"),
  ];
  if (isAdmin()) requests.push(api("/admin/sessions/stats"));
  const [queue, locks, tasks, sessions] = await Promise.all(requests);

  $("#dashboard-queue").textContent = queue.waiting ?? 0;
  $("#dashboard-busy").textContent = locks.busy ?? 0;
  $("#dashboard-locks-meta").textContent = `${locks.total_accounts ?? 0} ta registrda`;
  $("#dashboard-task-total").textContent = tasks.length;
  $("#dashboard-task-meta").textContent = `${tasks.filter((task) => task.status === "done").length} ta bajarilgan`;
  if (sessions) {
    $("#dashboard-active-sessions").textContent = sessions.active ?? 0;
    $("#dashboard-session-meta").textContent = `${sessions.total ?? 0} ta jami`;
  }
  renderTaskCards($("#dashboard-task-list"), tasks.slice(0, 6));
}

async function submitTask(event, prefix) {
  event.preventDefault();
  const service = $(`#${prefix}-service`).value;
  const accounts = Number($(`#${prefix}-accounts`).value);
  const payload = { post_link: $(`#${prefix}-post-link`).value.trim() };
  if (Number.isInteger(accounts) && accounts > 0) payload.accounts = accounts;
  if (service === "reactions") payload.reaction = $(`#${prefix}-reaction`).value.trim() || "👍";

  const button = $(`#${prefix}-task-submit`);
  await withButton(button, "Yuborilmoqda...", async () => {
    const data = await api(`/task/${service}`, {
      method: "POST",
      apiKey: state.user.api_key,
      json: payload,
    });
    if (prefix === "quick") showJsonResult(data, $("#quick-task-result"));
    showToast("Task navbatga qo'shildi.");
    await loadDashboard();
    if (state.page === "tasks") await loadTasks();
  });
}

function syncReactionFields() {
  $("#quick-reaction-field").hidden = $("#quick-service").value !== "reactions";
  $("#task-reaction-field").hidden = $("#task-service").value !== "reactions";
}

async function loadTasks(notify = false) {
  let scope = $("#task-scope").value;
  if (!isAdmin()) {
    scope = "mine";
    $("#task-scope").value = "mine";
  }
  const base = scope === "all" ? "/admin/tasks" : "/tasks";
  const tasks = await api(`${base}?limit=${state.taskLimit}&offset=${state.taskOffset}`);
  state.lastTaskCount = tasks.length;
  renderTaskTable(tasks, scope === "all");
  const page = Math.floor(state.taskOffset / state.taskLimit) + 1;
  $("#tasks-page-label").textContent = `${page}-sahifa`;
  $("#tasks-prev").disabled = state.taskOffset === 0;
  $("#tasks-next").disabled = tasks.length < state.taskLimit;
  $("#task-table-title").textContent = scope === "all" ? "Barcha tasklar" : "Mening tasklarim";
  if (notify) showToast("Tasklar yangilandi.");
}

function renderTaskCards(container, tasks) {
  container.innerHTML = "";
  if (!tasks.length) return renderEmpty(container, "Hali task yo'q.");
  tasks.forEach((task) => {
    const card = document.createElement("article");
    card.className = "task-card";
    card.innerHTML = `
      <div class="task-card-head">
        <strong>${escapeHtml(serviceLabel(task.service))}</strong>
        ${statusPill(task.status)}
      </div>
      <a href="${escapeHtml(task.post_link || "#")}" target="_blank" rel="noreferrer">${escapeHtml(task.post_link || "-")}</a>
      <div class="task-meta">
        <span>ID: ${escapeHtml(shortId(task.task_id))}</span>
        <span>${Number(task.done || 0)}/${Number(task.total || 0)}</span>
        <span>${Number(task.percent || 0).toFixed(1)}%</span>
        <span>${formatDate(task.created_at)}</span>
      </div>
      <div class="progress"><span style="width:${clampPercent(task.percent)}%"></span></div>
    `;
    container.appendChild(card);
  });
}

function renderTaskTable(tasks, showKey) {
  const headers = ["Task", "Service", "Status", "Progress", "Priority", "Yaratilgan"];
  if (showKey) headers.splice(1, 0, "API key");
  const rows = tasks.map((task) => {
    const cells = [
      `<td><div class="mono">${escapeHtml(shortId(task.task_id))}</div><a href="${escapeHtml(task.post_link || "#")}" target="_blank" rel="noreferrer">${escapeHtml(shortLink(task.post_link))}</a></td>`,
      `<td>${escapeHtml(serviceLabel(task.service))}</td>`,
      `<td>${statusPill(task.status)}${task.error ? `<div class="danger-text">${escapeHtml(task.error)}</div>` : ""}</td>`,
      `<td>${Number(task.done || 0)}/${Number(task.total || 0)} (${Number(task.percent || 0).toFixed(1)}%)</td>`,
      `<td>${Number(task.priority || 0)}</td>`,
      `<td>${formatDate(task.created_at)}</td>`,
    ];
    if (showKey) cells.splice(1, 0, `<td class="mono">${escapeHtml(shortId(task.api_key))}</td>`);
    return cells.join("");
  });
  renderTable($("#tasks-table"), headers, rows);
}

function renderSponsoredResults(data) {
  const results = Array.isArray(data.results) ? data.results : [];
  const errors = Array.isArray(data.errors) ? data.errors : [];
  const sessionResults = Array.isArray(data.session_results) ? data.session_results : [];
  state.sponsoredResults = results;
  $("#sponsored-download").hidden = !results.length;
  $("#sponsored-metrics").hidden = false;
  $("#sponsored-found").textContent = Number(data.found || 0);
  $("#sponsored-sessions-found").textContent = Number(data.sessions_found || 0);
  $("#sponsored-sessions-not-found").textContent = Number(data.sessions_not_found || 0);
  $("#sponsored-sessions-failed").textContent = Number(data.sessions_failed || 0);
  $("#sponsored-parallel-used").textContent = Number(data.parallel_sessions || 0);
  $("#sponsored-checks").textContent = Number(data.checks_completed || 0);
  $("#sponsored-target-found").textContent = Number(data.target_found_sessions || 0);
  $("#sponsored-daily-skipped").textContent = Number(data.daily_skipped || 0);
  $("#sponsored-busy-waited").textContent = Number(data.busy_waited || 0);
  $("#sponsored-queue-wait").textContent = `${Number(data.queue_waited_seconds || 0).toFixed(2)}s`;

  const sessionRows = sessionResults.map((item) => `
    <td class="mono">${escapeHtml(item.session || "-")}</td>
    <td>${escapeHtml(formatTelegramAccount(item.account))}</td>
    <td>${statusPill(item.status)}</td>
    <td>${Number(item.found || 0)}</td>
    <td>${renderSponsoredAdList(item.found_ads)}</td>
    <td>${Number(item.checks || 0)}</td>
    <td>${Number(item.rounds_with_results || 0)}</td>
    <td>${Number(item.views_sent || 0)} / ${Number(item.views_failed || 0)}</td>
    <td>${Array.isArray(item.errors) ? item.errors.length : 0}</td>
  `);
  renderTable(
    $("#sponsored-session-results"),
    ["Session", "Telegram account", "Natija", "Topilgan", "Qaysi reklamalar", "Tekshiruv", "Natijali round", "View success / fail", "Xato"],
    sessionRows
  );

  const rows = results.map((item) => `
    <td>
      <strong>${escapeHtml(item.name || "-")}</strong>
      ${item.link ? `<div><a href="${escapeHtml(item.link)}" target="_blank" rel="noreferrer">${escapeHtml(item.link)}</a></div>` : ""}
    </td>
    <td>${escapeHtml(item.type || item.entity_type || "-")}</td>
    <td>${item.target_match ? statusPill("target") : statusPill("other")}</td>
    <td>${item.target_match ? "Yuborilmadi" : item.view_sent ? "Yuborildi" : "Xato"}</td>
    <td><strong>${Number(item.sessions_count || 0)}</strong> ta</td>
    <td>${renderSponsoredViewerList(item.sessions)}</td>
    <td>${Number(item.sightings || 0)}</td>
    <td>${escapeHtml((item.queries || [item.query_used]).filter(Boolean).join(", ") || "-")}</td>
    <td>${escapeHtml(item.sponsor_info || "-")}</td>
    <td>${escapeHtml(item.additional_info || "-")}</td>
  `);
  renderTable(
    $("#sponsored-results"),
    ["Reklama", "Turi", "Moslik", "View", "Topgan session", "Qaysi session/accountlar", "Jami ko'rinish", "Query", "Sponsor info", "Qo'shimcha"],
    rows
  );

  const errorBox = $("#sponsored-error-result");
  if (errors.length) {
    showJsonResult({ errors }, errorBox);
  } else {
    errorBox.hidden = true;
    errorBox.innerHTML = "";
  }
}

function downloadSponsoredCsv() {
  if (!state.sponsoredResults.length) return;
  const fields = [
    "keyword", "query_used", "account", "round", "seen_at", "name", "username",
    "link", "type", "source", "target_match", "view_sent", "view_error",
    "sponsor_info", "additional_info", "entity_type", "entity_id",
    "sessions_count", "sessions", "sightings", "queries", "rounds_seen",
  ];
  const csvCell = (value) => {
    const normalized = typeof value === "object" && value !== null ? JSON.stringify(value) : value;
    return `"${String(normalized ?? "").replaceAll('"', '""')}"`;
  };
  const lines = [
    fields.map(csvCell).join(","),
    ...state.sponsoredResults.map((row) => fields.map((field) => csvCell(row[field])).join(",")),
  ];
  const blob = new Blob([`\uFEFF${lines.join("\r\n")}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `sponsored-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function loadKeys(notify = false) {
  const keys = await api("/admin/keys");
  const rows = keys.map((key) => `
    <td><div class="key-cell"><code class="mono">${escapeHtml(key.api_key)}</code><button class="table-action" data-copy="${escapeHtml(key.api_key)}">Copy</button></div></td>
    <td>${escapeHtml(key.username || "-")} <span class="mono">#${key.user_id ?? "-"}</span></td>
    <td><input class="priority-input" data-key="${escapeHtml(key.api_key)}" type="number" min="1" max="10000" value="${Number(key.priority)}"></td>
    <td>${formatDate(key.created_at)}</td>
    <td><div class="table-actions"><button class="table-action" data-save-key="${escapeHtml(key.api_key)}">Saqlash</button><button class="table-action danger" data-delete-key="${escapeHtml(key.api_key)}">O'chirish</button></div></td>
  `);
  renderTable($("#keys-table"), ["API kalit", "User", "Prioritet", "Yaratilgan", "Amallar"], rows);
  $$("[data-copy]", $("#keys-table")).forEach((button) => button.addEventListener("click", () => copyText(button.dataset.copy)));
  $$("[data-save-key]", $("#keys-table")).forEach((button) => button.addEventListener("click", () => saveKeyPriority(button.dataset.saveKey)));
  $$("[data-delete-key]", $("#keys-table")).forEach((button) => button.addEventListener("click", () => deleteKey(button.dataset.deleteKey)));
  if (notify) showToast("API kalitlar yangilandi.");
}

async function saveKeyPriority(key) {
  const input = $(`.priority-input[data-key="${cssEscape(key)}"]`);
  await api(`/admin/keys/${encodeURIComponent(key)}`, {
    method: "PATCH",
    json: { priority: Number(input.value) },
  });
  showToast("Prioritet saqlandi.");
}

async function deleteKey(key) {
  if (!(await confirmAction(`${shortId(key)} kalitini o'chirasizmi?`))) return;
  await api(`/admin/keys/${encodeURIComponent(key)}`, { method: "DELETE" });
  showToast("API kalit o'chirildi.");
  await loadKeys();
}

async function loadUsers(notify = false) {
  const users = await api("/admin/users");
  const rows = users.map((user) => `
    <td class="mono">#${user.id}</td>
    <td><strong>${escapeHtml(user.username)}</strong></td>
    <td>${statusPill(user.role)}</td>
    <td>${formatDate(user.created_at)}</td>
    <td><button class="table-action danger" data-delete-user="${user.id}" ${user.id === state.user.id ? "disabled" : ""}>O'chirish</button></td>
  `);
  renderTable($("#users-table"), ["ID", "Username", "Rol", "Yaratilgan", "Amal"], rows);
  $$("[data-delete-user]", $("#users-table")).forEach((button) => button.addEventListener("click", () => deleteUser(Number(button.dataset.deleteUser))));
  if (notify) showToast("Foydalanuvchilar yangilandi.");
}

async function deleteUser(userId) {
  if (!(await confirmAction(`User #${userId} va unga tegishli API kalit o'chiriladi.`))) return;
  await api(`/admin/users/${userId}`, { method: "DELETE" });
  showToast("Foydalanuvchi o'chirildi.");
  await loadUsers();
}

async function loadSessions(notify = false) {
  await loadSessionStats();
  if (notify) showToast("Session statistikasi yangilandi.");
}

async function loadSessionStats() {
  const stats = await api("/admin/sessions/stats");
  $("#session-total").textContent = stats.total ?? 0;
  $("#session-active").textContent = stats.active ?? 0;
  $("#session-sleep").textContent = stats.sleep ?? 0;
  $("#session-flood").textContent = stats.flood ?? 0;
  $("#session-banned").textContent = stats.banned ?? 0;
}

async function sessionUpload(button, url, formData = null) {
  await withButton(button, "Yuklanmoqda...", async () => {
    const data = await api(url, { method: "POST", body: formData });
    showSessionResult(data);
    showToast("Session amali bajarildi.");
    await loadSessionStats();
  });
}

function showSessionResult(data) {
  showJsonResult(data, $("#session-result"));
}

function showSessionBlockCheckStatus(message, stateName = "") {
  const container = $("#session-block-check-status");
  container.hidden = false;
  container.className = `session-check-status ${stateName}`.trim();
  container.textContent = message;
}

function renderSessionBlockCheckResult(data) {
  const message = data.message || "Session tekshiruvi tugadi.";
  const summary = [
    `Tekshirildi: ${Number(data.checked || 0)}/${Number(data.requested || 0)}`,
    `Active: ${Number(data.active || 0)}`,
    `Bloklangan: ${Number(data.blocked || 0)}`,
    `Flood: ${Number(data.flooded || 0)}`,
    `Aniqlanmadi: ${Number(data.failed || 0)}`,
    `Bandligi sabab o'tkazildi: ${Number(data.busy_skipped || 0)}`,
  ].join(" | ");
  showSessionBlockCheckStatus(`${message}\n${summary}`, Number(data.failed || 0) ? "warning" : "success");
}

async function loadSystem(notify = false) {
  const [system, stats, catalog] = await Promise.all([
    api("/admin/system/status"),
    api("/admin/stats"),
    api("/api", { auth: false }),
  ]);
  $("#system-state").textContent = system.is_paused ? "Pauza" : "Ishlayapti";
  $("#system-state").classList.toggle("danger-text", Boolean(system.is_paused));
  $("#system-reason").textContent = system.paused_reason || "Sabab yo'q";
  $("#system-errors").textContent = system.recent_errors ?? 0;
  $("#system-error-limit").textContent = `limit: ${system.cb_threshold ?? "-"} / ${system.cb_window_sec ?? "-"} sec`;
  $("#system-queue").textContent = system.queue_size ?? 0;
  $("#system-parallel").textContent = system.parallel_accounts ?? 0;
  $("#system-parallel-default").textContent = `default: ${system.parallel_accounts_default ?? "-"}`;
  $("#system-parallel-input").value = system.parallel_accounts ?? system.parallel_accounts_default ?? 1;

  const rows = stats.map((item) => `
    <td class="mono">${escapeHtml(shortId(item.api_key))}</td>
    <td>${Number(item.total_tasks || 0)}</td>
    <td>${Number(item.done_tasks || 0)}</td>
    <td>${Number(item.queued_tasks || 0)}</td>
    <td>${Number(item.running_tasks || 0)}</td>
    <td>${Number(item.error_tasks || 0)}</td>
  `);
  renderTable($("#stats-table"), ["API key", "Jami", "Done", "Queued", "Running", "Error"], rows);
  showJsonResult(catalog, $("#api-catalog"));
  if (notify) showToast("Tizim holati yangilandi.");
}

function renderTable(container, headers, rows) {
  if (!rows.length) return renderEmpty(container, "Ma'lumot topilmadi.");
  container.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((row) => `<tr>${row}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;
}

function renderEmpty(container, message) {
  container.innerHTML = `<p class="empty">${escapeHtml(message)}</p>`;
}

function showJsonResult(data, container) {
  if (!container) {
    showToast(data.message || "Amal bajarildi.");
    return;
  }
  container.hidden = false;
  container.innerHTML = "";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(data, null, 2);
  container.appendChild(pre);
}

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.auth !== false && state.token) headers.set("X-Token", state.token);
  if (options.apiKey) headers.set("X-API-Key", options.apiKey);
  let body = options.body;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }

  const response = await fetch(url, {
    method: options.method || "GET",
    headers,
    body,
  });
  const data = await readJson(response);
  if (!response.ok) {
    if (response.status === 401 && options.auth !== false) setLoggedOut();
    throw new Error(errorMessage(data, response.status));
  }
  return data;
}

async function readJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

function errorMessage(data, status) {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((item) => item.msg || JSON.stringify(item)).join(", ");
  return `So'rov bajarilmadi. HTTP ${status}`;
}

async function withButton(button, loadingText, action) {
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = loadingText;
  try {
    return await action();
  } catch (error) {
    showToast(error.message, true);
    return null;
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function confirmAction(message) {
  el.confirmMessage.textContent = message;
  el.confirmDialog.hidden = false;
  return new Promise((resolve) => {
    state.confirmResolve = resolve;
  });
}

function closeConfirm(result) {
  el.confirmDialog.hidden = true;
  if (state.confirmResolve) state.confirmResolve(result);
  state.confirmResolve = null;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast("Nusxalandi.");
  } catch {
    const input = document.createElement("textarea");
    input.value = text;
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
    showToast("Nusxalandi.");
  }
}

function startRefreshTimer() {
  stopRefreshTimer();
  state.refreshTimer = window.setInterval(() => {
    if (state.page === "dashboard") loadDashboard().catch(() => {});
  }, 10000);
}

function stopRefreshTimer() {
  if (state.refreshTimer) window.clearInterval(state.refreshTimer);
  state.refreshTimer = null;
}

function showToast(message, isError = false) {
  el.toast.textContent = message;
  el.toast.classList.toggle("error", isError);
  el.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    el.toast.hidden = true;
  }, 3600);
}

function serviceLabel(service) {
  if (service === "views") return "Views";
  if (service === "reactions") return "Reactions";
  if (service === "shares") return "Shares";
  if (service === "sponsored_search") return "Reklama qidiruvi";
  return service || "Task";
}

function renderSponsoredAdList(ads) {
  if (!Array.isArray(ads) || !ads.length) return '<span class="muted-text">Hech nima topmadi</span>';
  return `<div class="compact-list">${ads.map((ad) => {
    const label = ad.username ? `@${ad.username}` : (ad.name || "-");
    return ad.link
      ? `<a href="${escapeHtml(ad.link)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`
      : `<span>${escapeHtml(label)}</span>`;
  }).join("")}</div>`;
}

function renderSponsoredViewerList(viewers) {
  if (!Array.isArray(viewers) || !viewers.length) return "-";
  return `<div class="compact-list">${viewers.map((viewer) => `
    <span>
      <strong>${escapeHtml(formatTelegramAccount(viewer.account))}</strong>
      <small>${escapeHtml(viewer.session || "-")} · ${Number(viewer.sightings || 0)} marta</small>
    </span>
  `).join("")}</div>`;
}

function formatTelegramAccount(account) {
  const value = String(account || "-");
  return value !== "-" && !/^\d+$/.test(value) && !value.startsWith("@") ? `@${value}` : value;
}

function statusPill(status) {
  return `<span class="status-pill ${escapeHtml(status || "")}">${escapeHtml(status || "-")}</span>`;
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : "-";
}

function shortLink(value) {
  if (!value) return "-";
  return value.length > 42 ? `${value.slice(0, 39)}...` : value;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return date.toLocaleString("uz-UZ", { dateStyle: "short", timeStyle: "short" });
}

function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, number));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replaceAll('"', '\\"');
}
