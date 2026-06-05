const allowedServices = new Set(["views", "reactions", "shares"]);
const finishedStatuses = new Set(["done", "error", "rejected"]);
const authTokenStorage = "post_api_frontend_auth_token";
const apiKeyStorage = "post_api_frontend_api_key";
const trackedStorage = "post_api_frontend_tracked_tasks";

const state = {
  authToken: localStorage.getItem(authTokenStorage) || "",
  currentUser: null,
  trackedIds: loadTrackedIds(),
  trackedTasks: new Map(),
};

const el = {
  loginPanel: document.querySelector("#login-panel"),
  appShell: document.querySelector("#app-shell"),
  loginForm: document.querySelector("#login-form"),
  username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  loginSubmit: document.querySelector("#login-btn"),
  userChip: document.querySelector("#user-chip"),
  currentUser: document.querySelector("#current-user"),
  logout: document.querySelector("#logout-btn"),
  sessionPanel: document.querySelector("#session-panel"),
  sessionRawForm: document.querySelector("#session-raw-form"),
  sessionRawText: document.querySelector("#session-raw-text"),
  sessionRawApiId: document.querySelector("#session-raw-api-id"),
  sessionRawApiHash: document.querySelector("#session-raw-api-hash"),
  sessionRawUpdate: document.querySelector("#session-raw-update"),
  sessionRawSubmit: document.querySelector("#session-raw-btn"),
  sessionJsonForm: document.querySelector("#session-json-form"),
  sessionJsonFiles: document.querySelector("#session-json-files"),
  sessionJsonUpdate: document.querySelector("#session-json-update"),
  sessionJsonSubmit: document.querySelector("#session-json-btn"),
  sessionCsvForm: document.querySelector("#session-csv-form"),
  sessionCsvFile: document.querySelector("#session-csv-file"),
  sessionCsvUpdate: document.querySelector("#session-csv-update"),
  sessionCsvSubmit: document.querySelector("#session-csv-btn"),
  sessionFolderForm: document.querySelector("#session-folder-form"),
  sessionFolder: document.querySelector("#session-folder"),
  sessionFolderClear: document.querySelector("#session-folder-clear"),
  sessionFolderSubmit: document.querySelector("#session-folder-btn"),
  sessionStats: document.querySelector("#session-stats-btn"),
  sessionTotal: document.querySelector("#session-total"),
  sessionActive: document.querySelector("#session-active"),
  sessionBanned: document.querySelector("#session-banned"),
  sessionFlood: document.querySelector("#session-flood"),
  sessionSleep: document.querySelector("#session-sleep"),
  sessionResult: document.querySelector("#session-result"),
  form: document.querySelector("#task-form"),
  apiKey: document.querySelector("#api-key"),
  copyApiKey: document.querySelector("#copy-api-key-btn"),
  postLink: document.querySelector("#post-link"),
  accounts: document.querySelector("#accounts"),
  reaction: document.querySelector("#reaction"),
  reactionField: document.querySelector("#reaction-field"),
  submit: document.querySelector("#submit-btn"),
  refresh: document.querySelector("#refresh-btn"),
  clearFinished: document.querySelector("#clear-finished-btn"),
  queueCount: document.querySelector("#queue-count"),
  trackedCount: document.querySelector("#tracked-count"),
  lastResult: document.querySelector("#last-result"),
  systemPanel: document.querySelector("#system-panel"),
  systemRefresh: document.querySelector("#system-refresh-btn"),
  systemPaused: document.querySelector("#system-paused"),
  systemErrors: document.querySelector("#system-errors"),
  systemQueue: document.querySelector("#system-queue"),
  systemReason: document.querySelector("#system-reason"),
  systemResume: document.querySelector("#system-resume-btn"),
  parallelForm: document.querySelector("#parallel-form"),
  parallelAccounts: document.querySelector("#parallel-accounts"),
  parallelSave: document.querySelector("#parallel-save-btn"),
  activeList: document.querySelector("#active-list"),
  recentList: document.querySelector("#recent-list"),
  toast: document.querySelector("#toast"),
};

