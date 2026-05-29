/* =========================================================
   Bella Vista — Chat Frontend Logic
   app.js
   ========================================================= */

const API_BASE = "";        // same origin
const CODE_REGEX = /\bBV-[A-Z0-9]{4,}\b/g;

let sessionId = null;
let isProcessing = false;

// ── DOM refs ──────────────────────────────────────────────
const messagesEl  = document.getElementById("messages");
const inputEl     = document.getElementById("chat-input");
const sendBtn     = document.getElementById("send-btn");
const typingEl    = document.getElementById("typing-indicator");

// ── Session initialisation ────────────────────────────────
async function initSession() {
  try {
    const res = await fetch(`${API_BASE}/session`);
    const data = await res.json();
    sessionId = data.session_id;
  } catch (err) {
    console.error("Failed to init session:", err);
    showError("Unable to connect to the server. Please refresh and try again.");
  }
}

// ── Utility: current time string ─────────────────────────
function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// ── Render helpers ────────────────────────────────────────

/**
 * Format agent message text:
 *  - Wrap BV-XXXX codes in a badge
 *  - Convert **bold** markdown
 *  - Split on double newlines into paragraphs
 */
function formatAgentText(raw) {
  // Escape HTML first
  let text = raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Bold: **text**
  text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // Confirmation codes → badge
  text = text.replace(/(BV-[A-Z0-9]{4,})/g, '<span class="code-badge">$1</span>');

  // Paragraphs: split on blank lines
  const paragraphs = text.split(/\n{2,}/);
  return paragraphs
    .map(p => `<p>${p.replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function appendMessage(role, text) {
  const isAgent = role === "agent";
  const isError = role === "error";

  const wrapper = document.createElement("div");
  wrapper.className = `message message--${isAgent || isError ? (isError ? "error" : "agent") : "guest"}`;
  wrapper.setAttribute("role", "listitem");

  const avatar = document.createElement("div");
  avatar.className = "message__avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = isAgent || isError ? "BV" : "You";

  const bubble = document.createElement("div");
  bubble.className = "message__bubble";

  if (isAgent || isError) {
    bubble.innerHTML = formatAgentText(text);
  } else {
    // Guest message — plain text, escaped
    const p = document.createElement("p");
    p.textContent = text;
    bubble.appendChild(p);
  }

  const time = document.createElement("div");
  time.className = "message__time";
  time.textContent = nowTime();
  bubble.appendChild(time);

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  messagesEl.appendChild(wrapper);
  scrollToBottom();
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showError(msg) {
  appendMessage("error", msg);
}

// ── Typing indicator ─────────────────────────────────────
function showTyping() {
  typingEl.hidden = false;
  scrollToBottom();
}

function hideTyping() {
  typingEl.hidden = true;
}

// ── Send message ─────────────────────────────────────────
async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || isProcessing || !sessionId) return;

  isProcessing = true;
  sendBtn.disabled = true;
  inputEl.disabled = true;

  // Show guest message immediately
  appendMessage("guest", text);
  inputEl.value = "";
  autoResize();

  // Show typing indicator
  showTyping();

  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });

    hideTyping();

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showError(err.detail || `Server error (${res.status}). Please try again.`);
      return;
    }

    const data = await res.json();
    appendMessage("agent", data.reply);
  } catch (err) {
    hideTyping();
    console.error("Chat error:", err);
    showError("Connection lost. Please check your network and try again.");
  } finally {
    isProcessing = false;
    sendBtn.disabled = false;
    inputEl.disabled = false;
    inputEl.focus();
  }
}

// ── Auto-resize textarea ─────────────────────────────────
function autoResize() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
}

// ── Event listeners ──────────────────────────────────────
sendBtn.addEventListener("click", sendMessage);

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

inputEl.addEventListener("input", autoResize);

// ── Boot ─────────────────────────────────────────────────
(async () => {
  await initSession();
  inputEl.focus();
})();
