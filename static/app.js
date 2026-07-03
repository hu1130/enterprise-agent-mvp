const messages = document.querySelector("#messages");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const skillsEl = document.querySelector("#skills");
const auditEl = document.querySelector("#audit");
const lastResult = document.querySelector("#lastResult");
const refreshAudit = document.querySelector("#refreshAudit");

let pendingApproval = null;

function addMessage(role, text) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.textContent = text;
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || data.error || "request_failed");
  }
  return data;
}

async function loadSkills() {
  const data = await api("/api/skills");
  skillsEl.innerHTML = "";
  for (const skill of data.skills) {
    const node = document.createElement("div");
    node.className = "skill";
    node.innerHTML = `
      <strong>${escapeHtml(skill.name)}</strong>
      <span>${escapeHtml(skill.description)}</span>
      <span>risk: ${escapeHtml(skill.risk)} / approval: ${skill.approval_required ? "yes" : "no"}</span>
    `;
    skillsEl.appendChild(node);
  }
}

async function loadAudit() {
  const data = await api("/api/audit");
  auditEl.innerHTML = "";
  for (const event of data.events.slice().reverse()) {
    const node = document.createElement("div");
    node.className = `audit-item ${event.result_status === "blocked" ? "blocked" : ""}`;
    node.innerHTML = `
      <strong>${escapeHtml(event.skill_name)} · ${escapeHtml(event.result_status)}</strong>
      <code>${escapeHtml(event.user_message)}</code>
    `;
    auditEl.appendChild(node);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  input.value = "";
  addMessage("user", message);

  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ user_id: "demo-user", message }),
    });
    addMessage("agent", data.message);
    renderResult(data);
    await loadAudit();
  } catch (error) {
    addMessage("agent", `请求失败：${error.message}`);
  }
});

refreshAudit.addEventListener("click", loadAudit);

function renderResult(data) {
  pendingApproval = data?.skill_result?.approval || null;
  lastResult.innerHTML = "";

  const pre = document.createElement("pre");
  pre.className = "json-box";
  pre.textContent = JSON.stringify(data, null, 2);
  lastResult.appendChild(pre);

  if (pendingApproval?.action_id) {
    const actions = document.createElement("div");
    actions.className = "approval-actions";
    actions.innerHTML = `
      <div>
        <strong>${escapeHtml(pendingApproval.label || "需要确认")}</strong>
        <span>${escapeHtml(pendingApproval.warning || "")}</span>
      </div>
      <button type="button" id="confirmApproval">确认写入</button>
    `;
    lastResult.appendChild(actions);

    document.querySelector("#confirmApproval").addEventListener("click", approvePendingAction);
  }
}

async function approvePendingAction() {
  if (!pendingApproval?.action_id) return;

  try {
    const data = await api("/api/approve", {
      method: "POST",
      body: JSON.stringify({ user_id: "demo-user", action_id: pendingApproval.action_id }),
    });
    addMessage("agent", data.message);
    pendingApproval = null;
    renderResult(data);
    await loadAudit();
  } catch (error) {
    addMessage("agent", `确认失败：${error.message}`);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

addMessage("agent", "第一版已启动。我只能读取授权 workspace 里的文本文件；写文件必须先生成草稿并由你确认。");
loadSkills();
loadAudit();