el.apiKey.value = localStorage.getItem(apiKeyStorage) || "";

document.querySelectorAll("input[name='service']").forEach((radio) => {
  radio.addEventListener("change", syncServiceFields);
});

document.querySelectorAll("[data-emoji]").forEach((button) => {
  button.addEventListener("click", () => {
    el.reaction.value = button.dataset.emoji;
    el.reaction.focus();
  });
});

el.apiKey.addEventListener("input", () => {
  localStorage.setItem(apiKeyStorage, el.apiKey.value.trim());
  syncApiKeyCopyState();
});
el.copyApiKey.addEventListener("click", copyApiKey);

el.loginForm.addEventListener("submit", submitLogin);
el.logout.addEventListener("click", logout);
el.sessionRawForm.addEventListener("submit", submitSessionRaw);
el.sessionJsonForm.addEventListener("submit", submitSessionJson);
el.sessionCsvForm.addEventListener("submit", submitSessionCsv);
el.sessionFolderForm.addEventListener("submit", submitSessionFolder);
el.sessionStats.addEventListener("click", () => refreshSessionStats());
el.systemRefresh.addEventListener("click", () => refreshSystemStatus());
el.systemResume.addEventListener("click", resumeSystem);
el.parallelForm.addEventListener("submit", saveParallelAccounts);
el.form.addEventListener("submit", submitTask);
el.refresh.addEventListener("click", refreshDashboard);
el.clearFinished.addEventListener("click", clearFinishedTasks);

syncServiceFields();
syncApiKeyCopyState();
initializeAuth();
setInterval(() => {
  if (state.authToken) refreshTrackedTasks();
}, 3000);
setInterval(() => {
  if (state.authToken) refreshQueue();
}, 5000);

async function initializeAuth() {
  if (!state.authToken) {
    setLoggedOut();
    return;
  }

  try {
    const response = await fetch("/auth/me", {
      headers: authHeaders(),
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }
    setLoggedIn(data);
    await refreshDashboard();
  } catch {
    setLoggedOut();
  }
}

async function submitLogin(event) {
  event.preventDefault();

  el.loginSubmit.disabled = true;
  el.loginSubmit.textContent = "Kirilmoqda...";

  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        username: el.username.value.trim(),
        password: el.password.value,
      }),
    });

    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }

    state.authToken = data.token;
    localStorage.setItem(authTokenStorage, state.authToken);
    setLoggedIn(data);
    el.password.value = "";
    showToast("Login muvaffaqiyatli.");
    await refreshDashboard();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    el.loginSubmit.disabled = false;
    el.loginSubmit.textContent = "Kirish";
  }
}

async function logout() {
  const token = state.authToken;
  setLoggedOut();
  if (!token) return;

  try {
    await fetch("/auth/logout", {
      method: "POST",
      headers: {
        "X-Token": token,
      },
    });
  } catch {
    // Local logout is enough for the UI; backend cleanup can fail only temporarily.
  }
}

function setLoggedIn(user) {
  state.currentUser = user;
  if (user.api_key) {
    el.apiKey.value = user.api_key;
    localStorage.setItem(apiKeyStorage, user.api_key);
  }
  syncApiKeyCopyState();
  el.loginPanel.hidden = true;
  el.appShell.hidden = false;
  el.userChip.hidden = false;
  el.systemPanel.hidden = user.role !== "admin";
  el.sessionPanel.hidden = user.role !== "admin";
  el.currentUser.textContent = `${user.username || "user"} (${user.role || "user"})`;
  if (user.role === "admin") {
    refreshSystemStatus({ silent: true });
    refreshSessionStats({ silent: true });
  }
}

function setLoggedOut() {
  state.authToken = "";
  state.currentUser = null;
  localStorage.removeItem(authTokenStorage);
  localStorage.removeItem(apiKeyStorage);
  el.apiKey.value = "";
  syncApiKeyCopyState();
  el.loginPanel.hidden = false;
  el.appShell.hidden = true;
  el.userChip.hidden = true;
  el.systemPanel.hidden = true;
  el.sessionPanel.hidden = true;
  el.sessionResult.hidden = true;
  el.systemReason.hidden = true;
  updateSystemStatus({});
  updateSessionStats({});
  renderEmpty(el.activeList, "Login qiling.");
  renderEmpty(el.recentList, "Login qiling.");
  el.trackedCount.textContent = "0";
  el.queueCount.textContent = "0";
}

