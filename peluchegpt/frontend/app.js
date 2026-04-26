// app.js — Lógica completa do PelucheGPT
const API_BASE = "http://127.0.0.1:7474";
const STORAGE_KEY = "peluchegpt_history";

// ── ELEMENTOS ──────────────────────────────────────────────────────────────
const tabs         = document.querySelectorAll(".tab-btn");
const tabPanels    = document.querySelectorAll(".tab");
const chatMessages = document.getElementById("chat-messages");
const typingEl     = document.getElementById("typing-indicator");
const chatForm     = document.getElementById("chat-form");
const chatInput    = document.getElementById("chat-input");
const clearBtn     = document.getElementById("clear-history-btn");
const reindexBtn   = document.getElementById("reindex-btn");
const loreSearch   = document.getElementById("lore-search");
const loreRefresh  = document.getElementById("lore-refresh-btn");
const loreTable    = document.getElementById("lore-table");
const botDot       = document.getElementById("bot-dot");
const botStatusTxt = document.getElementById("bot-status-text");
const botToggleBtn = document.getElementById("bot-toggle-btn");
const botBtnIcon   = document.getElementById("bot-btn-icon");
const botBtnLabel  = document.getElementById("bot-btn-label");
const botLogEl     = document.getElementById("bot-log-content");

let history    = loadHistory();
let botRunning = false;
let botPid     = null;

// ── ABAS ───────────────────────────────────────────────────────────────────
tabs.forEach(btn => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    tabs.forEach(b => b.classList.remove("active"));
    tabPanels.forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(target).classList.add("active");

    if (target === "painel")       refreshPainel();
    if (target === "lore")         refreshLoreTable();
    if (target === "config")       loadConfigForm();
  });
});

// ── HISTÓRICO ──────────────────────────────────────────────────────────────
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); }
  catch { return []; }
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
    const badges = document.createElement("div");
    badges.className = "badges";

    const srcBadge = document.createElement("span");
    srcBadge.className = `badge ${source}`;
    srcBadge.textContent = source === "local" ? "🟢 LOCAL" : "🔵 GEMINI";
    badges.appendChild(srcBadge);

    if (tokens) {
      const tokBadge = document.createElement("span");
      tokBadge.className = "badge tokens";
      tokBadge.textContent = `${tokens} tokens`;
      badges.appendChild(tokBadge);
    }

    bubble.appendChild(badges);
  }

  wrapper.appendChild(bubble);
  chatMessages.appendChild(wrapper);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function renderHistory() {
  chatMessages.innerHTML = "";
  history.forEach(item =>
    appendMessage(item.role, item.content, item.source, item.tokens_used)
  );
}

// ── CHAT ───────────────────────────────────────────────────────────────────
chatForm.addEventListener("submit", async e => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;

  appendMessage("user", message);
  history.push({ role: "user", content: message });
  saveHistory();
  chatInput.value = "";

  typingEl.classList.remove("hidden");
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
    typingEl.classList.add("hidden");
  }
});

clearBtn.addEventListener("click", () => {
  history = [];
  saveHistory();
  renderHistory();
});

reindexBtn.addEventListener("click", async () => {
  reindexBtn.disabled = true;
  reindexBtn.textContent = "⏳ Reindexando...";
  try {
    await fetch(`${API_BASE}/lore/reindex`, { method: "POST" });
    reindexBtn.textContent = "✅ Concluído!";
    setTimeout(() => { reindexBtn.textContent = "⟳ Reindexar Lore"; }, 2000);
  } catch {
    reindexBtn.textContent = "❌ Erro";
    setTimeout(() => { reindexBtn.textContent = "⟳ Reindexar Lore"; }, 2000);
  } finally {
    reindexBtn.disabled = false;
  }
});

// ── PAINEL ─────────────────────────────────────────────────────────────────
async function refreshPainel() {
  try {
    const data = await (await fetch(`${API_BASE}/bot/stats`)).json();

    document.getElementById("stat-users").textContent =
      data.economy_users ?? data.users ?? "—";
    document.getElementById("stat-lore").textContent =
      data.lore_entries ?? "—";
    document.getElementById("stat-warns").textContent =
      data.active_warns ?? "—";
    document.getElementById("stat-music").textContent =
      data.music_cache ?? "—";
  } catch {
    ["stat-users","stat-lore","stat-warns","stat-music"]
      .forEach(id => document.getElementById(id).textContent = "err");
  }
}

