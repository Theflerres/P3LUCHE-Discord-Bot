// Lógica de interface do PelucheGPT com persistência local de histórico.
const API_BASE = "http://127.0.0.1:7474";
const STORAGE_KEY = "peluchegpt_history";

const tabs = document.querySelectorAll(".tab-btn");
const tabPanels = document.querySelectorAll(".tab");
const chatMessages = document.getElementById("chat-messages");
const typingIndicator = document.getElementById("typing-indicator");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const clearHistoryBtn = document.getElementById("clear-history-btn");
const reindexBtn = document.getElementById("reindex-btn");
const botStatsEl = document.getElementById("bot-stats");
const loreSearchInput = document.getElementById("lore-search");
const loreRefreshBtn = document.getElementById("lore-refresh-btn");
const loreTableEl = document.getElementById("lore-table");
const configForm = document.getElementById("config-form");

let history = loadHistory();

tabs.forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    tabs.forEach((b) => b.classList.remove("active"));
    tabPanels.forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(target).classList.add("active");
  });
});

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveHistory() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
}

function appendMessage(role, content, source = "", tokens = 0) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content;

  if (role === "assistant" && source) {
    const badge = document.createElement("span");
    badge.className = `badge ${source}`;
    badge.textContent = source === "local" ? "🟢 LOCAL" : "🔵 GEMINI";
    bubble.appendChild(document.createElement("br"));
    bubble.appendChild(badge);
    if (tokens) {
      const tokenTag = document.createElement("span");
      tokenTag.className = "badge";
      tokenTag.textContent = `tokens: ${tokens}`;
      bubble.appendChild(tokenTag);
    }
  }

  wrapper.appendChild(bubble);
  chatMessages.appendChild(wrapper);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function renderHistory() {
  chatMessages.innerHTML = "";
  history.forEach((item) => appendMessage(item.role, item.content, item.source, item.tokens_used));
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;

  appendMessage("user", message);
  history.push({ role: "user", content: message });
  saveHistory();
  chatInput.value = "";

  typingIndicator.classList.remove("hidden");
  try {
    const resp = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
    });
    const data = await resp.json();
    appendMessage("assistant", data.response, data.source, data.tokens_used);
    history.push({
      role: "assistant",
      content: data.response,
      source: data.source,
      tokens_used: data.tokens_used || 0,
    });
    saveHistory();
  } catch (err) {
    appendMessage("assistant", `Erro de comunicação com backend: ${String(err)}`);
  } finally {
    typingIndicator.classList.add("hidden");
  }
});

clearHistoryBtn.addEventListener("click", () => {
  history = [];
  saveHistory();
  renderHistory();
});

reindexBtn.addEventListener("click", async () => {
  reindexBtn.disabled = true;
  reindexBtn.textContent = "Reindexando...";
  try {
    await fetch(`${API_BASE}/lore/reindex`, { method: "POST" });
  } finally {
    reindexBtn.disabled = false;
    reindexBtn.textContent = "Reindexar Lore";
  }
});

async function refreshBotStats() {
  try {
    const data = await (await fetch(`${API_BASE}/bot/stats`)).json();
    botStatsEl.textContent = JSON.stringify(data, null, 2);
  } catch {
    botStatsEl.textContent = "Falha ao carregar estatísticas do bot.";
  }
}

async function refreshLoreTable() {
  const q = encodeURIComponent(loreSearchInput.value.trim());
  const rows = await (await fetch(`${API_BASE}/lore/list?search=${q}`)).json();
  loreTableEl.innerHTML = rows
    .map(
      (r) => `
      <div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #2a2a43;">
        <strong>${r.title || "Sem título"}</strong><br/>
        <small>${(r.content || "").slice(0, 220)}...</small>
      </div>
    `
    )
    .join("") || "Nenhum resultado.";
}

loreRefreshBtn.addEventListener("click", refreshLoreTable);

async function loadConfigForm() {
  const cfg = await (await fetch(`${API_BASE}/config`)).json();
  configForm.innerHTML = "";
  Object.entries(cfg).forEach(([key, value]) => {
    const label = document.createElement("label");
    label.textContent = key;
    const input = document.createElement("input");
    input.name = key;
    input.value = String(value ?? "");
    configForm.appendChild(label);
    configForm.appendChild(input);
  });
  const saveBtn = document.createElement("button");
  saveBtn.type = "submit";
  saveBtn.textContent = "Salvar configurações";
  configForm.appendChild(saveBtn);
}

configForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(configForm);
  const payload = {};
  for (const [k, v] of formData.entries()) {
    if (k === "complexity_threshold") payload[k] = Number(v);
    else if (k === "max_context_chunks") payload[k] = parseInt(v, 10);
    else payload[k] = v;
  }
  await fetch(`${API_BASE}/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
});

renderHistory();
refreshBotStats();
refreshLoreTable();
loadConfigForm();
setInterval(refreshBotStats, 10000);