function authHeaders(extra = {}) {
  return state.authToken
    ? { ...extra, "X-Token": state.authToken }
    : extra;
}

async function refreshSystemStatus(options = {}) {
  if (!requireAdmin()) return;
  try {
    const response = await fetch("/admin/system/status", {
      headers: authHeaders(),
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }
    updateSystemStatus(data);
    if (!options.silent) {
      showToast("Tizim holati yangilandi.");
    }
  } catch (error) {
    if (!options.silent) {
      showToast(error.message, true);
    }
  }
}

async function resumeSystem() {
  if (!requireAdmin()) return;
  el.systemResume.disabled = true;
  const oldText = el.systemResume.textContent;
  el.systemResume.textContent = "Davom ettirilmoqda...";
  try {
    const response = await fetch("/admin/sessions/resume", {
      method: "POST",
      headers: authHeaders(),
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }
    renderSystemResult(data);
    showToast("Tizim davom ettirildi.");
    await refreshSystemStatus({ silent: true });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    el.systemResume.disabled = false;
    el.systemResume.textContent = oldText;
  }
}

async function saveParallelAccounts(event) {
  event.preventDefault();
  if (!requireAdmin()) return;

  const value = Number(el.parallelAccounts.value);
  if (!Number.isInteger(value) || value < 1 || value > 5000) {
    showToast("Parallel accounts 1 dan 5000 gacha bo'lishi kerak.", true);
    return;
  }

  el.parallelSave.disabled = true;
  const oldText = el.parallelSave.textContent;
  el.parallelSave.textContent = "Saqlanmoqda...";
  try {
    const response = await fetch("/admin/system/settings", {
      method: "PATCH",
      headers: authHeaders({
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({ parallel_accounts: value }),
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }
    showToast("Parallel accounts saqlandi.");
    await refreshSystemStatus({ silent: true });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    el.parallelSave.disabled = false;
    el.parallelSave.textContent = oldText;
  }
}

async function submitSessionRaw(event) {
  event.preventDefault();
  if (!requireAdmin()) return;

  const sessions = el.sessionRawText.value.trim();
  if (!sessions) {
    showToast("Session qatorlarini kiriting.", true);
    el.sessionRawText.focus();
    return;
  }

  await runJsonRequest(
    el.sessionRawSubmit,
    "/admin/sessions/import-raw",
    {
      sessions,
      api_id: Number(el.sessionRawApiId.value) || 2040,
      api_hash: el.sessionRawApiHash.value.trim(),
      update: el.sessionRawUpdate.checked,
      default_status: "active",
    },
    "Session qatorlari DBga yuklandi."
  );
}

async function submitSessionJson(event) {
  event.preventDefault();
  if (!requireAdmin()) return;

  const files = Array.from(el.sessionJsonFiles.files || []);
  if (!files.length) {
    showToast("JSON session fayl tanlang.", true);
    return;
  }

  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  const update = el.sessionJsonUpdate.checked ? "true" : "false";

  await runSessionUpload(
    el.sessionJsonSubmit,
    `/admin/sessions/import-json?update=${update}&default_status=active`,
    formData,
    "JSON sessionlar DBga yuklandi."
  );
}

async function submitSessionCsv(event) {
  event.preventDefault();
  if (!requireAdmin()) return;

  const file = el.sessionCsvFile.files && el.sessionCsvFile.files[0];
  if (!file) {
    showToast("CSV session fayl tanlang.", true);
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  const update = el.sessionCsvUpdate.checked ? "true" : "false";

  await runSessionUpload(
    el.sessionCsvSubmit,
    `/admin/sessions/import-csv?update=${update}`,
    formData,
    "CSV sessionlar DBga yuklandi."
  );
}

async function submitSessionFolder(event) {
  event.preventDefault();
  if (!requireAdmin()) return;

  const folder = el.sessionFolder.value.trim() || "sessions";
  const clearFirst = el.sessionFolderClear.checked ? "true" : "false";
  const query = new URLSearchParams({
    sessions_dir: folder,
    clear_first: clearFirst,
  });

  await runSessionUpload(
    el.sessionFolderSubmit,
    `/admin/sessions/upload-folder?${query}`,
    null,
    "Server papkasidagi sessionlar DBga yuklandi."
  );
}

async function refreshSessionStats(options = {}) {
  if (!requireAdmin()) return;
  try {
    const response = await fetch("/admin/sessions/stats", {
      headers: authHeaders(),
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }
    updateSessionStats(data);
    if (!options.silent) {
      renderSessionResult(data);
    }
  } catch (error) {
    if (!options.silent) {
      showToast(error.message, true);
    }
  }
}

async function runSessionUpload(button, url, body, successMessage) {
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "Yuklanmoqda...";

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: authHeaders(),
      body,
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }
    renderSessionResult(data);
    refreshSessionStats({ silent: true });
    showToast(successMessage);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function runJsonRequest(button, url, payload, successMessage) {
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "Yuklanmoqda...";

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: authHeaders({
        "Content-Type": "application/json",
      }),
      body: JSON.stringify(payload),
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }
    renderSessionResult(data);
    refreshSessionStats({ silent: true });
    showToast(successMessage);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function renderSessionResult(data) {
  el.sessionResult.hidden = false;
  el.sessionResult.innerHTML = "";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(data, null, 2);
  el.sessionResult.appendChild(pre);
}

function renderSystemResult(data) {
  el.systemReason.hidden = false;
  el.systemReason.innerHTML = "";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(data, null, 2);
  el.systemReason.appendChild(pre);
}

function updateSystemStatus(data) {
  const isPaused = Boolean(data.is_paused);
  el.systemPaused.textContent = data.is_paused === undefined ? "-" : (isPaused ? "Pauza" : "Ishlayapti");
  el.systemPaused.style.color = isPaused ? "var(--danger)" : "var(--accent-strong)";
  el.systemErrors.textContent = Number(data.recent_errors || 0);
  el.systemQueue.textContent = Number(data.queue_size || 0);
  if (data.parallel_accounts) {
    el.parallelAccounts.value = data.parallel_accounts;
  }

  if (data.paused_reason) {
    el.systemReason.hidden = false;
    el.systemReason.textContent = data.paused_reason;
  } else {
    el.systemReason.hidden = true;
    el.systemReason.textContent = "";
  }
}

function updateSessionStats(data) {
  el.sessionTotal.textContent = Number(data.total || 0);
  el.sessionActive.textContent = Number(data.active || data.active_total || 0);
  el.sessionBanned.textContent = Number(data.banned || 0);
  el.sessionFlood.textContent = Number(data.flood || 0);
  el.sessionSleep.textContent = Number(data.sleep || 0);
}

function requireAdmin() {
  if (!state.authToken || state.currentUser?.role !== "admin") {
    showToast("Bu amal faqat admin uchun.", true);
    return false;
  }
  return true;
}

function selectedService() {
  return document.querySelector("input[name='service']:checked").value;
}

function syncServiceFields() {
  el.reactionField.hidden = selectedService() !== "reactions";
}

function syncApiKeyCopyState() {
  el.copyApiKey.disabled = !el.apiKey.value.trim();
}

async function copyApiKey() {
  const apiKey = el.apiKey.value.trim();
  if (!apiKey) {
    showToast("API key hali olinmagan.", true);
    return;
  }

  try {
    copyTextFallback(apiKey);
    showToast("API key nusxalandi.");
  } catch (error) {
    try {
      if (!navigator.clipboard?.writeText) {
        throw error;
      }
      await navigator.clipboard.writeText(apiKey);
      showToast("API key nusxalandi.");
    } catch {
      showToast("API keyni nusxalab bo'lmadi.", true);
    }
  }
}

function copyTextFallback(text) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.top = "0";
  input.style.left = "0";
  input.style.width = "1px";
  input.style.height = "1px";
  input.style.opacity = "0";
  input.style.pointerEvents = "none";
  document.body.appendChild(input);
  input.focus({ preventScroll: true });
  input.select();
  input.setSelectionRange(0, input.value.length);
  const copied = document.execCommand("copy");
  document.body.removeChild(input);
  if (!copied) {
    throw new Error("Copy failed");
  }
}

async function submitTask(event) {
  event.preventDefault();

  if (!state.authToken) {
    showToast("Avval login qiling.", true);
    return;
  }

  const service = selectedService();
  const apiKey = el.apiKey.value.trim();
  const postLink = el.postLink.value.trim();
  const accountLimit = Number(el.accounts.value);

  if (!apiKey) {
    showToast("API key kiriting.", true);
    el.apiKey.focus();
    return;
  }

  const payload = { post_link: postLink };
  if (Number.isInteger(accountLimit) && accountLimit > 0) {
    payload.accounts = accountLimit;
  }
  if (service === "reactions") {
    payload.reaction = el.reaction.value.trim() || "👍";
  }

  el.submit.disabled = true;
  el.submit.textContent = "Yuborilmoqda...";

  try {
    const response = await fetch(`/task/${service}`, {
      method: "POST",
      headers: authHeaders({
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      }),
      body: JSON.stringify(payload),
    });

    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(getErrorMessage(data, response.status));
    }

    trackTask(data.task_id, data);
    renderLastResult(data);
    showToast("Task qabul qilindi.");
    await refreshDashboard();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    el.submit.disabled = false;
    el.submit.textContent = "Task yaratish";
  }
}

async function refreshDashboard() {
  if (!state.authToken) return;
  await Promise.all([refreshQueue(), refreshTrackedTasks(), refreshRecentTasks()]);
}

async function refreshQueue() {
  if (!state.authToken) return;
  try {
    const response = await fetch("/queue", {
      headers: authHeaders(),
    });
    const data = await readJson(response);
    el.queueCount.textContent = Number(data.waiting || 0);
  } catch {
    el.queueCount.textContent = "-";
  }
}

async function refreshTrackedTasks() {
  if (!state.authToken) return;
  if (!state.trackedIds.length) {
    renderTrackedTasks();
    return;
  }

  const results = await Promise.allSettled(
    state.trackedIds.map((id) => fetchStatus(id))
  );

  results.forEach((result, index) => {
    if (result.status === "fulfilled" && result.value) {
      state.trackedTasks.set(state.trackedIds[index], result.value);
    }
  });

  renderTrackedTasks();
}

async function refreshRecentTasks() {
  if (!state.authToken) return;
  try {
    const response = await fetch("/tasks?limit=20&offset=0", {
      headers: authHeaders(),
    });
    const data = await readJson(response);
    const tasks = Array.isArray(data)
      ? data.filter((task) => allowedServices.has(task.service)).slice(0, 10)
      : [];
    renderTaskList(el.recentList, tasks, { removable: false });
  } catch (error) {
    renderEmpty(el.recentList, "So'nggi tasklarni olib bo'lmadi.");
  }
}

async function fetchStatus(taskId) {
  const response = await fetch(`/status/${encodeURIComponent(taskId)}`, {
    headers: authHeaders(),
  });
  const data = await readJson(response);
  if (!response.ok) {
    throw new Error(getErrorMessage(data, response.status));
  }
  return data;
}

function trackTask(taskId, initialData = null) {
  if (!taskId) return;
  state.trackedIds = [taskId, ...state.trackedIds.filter((id) => id !== taskId)].slice(0, 30);
  if (initialData) {
    state.trackedTasks.set(taskId, {
      task_id: taskId,
      service: "",
      status: initialData.status,
      priority: initialData.priority,
      error: initialData.message,
      total: 0,
      done: 0,
      percent: 0,
    });
  }
  saveTrackedIds();
}

function clearFinishedTasks() {
  state.trackedIds = state.trackedIds.filter((id) => {
    const task = state.trackedTasks.get(id);
    return !task || !finishedStatuses.has(task.status);
  });
  saveTrackedIds();
  renderTrackedTasks();
}

function renderTrackedTasks() {
  el.trackedCount.textContent = String(state.trackedIds.length);
  const tasks = state.trackedIds.map((id) => state.trackedTasks.get(id) || { task_id: id, status: "loading" });
  renderTaskList(el.activeList, tasks, { removable: true });
}

function renderTaskList(container, tasks, options) {
  container.innerHTML = "";
  if (!tasks.length) {
    renderEmpty(container, "Hali task yo'q.");
    return;
  }

  tasks.forEach((task) => {
    const row = document.createElement("article");
    row.className = "task-row";

    const body = document.createElement("div");
    const title = document.createElement("div");
    title.className = "task-title";

    const service = document.createElement("strong");
    service.textContent = serviceLabel(task.service);
    title.appendChild(service);

    const status = document.createElement("span");
    status.className = `status-pill ${task.status || ""}`;
    status.textContent = task.status || "unknown";
    title.appendChild(status);
    body.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "task-meta";
    addMeta(meta, `ID: ${shortId(task.task_id)}`);
    addMeta(meta, `Done: ${task.done || 0}/${task.total || 0}`);
    addMeta(meta, `Percent: ${Number(task.percent || 0).toFixed(1)}%`);
    if (task.priority) addMeta(meta, `Priority: ${task.priority}`);
    if (task.post_link) addMeta(meta, task.post_link);
    body.appendChild(meta);

    const progress = document.createElement("div");
    progress.className = "progress";
    const bar = document.createElement("span");
    bar.style.width = `${clampPercent(task.percent)}%`;
    progress.appendChild(bar);
    body.appendChild(progress);

    if (task.error) {
      const error = document.createElement("p");
      error.className = "task-error";
      error.textContent = task.error;
      body.appendChild(error);
    }

    row.appendChild(body);

    if (options.removable) {
      const remove = document.createElement("button");
      remove.className = "remove-btn";
      remove.type = "button";
      remove.textContent = "O'chirish";
      remove.addEventListener("click", () => removeTrackedTask(task.task_id));
      row.appendChild(remove);
    }

    container.appendChild(row);
  });
}

function renderLastResult(data) {
  el.lastResult.hidden = false;
  el.lastResult.innerHTML = "";
  const lines = [
    `Task ID: ${data.task_id}`,
    `Status: ${data.status}`,
    `Priority: ${data.priority}`,
    data.message,
  ].filter(Boolean);

  lines.forEach((line) => {
    const div = document.createElement("div");
    div.textContent = line;
    el.lastResult.appendChild(div);
  });
}

function renderEmpty(container, message) {
  container.innerHTML = "";
  const empty = document.createElement("p");
  empty.className = "empty";
  empty.textContent = message;
  container.appendChild(empty);
}

function addMeta(container, text) {
  const item = document.createElement("span");
  item.textContent = text;
  container.appendChild(item);
}

function removeTrackedTask(taskId) {
  state.trackedIds = state.trackedIds.filter((id) => id !== taskId);
  state.trackedTasks.delete(taskId);
  saveTrackedIds();
  renderTrackedTasks();
}

function serviceLabel(service) {
  if (service === "views") return "Views";
  if (service === "reactions") return "Reactions";
  if (service === "shares") return "Shares";
  return "Task";
}

function shortId(taskId) {
  return taskId ? taskId.slice(0, 8) : "-";
}

function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, number));
}

function loadTrackedIds() {
  try {
    const ids = JSON.parse(localStorage.getItem(trackedStorage) || "[]");
    return Array.isArray(ids) ? ids.filter(Boolean).slice(0, 30) : [];
  } catch {
    return [];
  }
}

function saveTrackedIds() {
  localStorage.setItem(trackedStorage, JSON.stringify(state.trackedIds));
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

function getErrorMessage(data, status) {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((item) => item.msg || JSON.stringify(item)).join(", ");
  }
  return `So'rov bajarilmadi. HTTP ${status}`;
}

function showToast(message, isError = false) {
  el.toast.textContent = message;
  el.toast.classList.toggle("error", isError);
  el.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    el.toast.hidden = true;
  }, 3500);
}
