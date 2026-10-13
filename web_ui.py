r"""
web_ui.py — Легковесный локальный Web-UI на FastAPI (порт 3080) для AI-Agent Bridge.

Возможности:
1. Автономный тёмный дашборд без внешних npm/CDN зависимостей.
2. Прозрачный мониторинг ReAct-цепочки:
   [Запрос] -> [Вызов инструмента] -> [Результат] -> [Итоговый ответ].
3. Мониторинг статуса: Локальная Ollama (GPU), Linux Mint узел (192.168.1.100), пул SQLite.
"""

import os
import sys
import asyncio
import uuid
import time
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Гарантируем корректный UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from orchestrator import load_agents_rules, load_tools_registry, chat_step
from services import primary_provider, fallback_provider

app = FastAPI(title="AI-Agent Bridge Dashboard", version="1.0.0")

# Реестр инструментов и системные правила
ollama_tools, tools_map = load_tools_registry()
system_prompt = load_agents_rules()


class SessionManager:
    """Изолированное хранилище сессий пользователей с контролем TTL и лимитом сессий."""
    def __init__(self, ttl_seconds: int = 86400, max_sessions: int = 200):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def get_or_create(self, session_id: Optional[str] = None) -> tuple:
        self._cleanup()
        if not session_id or session_id not in self.sessions:
            new_id = uuid.uuid4().hex[:12]
            self.sessions[new_id] = {
                "created_at": time.time(),
                "last_active": time.time(),
                "messages": [{"role": "system", "content": load_agents_rules()}]
            }
            return new_id, self.sessions[new_id]["messages"]
        self.sessions[session_id]["last_active"] = time.time()
        return session_id, self.sessions[session_id]["messages"]

    def clear(self, session_id: Optional[str] = None) -> None:
        if session_id and session_id in self.sessions:
            self.sessions[session_id]["messages"] = [{"role": "system", "content": load_agents_rules()}]
            self.sessions[session_id]["last_active"] = time.time()

    def _cleanup(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self.sessions.items() if now - s["last_active"] > self.ttl_seconds]
        for sid in expired:
            del self.sessions[sid]
        if len(self.sessions) > self.max_sessions:
            sorted_sids = sorted(self.sessions.keys(), key=lambda k: self.sessions[k]["last_active"])
            for sid in sorted_sids[:len(self.sessions) - self.max_sessions]:
                del self.sessions[sid]