function addLog(text, type = "info") {
  if (!botLogEl) return;
  const line = document.createElement("div");
  line.className = `log-line log-${type}`;
  const now = new Date().toLocaleTimeString("pt-BR");
  line.textContent = `[${now}] ${text}`;
  botLogEl.appendChild(line);
  botLogEl.scrollTop = botLogEl.scrollHeight;

  // Limita a 200 linhas
  while (botLogEl.children.length > 200)
    botLogEl.removeChild(botLogEl.firstChild);
}

// ── LORE ───────────────────────────────────────────────────────────────────
async function refreshLoreTable() {
  loreTable.textContent = "Carregando...";
  try {
    const q = encodeURIComponent(loreSearch.value.trim());
    const rows = await (await fetch(`${API_BASE}/lore/list?search=${q}`)).json();

    if (!rows.length) {
      loreTable.innerHTML = `<div style="color:var(--text-dim);padding:20px;text-align:center;">Nenhuma entrada encontrada.</div>`;
      return;
    }

    loreTable.innerHTML = rows.map(r => `
      <div class="lore-entry">
        <strong>${r.title || "Sem título"}</strong>
        <small>${(r.content || "").slice(0, 280)}${r.content?.length > 280 ? "..." : ""}</small>
      </div>
    `).join("");
  } catch {
    loreTable.innerHTML = `<div style="color:var(--red);padding:20px;">Erro ao carregar lore.</div>`;
  }
}

loreRefresh.addEventListener("click", refreshLoreTable);
loreSearch.addEventListener("keydown", e => { if (e.key === "Enter") refreshLoreTable(); });

// ── CONFIGURAÇÕES ──────────────────────────────────────────────────────────
async function loadConfigForm() {
  try {
    const cfg = await (await fetch(`${API_BASE}/config`)).json();

    setValue("cfg-gemini-key",     cfg.gemini_api_key     || "");
    setValue("cfg-num-gpu",        cfg.ollama_num_gpu     ?? 12);
    setValue("cfg-num-threads",    cfg.ollama_num_threads ?? 5);
    setValue("cfg-num-ctx",        cfg.ollama_num_ctx     ?? 2048);
    setValue("cfg-discord-token",  cfg.discord_token      || "");
    setValue("cfg-bot-path",       cfg.bot_path           || "C:/P3-LUCH3/main.py");
    setValue("cfg-drive-folder",   cfg.drive_folder_id    || "");
    setValue("cfg-python-path", cfg.python_path || "C:/P3-LUCH3/.venv/Scripts/python.exe");

    const thresholdPct = Math.round((cfg.complexity_threshold ?? 0.7) * 100);
    const slider = document.getElementById("cfg-threshold");
    const sliderVal = document.getElementById("cfg-threshold-val");
    if (slider) {
      slider.value = thresholdPct;
      sliderVal.textContent = thresholdPct + "%";
    }
  } catch (e) {
    console.error("Erro ao carregar config:", e);
  }
}

