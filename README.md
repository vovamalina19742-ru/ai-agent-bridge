# AI Agent Bridge: Hybrid Cloud-Local Multi-Agent Ecosystem

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[![Status: Work in Progress](https://img.shields.io/badge/status-work--in--progress-orange.svg)]()
[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](https://github.com/vovamalina19742-ru/ai-agent-bridge/issues)

> [!NOTE]
> **🚧 Проект в активной разработке (Work in Progress / Alpha)!**
> Это открытый эксперимент по связке облачных планировщиков (Antigravity / Claude / Gemini) с локальным парком моделей (Ollama) и домашним сервером. Некоторые части архитектуры ещё «сырые», активно тестируются и дорабатываются.
> **Приглашаем к доработке и тестированию!** Будем очень рады вашим Issue, предложениям по архитектуре и Pull Request'ам.

A high-performance, autonomous, and secure local AI agent ecosystem. It bridges cloud-based planners (like Claude/Gemini via MCP) with local open-source models (Ollama: Qwen 2.5, DeepSeek R1), executing heavy tasks locally **for 0 API token costs** while keeping your data strictly private.

---

## 🏗 Architecture / Архитектура

```
Cloud Dispatcher (Antigravity / Claude / Gemini) [Платная квота / Планирование]
   │  MCP stdio (JSON-RPC 2.0)
   ▼
bridge_mcp.py  ──►  bridge_queue.db (Транзакционная очередь SQLite: WAL)
   │
   ▼
bridge_worker.py  ──►  Локальные воркеры Ollama (Бесплатные / Выполнение)
   │                   (qwen2.5-coder:7b / deepseek-r1:8b / qwen2.5:3b / 1.5b)
   ▼
Слой инструментов (tools/) ──► SQLite FTS5 книги, веб-скрейпер, сетевой сканер,
                               гибридный RRF-поиск, безопасный Linux SSH-сервер
```

---

## ✨ Key Features / Ключевые возможности

- **Zero-Cost Execution:** Cloud models only handle high-level planning and verification. All iterative coding, web analysis, and search tasks are executed locally by free Ollama models.
- **Transactional SQLite Queue (`bridge_queue.db`)**: Ensures robust async task dispatching (`queued ➔ assigned ➔ done ➔ reviewed`) with zero data loss, atomic `UPDATE ... RETURNING`, and stale task recovery.
- **Model Context Protocol (MCP):** Clean `stdlib`-based MCP server exposing 11 secure tools out-of-the-box over stdio JSON-RPC 2.0.
- **Defensive Routing:** Automatically upgrades complex tasks from lightweight models (1.5b) to heavyweight reasoning/coding models (7b) based on task length and intent markers.
- **Hardened Security (P0/P1 Shield):**
  - Strict command sanitizer (`FORBIDDEN_PATTERNS`) blocking destructive actions (`rm -rf`, `mkfs`, `dd`, etc.) on remote servers.
  - Strict SSL verification and SSH `known_hosts` enforcement.
  - Prompt-injection defenses preventing untrusted web content from executing arbitrary tools.
- **Lightning-Fast Local RAG:** SQLite FTS5 full-text book indexing with Russian `unicode61` tokenizer (<50ms response) and hybrid semantic ranking (Reciprocal Rank Fusion) using local embeddings.

---

## 🚀 Quick Start / Быстрый старт

### 1. Clone & Setup
```bash
git clone https://github.com/your-username/ai-agent-bridge.git
cd ai-agent-bridge
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your local paths and settings
```

### 3. Run Worker
```bash
# Continuous background queue worker
python bridge_worker.py

# Or process a single task for testing
python bridge_worker.py --once
```

### 4. Connect to MCP Client (Antigravity / Claude Desktop)
Add the server configuration to your MCP config (`mcp_config.json`):
```json
{
  "mcpServers": {
    "ai-agent-bridge": {
      "command": "python",
      "args": ["/absolute/path/to/ai-agent-bridge/bridge_mcp.py"]
    }
  }
}
```

---

## 🛠 Included Tools / Инструменты в комплекте

| Tool | Description |
|---|---|
| `task_assign` | Put an asynchronous task into the transactional queue |
| `task_status` | Inspect task state (`queued`, `assigned`, `done`, `failed`) |
| `task_result` | Retrieve summarized task output without context bloat |
| `book_search` | Blazing-fast full-text search across PDF library via SQLite FTS5 |
| `web_fetch` | Web scraper and content extractor with auto-saving metadata |
| `network_scan` | Local subnet scanner with MAC resolution |
| `memory` | Persistent key-value fact store with hybrid semantic recall |
| `server_status` | Check ping, SSH availability and disk space on remote node |
| `server_wake` | Send Wake-on-LAN (WOL) magic packet to remote host |
| `server_sleep` | Send remote host to energy-efficient S3 Suspend (<1W) |
| `server_exec` | Execute guarded bash commands on remote Linux node |
| `status_report` | Unified one-call ecosystem health check (Ollama, DBs, SearXNG, node) |
| `ask_books` | RAG over 424+ books using FTS5 + local Ollama with page citations |
| `backup_manager` | Hot SQLite backup API with rotation & remote Linux node sync |
| `doc_reader` | Universal document extractor for DOCX, XLSX, PPTX, CSV, TXT, HTML |
| `code_check` | Syntax verification, ruff linting, pytest & cargo check |
| `ocr_scans` | High-accuracy OCR for scanned PDFs, blueprints & images (RapidOCR) |
| `night_ops` | Scheduled maintenance chain (health check -> backup -> code check -> report) |

---

## 📄 License
Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