session_mgr = SessionManager()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ClearRequest(BaseModel):
    session_id: Optional[str] = None


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI-Agent Bridge | Панель Управления (3080)</title>
    <style>
        :root {
            --bg-base: #0f172a;
            --bg-panel: #1e293b;
            --bg-input: #334155;
            --border: #475569;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-green: #4ade80;
            --accent-amber: #fbbf24;
            --accent-red: #f87171;
            --code-bg: #090d16;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; }
        body { background: var(--bg-base); color: var(--text-main); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
        header { background: var(--bg-panel); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 1.15rem; font-weight: 700; color: var(--accent); display: flex; align-items: center; gap: 8px; }
        .status-bar { display: flex; gap: 16px; font-size: 0.85rem; }
        .badge { display: flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: rgba(255,255,255,0.06); border: 1px solid var(--border); }
        .dot { width: 8px; height: 8px; border-radius: 50%; }
        .dot-green { background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green); }
        .dot-amber { background: var(--accent-amber); }
        .dot-red { background: var(--accent-red); }
        
        main { flex: 1; display: grid; grid-template-columns: 1fr 420px; overflow: hidden; }
        .chat-pane { display: flex; flex-direction: column; height: 100%; border-right: 1px solid var(--border); }
        .messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
        .msg { max-width: 85%; padding: 14px 18px; border-radius: 12px; font-size: 0.95rem; line-height: 1.5; word-wrap: break-word; }
        .msg-user { align-self: flex-end; background: #2563eb; color: #fff; border-bottom-right-radius: 2px; }
        .msg-bot { align-self: flex-start; background: var(--bg-panel); border: 1px solid var(--border); border-bottom-left-radius: 2px; }
        .msg-system { align-self: center; font-size: 0.8rem; color: var(--text-muted); }
        
        .input-bar { padding: 16px 20px; background: var(--bg-panel); border-top: 1px solid var(--border); display: flex; gap: 12px; }
        textarea { flex: 1; background: var(--bg-input); border: 1px solid var(--border); color: #fff; padding: 12px 14px; border-radius: 8px; font-size: 0.95rem; resize: none; height: 50px; outline: none; }
        textarea:focus { border-color: var(--accent); }
        button { background: var(--accent); color: #000; font-weight: 600; border: none; padding: 0 20px; border-radius: 8px; cursor: pointer; transition: 0.15s; }
        button:hover { opacity: 0.9; }
        button:disabled { opacity: 0.4; cursor: not-allowed; }
        .btn-clear { background: transparent; color: var(--text-muted); border: 1px solid var(--border); padding: 0 14px; }
        .btn-clear:hover { background: rgba(255,255,255,0.05); color: #fff; }
        
        .inspector-pane { display: flex; flex-direction: column; background: #0b1120; height: 100%; overflow: hidden; }
        .inspector-hdr { padding: 14px 18px; font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
        .logs { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.8rem; }
        .log-item { background: var(--bg-panel); border-left: 3px solid var(--accent); padding: 8px 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-all; }
        .log-tool { border-left-color: var(--accent-amber); }
        .log-error { border-left-color: var(--accent-red); color: #fca5a5; }
        .log-success { border-left-color: var(--accent-green); }
        pre { background: var(--code-bg); padding: 8px; border-radius: 4px; margin-top: 6px; overflow-x: auto; color: #38bdf8; }
    </style>
</head>
<body>
    <header>
        <div class="logo">
            <span>⚡ AI-Agent Bridge</span>
            <span style="font-size:0.75rem; background:rgba(56,189,248,0.15); color:var(--accent); padding:2px 8px; border-radius:4px;">DSH Core v3.0</span>
        </div>
        <div class="status-bar">
            <div class="badge"><div class="dot dot-green"></div> Сессия: <span id="session-badge" style="font-family:monospace; color:var(--accent);">новая</span></div>
            <div class="badge"><div id="dot-ollama" class="dot dot-amber"></div> Local Ollama (<span id="model-name">qwen2.5-coder:7b</span>)</div>
            <div class="badge"><div id="dot-remote" class="dot dot-amber"></div> Linux Node (192.168.1.100)</div>
            <div class="badge"><div id="dot-db" class="dot dot-green"></div> SQLite Pool (WAL)</div>
        </div>
    </header>
    <main>
        <div class="chat-pane">
            <div class="messages" id="chat-messages">
                <div class="msg msg-system">Готов к работе. Подключено 17 инструментов локальной экосистемы D:\AI_Agent.</div>
            </div>
            <div class="input-bar">
                <textarea id="prompt-input" maxlength="8000" placeholder="Введите задачу или ссылку для анализа... (до 8000 симв., Ctrl+Enter для отправки)" onkeydown="handleKeyDown(event)"></textarea>
                <button id="send-btn" onclick="sendMessage()">Отправить</button>
                <button class="btn-clear" onclick="clearChat()">Сброс</button>
            </div>
        </div>
        <div class="inspector-pane">
            <div class="inspector-hdr">
                <span>Инспектор выполнения (ReAct Trace)</span>
                <span id="step-counter" style="color:var(--accent);">Шагов: 0</span>
            </div>
            <div class="logs" id="logs-container">
                <div class="log-item">Ожидание команд... Инструменты будут отображаться здесь в реальном времени.</div>
            </div>
        </div>
    </main>

    <script>
        let isProcessing = false;
        let totalSteps = 0;
        let currentSessionId = localStorage.getItem("ai_agent_session_id") || "";
        if (currentSessionId) {
            const badge = document.getElementById("session-badge");
            if (badge) badge.innerText = currentSessionId;
        }

        function addMessage(role, text) {
            const container = document.getElementById("chat-messages");
            const div = document.createElement("div");
            div.className = "msg " + (role === "user" ? "msg-user" : "msg-bot");
            div.innerText = text;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }

        function addLog(text, type = "info") {
            const container = document.getElementById("logs-container");
            const div = document.createElement("div");
            div.className = "log-item" + (type === "tool" ? " log-tool" : type === "error" ? " log-error" : type === "success" ? " log-success" : "");
            div.innerText = text;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }

        async function updateStatus() {
            try {
                const res = await fetch("/api/status");
                const data = await res.json();
                document.getElementById("dot-ollama").className = "dot " + (data.ollama_local ? "dot-green" : "dot-red");
                document.getElementById("dot-remote").className = "dot " + (data.linux_node ? "dot-green" : "dot-amber");
            } catch (e) {}
        }
        setInterval(updateStatus, 10000);
        updateStatus();

        function handleKeyDown(e) {
            if (e.key === "Enter" && (e.ctrlKey || !e.shiftKey)) {
                e.preventDefault();
                sendMessage();
            }
        }

        async function sendMessage() {
            if (isProcessing) return;
            const input = document.getElementById("prompt-input");
            const text = input.value.trim();
            if (!text) return;
            if (text.length > 8000) {
                alert("Сообщение превышает лимит в 8000 символов!");
                return;
            }

            input.value = "";
            addMessage("user", text);
            addLog("[⚡ Запрос пользователя]: " + text);

            isProcessing = true;
            document.getElementById("send-btn").disabled = true;
            document.getElementById("send-btn").innerText = "Думаю...";

            try {
                const res = await fetch("/api/chat", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ message: text, session_id: currentSessionId || null })
                });
                if (!res.ok) {
                    const errData = await res.json().catch(() => ({}));
                    throw new Error(errData.detail || ("HTTP " + res.status));
                }
                const data = await res.json();
                if (data.session_id) {
                    currentSessionId = data.session_id;
                    localStorage.setItem("ai_agent_session_id", currentSessionId);
                    const badge = document.getElementById("session-badge");
                    if (badge) badge.innerText = currentSessionId;
                }
                
                if (data.logs && data.logs.length > 0) {
                    data.logs.forEach(l => addLog(l.text, l.type));
                }
                
                totalSteps += (data.steps || 1);
                document.getElementById("step-counter").innerText = "Шагов: " + totalSteps;
                addMessage("bot", data.reply);
                addLog("[✓ Ответ сформирован]", "success");
            } catch (err) {
                addMessage("bot", "[-] Ошибка выполнения: " + err.message);
                addLog("[-] Ошибка соединения: " + err.message, "error");
            } finally {
                isProcessing = false;
                document.getElementById("send-btn").disabled = false;
                document.getElementById("send-btn").innerText = "Отправить";
            }
        }

        async function clearChat() {
            if (isProcessing) return;
            await fetch("/api/clear", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: currentSessionId || null })
            });
            document.getElementById("chat-messages").innerHTML = '<div class="msg msg-system">Сессия ' + (currentSessionId || '') + ' сброшена. Контекст очищен.</div>';
            document.getElementById("logs-container").innerHTML = '<div class="log-item">Память очищена. Готов к новым задачам.</div>';
            totalSteps = 0;
            document.getElementById("step-counter").innerText = "Шагов: 0";
        }
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    return HTMLResponse(content=HTML_TEMPLATE, status_code=200)


@app.get("/api/status")
async def get_status():
    loop = asyncio.get_event_loop()
    local_ok = await loop.run_in_executor(None, primary_provider.is_available)
    remote_ok = await loop.run_in_executor(None, fallback_provider.is_available)
    return {
        "ollama_local": local_ok,
        "linux_node": remote_ok,
        "db_pool": True,
        "tools_count": len(tools_map)
    }


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    user_prompt = req.message.strip()
    if not user_prompt:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым.")
    if len(user_prompt) > 8000:
        raise HTTPException(status_code=400, detail="Сообщение превышает лимит 8000 символов.")

    session_id, messages = session_mgr.get_or_create(req.session_id)
    prev_len = len(messages)
    messages.append({"role": "user", "content": user_prompt})

    logs: List[Dict[str, str]] = []
    
    # Запускаем шаг оркестратора в отдельном потоке, чтобы не блокировать event loop FastAPI
    loop = asyncio.get_event_loop()
    reply = await loop.run_in_executor(None, chat_step, messages, ollama_tools, tools_map)

    # Проверяем только новые сообщения для сбора логов вызова инструментов
    for m in messages[prev_len:]:
        if m.get("role") == "tool":
            tool_name = m.get("name", "tool")
            content_preview = str(m.get("content", ""))[:200]
            logs.append({"type": "tool", "text": f"[🛠️ Вызов инструмента {tool_name}]:\n{content_preview}..."})

    return {
        "session_id": session_id,
        "reply": reply,
        "logs": logs,
        "steps": len(logs) + 1
    }


@app.post("/api/clear")
async def clear_endpoint(req: Optional[ClearRequest] = None):
    sid = req.session_id if req else None
    if sid:
        session_mgr.clear(sid)
    return {"status": "cleared", "session_id": sid}


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("🚀 Запуск AI-Agent Bridge Web-UI на порту http://127.0.0.1:3080")
    print("=" * 60)
    uvicorn.run("web_ui:app", host="127.0.0.1", port=3080, log_level="info")
