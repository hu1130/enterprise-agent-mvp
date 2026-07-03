const BASE_URL = process.env.LOCAL_AGENT_URL || "http://127.0.0.1:8788";
const API_TOKEN = process.env.AGENT_API_TOKEN || "";

function headers() {
  const result = { "content-type": "application/json" };
  if (API_TOKEN) {
    result.authorization = `Bearer ${API_TOKEN}`;
  }
  return result;
}

export async function callLocalTool(skillName, params = {}, message = "", userId = "current-user") {
  const response = await fetch(`${BASE_URL}/api/tool/call`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      user_id: userId,
      skill_name: skillName,
      params,
      message,
    }),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `tool call failed: ${response.status}`);
  }
  return data;
}

export async function approveLocalAction(actionId, userId = "current-user") {
  const response = await fetch(`${BASE_URL}/api/tool/approve`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      user_id: userId,
      action_id: actionId,
    }),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `approval failed: ${response.status}`);
  }
  return data;
}

export async function getLocalContext(query, userId = "current-user") {
  return callLocalTool(
    "file.context",
    { query, max_items: 5, max_chars_per_item: 700 },
    `Get local context for: ${query}`,
    userId
  );
}

export async function askConfiguredModel(prompt, userId = "current-user") {
  return callLocalTool("model.chat", { prompt }, "Call configured model", userId);
}

export async function proposeEmailSend(to, subject, body, userId = "current-user") {
  return callLocalTool("email.propose_send", { to, subject, body }, "Propose email send", userId);
}

export async function proposeCalendarEvent(event, userId = "current-user") {
  return callLocalTool("calendar.propose_event", event, "Propose calendar event", userId);
}

export async function fetchAllowedWebPage(url, userId = "current-user") {
  return callLocalTool("web.fetch", { url }, "Fetch allowlisted web page", userId);
}

export async function proposeScriptRun(command, userId = "current-user") {
  return callLocalTool("script.propose_run", { command }, "Propose script run", userId);
}