function setValue(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

async function saveConfig() {
  const feedback = document.getElementById("save-feedback");
  const threshold = (parseInt(document.getElementById("cfg-threshold")?.value || "70") / 100);

  const payload = {
    gemini_api_key:      document.getElementById("cfg-gemini-key")?.value     || "",
    complexity_threshold: threshold,
    ollama_num_gpu:      parseInt(document.getElementById("cfg-num-gpu")?.value     || "12"),
    ollama_num_threads:  parseInt(document.getElementById("cfg-num-threads")?.value || "5"),
    ollama_num_ctx:      parseInt(document.getElementById("cfg-num-ctx")?.value     || "2048"),
    discord_token:       document.getElementById("cfg-discord-token")?.value   || "",
    bot_path:            document.getElementById("cfg-bot-path")?.value        || "",
    drive_folder_id:     document.getElementById("cfg-drive-folder")?.value    || "",
    python_path: document.getElementById("cfg-python-path")?.value || "",
  };

  try {
    const resp = await fetch(`${API_BASE}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      feedback.textContent = "✅ Configurações salvas!";
      feedback.className = "save-feedback ok";
    } else {
      throw new Error("Resposta não-ok");
    }
  } catch {
    feedback.textContent = "❌ Erro ao salvar.";
    feedback.className = "save-feedback err";
  }

  setTimeout(() => { feedback.textContent = ""; feedback.className = "save-feedback"; }, 3000);
}

function togglePassword(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === "password") {
    input.type = "text";
    btn.textContent = "🙈";
  } else {
    input.type = "password";
    btn.textContent = "👁";
  }
}

// ── CONTROLE DO BOT DISCORD ────────────────────────────────────────────────
function setBotStatus(status) {
  botDot.className = `status-dot ${status}`;

  if (status === "online") {
    botStatusTxt.textContent = "online";
    botBtnIcon.textContent = "⏹";
    botBtnLabel.textContent = "Desligar Bot";
    botToggleBtn.classList.add("running");
    botToggleBtn.disabled = false;
    // Só loga se estava offline antes
    if (!botRunning) addLog("Bot Discord conectado!", "ok");
    botRunning = true;
  } else if (status === "loading") {
    botStatusTxt.textContent = "iniciando...";
    botBtnIcon.textContent = "⏳";
    botBtnLabel.textContent = "Iniciando...";
    botToggleBtn.classList.remove("running");
    botToggleBtn.disabled = true;
  } else {
    botStatusTxt.textContent = "offline";
    botBtnIcon.textContent = "▶";
    botBtnLabel.textContent = "Ligar Bot";
    botToggleBtn.classList.remove("running");
    botToggleBtn.disabled = false;
    botRunning = false;
  }
}

async function toggleBot() {
  if (botRunning) {
    try {
      const resp = await fetch(`${API_BASE}/bot/stop`, { method: "POST" });
      if (resp.ok) {
        setBotStatus("offline");
        addLog("Bot Discord desligado.", "warn");
      }
    } catch {
      addLog("Erro ao desligar o bot.", "error");
    }
  } else {
    setBotStatus("loading");
    addLog("Iniciando bot Discord...", "info");
    try {
      const resp = await fetch(`${API_BASE}/bot/start`, { method: "POST" });
      if (!resp.ok) {
        setBotStatus("offline");
        addLog("Erro ao iniciar o bot (verifique o caminho no config).", "error");
        return;
      }

      // Tenta confirmar até 5 vezes com 2s de intervalo (10s total)
      let confirmed = false;
      for (let i = 0; i < 5; i++) {
        await new Promise(r => setTimeout(r, 2000));
        try {
          const check = await fetch(`${API_BASE}/bot/status`);
          const data = await check.json();
          if (data.running) {
            confirmed = true;
            break;
          }
        } catch { /* continua tentando */ }
      }

      if (confirmed) {
        setBotStatus("online");
      } else {
        setBotStatus("offline");
        addLog("Bot não respondeu após 10s. Verifique o token e o caminho.", "warn");
      }
    } catch {
      setBotStatus("offline");
      addLog("Erro de comunicação com o backend.", "error");
    }
  }
}

// ── POLLING DE STATUS ──────────────────────────────────────────────────────
// Substitui essa função no app.js:
async function pollBotStatus() {
  try {
    const resp = await fetch(`${API_BASE}/bot/status`);
    const data = await resp.json();
    // Só atualiza se o estado MUDOU
    if (data.running && !botRunning) {
      setBotStatus("online");
    } else if (!data.running && botRunning) {
      setBotStatus("offline");
      addLog("Bot Discord desconectado.", "warn");
    }
  } catch {
    // backend offline
  }
}

// ── INIT ───────────────────────────────────────────────────────────────────
renderHistory();
refreshPainel();
refreshLoreTable();
loadConfigForm();

setInterval(refreshPainel, 15000);
setInterval(pollBotStatus, 5000);