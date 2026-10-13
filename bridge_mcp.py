# -*- coding: utf-8 -*-
"""
bridge_mcp.py — MCP-сервер проекта AI_Agent (Antigravity -> AI_Agent -> сервер).

Протокол: MCP over stdio (newline-delimited JSON-RPC 2.0). Зависимости: только stdlib.
Запуск: python bridge_mcp.py  (подключается из Antigravity / любого MCP-клиента)

Инструменты:
  task_assign(task, worker_hint?, acceptance?)  — положить задачу в очередь для воркера (Ollama)
  task_status(task_id?)                         — статус одной задачи или последние 10
  task_result(task_id)                          — result_summary + артефакты (сырые данные не тянутся в контекст)
  book_search(query, book_filter?)              — FTS5-поиск по книгам (tools/search_books.py)
  web_fetch(url, mode?)                         — загрузка страницы (tools/web_fetcher.py)
  network_scan(subnet_prefix?)                  — скан локальной сети (tools/network_scanner.py)
  memory(action, topic?, content?)              — долговременная память (tools/memory_manager.py)
  server_status() / server_wake() / server_sleep() — управление Linux-сервером
  server_exec(command)                          — bash на сервере ЧЕРЕЗ черный список (F2)

Важно: stdout используется только протоколом. Логи — в bridge_mcp.log и stderr.
"""
import sys
import os
import json
import sqlite3
import subprocess
import time
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import QUEUE_DB_PATH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
QUEUE_DB = QUEUE_DB_PATH
LOG_FILE = os.path.join(BASE_DIR, "bridge_mcp.log")


# Слитый вывод инструментов обрезается: диспетчер платит квотой за каждый токен в контексте
MAX_TOOL_OUTPUT_CHARS = 4000
MAX_SUMMARY_CHARS = 2000

# Белый список разрешенных структурированных операций для server_exec (Allowlist Matrix)
ALLOWED_SERVER_OPERATIONS = {
    "get_disk_space", "docker_ps", "ram_info", "uptime_info",
    "check_service_status", "restart_service", "tail_logs"
}


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, file=sys.stderr, flush=True)


def truncate(text, limit=MAX_TOOL_OUTPUT_CHARS):
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... вывод обрезан: показано {limit} из {len(text)} символов ...]"


def ensure_db():
    con = sqlite3.connect(QUEUE_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, updated_at TEXT,
            task TEXT NOT NULL,
            worker_hint TEXT DEFAULT '',
            acceptance TEXT DEFAULT '',
            status TEXT DEFAULT 'queued',
            result_summary TEXT DEFAULT '',
            artifacts TEXT DEFAULT '',
            error TEXT DEFAULT ''
        )
    """)
    con.commit()
    con.close()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- Реализации инструментов ----------------

def run_tool_script(filename, cli_args, timeout=120):
    """Запускает скрипт из tools/ и возвращает обрезанный вывод."""
    path = os.path.join(TOOLS_DIR, filename)
    if not os.path.exists(path):
        return f"[!] Инструмент не найден: {filename}"
    cmd = [sys.executable, path] + [str(a) for a in cli_args if str(a).strip() != ""]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, cwd=BASE_DIR,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if out:
            return truncate(out)
        if err:
            return truncate("[stderr]\n" + err)
        return "[Инструмент завершился без вывода]"
    except subprocess.TimeoutExpired:
        return f"[!] Таймаут {timeout}с при выполнении {filename}"
    except Exception as e:
        return f"[!] Ошибка запуска {filename}: {e}"


def tool_task_assign(args):
    task = (args.get("task") or "").strip()
    if not task:
        return _text_result("[!] Обязателен параметр task", is_error=True)
    con = sqlite3.connect(QUEUE_DB)
    cur = con.execute(
        "INSERT INTO tasks(created_at, updated_at, task, worker_hint, acceptance, status) "
        "VALUES (?,?,?,?,?, 'queued')",
        (now_str(), now_str(), task, args.get("worker_hint", ""), args.get("acceptance", "")),
    )
    tid = cur.lastrowid
    con.commit()
    con.close()
    log(f"task_assign #{tid}: {task[:80]}")
    return _text_result(
        f"Задача #{tid} принята в очередь (status=queued).\n"
        f"Подсказка воркеру: {args.get('worker_hint') or '(не задана)'}\n"
        f"Отслеживание: task_status, результат: task_result."
    )


def tool_task_status(args):
    tid = args.get("task_id")
    con = sqlite3.connect(QUEUE_DB)
    con.row_factory = sqlite3.Row
    if tid:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (int(tid),)).fetchone()
        con.close()
        if not row:
            return _text_result(f"[!] Задача #{tid} не найдена", is_error=True)
        return _text_result(
            f"Задача #{row['id']} [{row['status']}]\n"
            f"Создана: {row['created_at']} | Обновлена: {row['updated_at']}\n"
            f"Задача: {row['task'][:200]}\n"
            f"Hint: {row['worker_hint'] or '-'} | Критерий: {row['acceptance'] or '-'}"
        )
    rows = con.execute("SELECT id, status, created_at, task FROM tasks ORDER BY id DESC LIMIT 10").fetchall()
    con.close()
    if not rows:
        return _text_result("Очередь пуста.")
    lines = [f"{'ID':<5} {'Статус':<10} {'Создана':<17} Задача", "-" * 78]
    for r in rows:
        lines.append(f"{r['id']:<5} {r['status']:<10} {r['created_at']:<17} {r['task'][:55]}")
    return _text_result("\n".join(lines))


def tool_task_result(args):
    tid = args.get("task_id")
    if not tid:
        return _text_result("[!] Обязателен параметр task_id", is_error=True)
    con = sqlite3.connect(QUEUE_DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM tasks WHERE id=?", (int(tid),)).fetchone()
    con.close()
    if not row:
        return _text_result(f"[!] Задача #{tid} не найдена", is_error=True)
    status = row["status"]
    if status in ("queued", "assigned"):
        return _text_result(f"Задача #{tid} ещё в работе (status={status}).")
    parts = [f"Задача #{tid} [{status}]"]
    if row["error"]:
        parts.append(f"Ошибка: {row['error']}")
    parts.append(f"Выжимка результата:\n{row['result_summary'] or '(пусто)'}")
    if row["artifacts"]:
        parts.append(f"Артефакты (полные данные в файлах): {row['artifacts']}")
    return _text_result(truncate("\n".join(parts), MAX_SUMMARY_CHARS))


def tool_book_search(args):
    query = (args.get("query") or "").strip()
    if not query:
        return _text_result("[!] Обязателен параметр query", is_error=True)
    cli = [query]
    if args.get("book_filter"):
        cli.append(args["book_filter"])
    return _text_result(run_tool_script("search_books.py", cli, timeout=120))


def tool_web_fetch(args):
    url = (args.get("url") or "").strip()
    if not url:
        return _text_result("[!] Обязателен параметр url", is_error=True)
    cli = [url]
    if args.get("mode") == "status":
        cli.append("status")
    return _text_result(run_tool_script("web_fetcher.py", cli, timeout=60))


def tool_network_scan(args):
    prefix = args.get("subnet_prefix") or "192.168.1"
    return _text_result(run_tool_script("network_scanner.py", [prefix], timeout=180))


def tool_memory(args):
    action = args.get("action", "recall")
    cli = [action]
    if args.get("topic"):
        cli.append(args["topic"])
    if action == "save" and args.get("content"):
        cli.append(args["content"])
    return _text_result(run_tool_script("memory_manager.py", cli, timeout=30))


def tool_server_exec(args):
    operation = (args.get("operation") or args.get("command") or "").strip().lower()
    target = (args.get("target") or "").strip()
    if not operation:
        return _text_result(
            f"[!] Укажите разрешенную операцию. Доступны: {', '.join(sorted(ALLOWED_SERVER_OPERATIONS))}",
            is_error=True,
        )

    parts = operation.split()
    if len(parts) > 1:
        operation = parts[0]
        if not target:
            target = parts[1]

    if operation not in ALLOWED_SERVER_OPERATIONS:
        log(f"REJECTED server_exec: operation={operation}")
        return _text_result(
            f"[!] Операция '{operation}' отклонена политикой безопасности (Allowlist).\n"
            f"Произвольные bash-команды запрещены. Разрешены только: {', '.join(sorted(ALLOWED_SERVER_OPERATIONS))}",
            is_error=True,
        )

    cli = [operation]
    if target:
        cli.extend(["--target", target])
    return _text_result(run_tool_script("remote_server.py", cli, timeout=30))


def tool_server_status(args):
    return _text_result(run_tool_script("remote_server.py", ["status"], timeout=30))


def tool_server_wake(args):
    return _text_result(run_tool_script("remote_server.py", ["wake"], timeout=30))


def tool_server_sleep(args):
    return _text_result(run_tool_script("remote_server.py", ["sleep"], timeout=30))


def tool_status_report(args):
    return _text_result(run_tool_script("status_report.py", [], timeout=15))


def tool_ask_books(args):
    question = (args.get("question") or "").strip()
    if not question:
        return _text_result("[!] Обязателен параметр question", is_error=True)
    cli = [question]
    if args.get("book_filter"):
        cli.extend(["--book", args["book_filter"]])
    return _text_result(run_tool_script("ask_books.py", cli, timeout=90))


def tool_backup_manager(args):
    cli = []
    if args.get("upload_to_server") is False:
        cli.append("--no-server")
    if args.get("keep_last"):
        cli.extend(["--keep", str(args["keep_last"])])
    return _text_result(run_tool_script("backup_manager.py", cli, timeout=40))


def tool_doc_reader(args):
    file_path = (args.get("file_path") or "").strip()
    if not file_path:
        return _text_result("[!] Обязателен параметр file_path", is_error=True)
    cli = [file_path]
    if args.get("max_lines"):
        cli.extend(["--lines", str(args["max_lines"])])
    return _text_result(run_tool_script("doc_reader.py", cli, timeout=30))


def tool_code_check(args):
    target_path = (args.get("target_path") or "").strip()
    if not target_path:
        return _text_result("[!] Обязателен параметр target_path", is_error=True)
    cli = [target_path]
    if args.get("run_tests"):
        cli.append("--test")
    if args.get("fix_lint"):
        cli.append("--fix")
    return _text_result(run_tool_script("code_check.py", cli, timeout=45))


def tool_auto_refactor(args):
    target = (args.get("target_file") or "").strip()
    if not target:
        return _text_result("[!] Обязателен параметр target_file", is_error=True)
    cli = [target]
    if args.get("instruction"):
        cli.extend(["--instruction", str(args["instruction"])])
    if args.get("max_attempts"):
        cli.extend(["--attempts", str(args["max_attempts"])])
    return _text_result(run_tool_script("auto_refactor.py", cli, timeout=180))


def tool_pipeline_bom(args):
    file_path = (args.get("file_path") or "").strip()
    if not file_path:
        return _text_result("[!] Обязателен параметр file_path", is_error=True)
    cli = [file_path]
    if args.get("reserve_percent"):
        cli.extend(["--reserve", str(args["reserve_percent"])])
    if args.get("output_csv"):
        cli.extend(["--csv", str(args["output_csv"])])
    return _text_result(run_tool_script("pipeline_bom.py", cli, timeout=60))


def tool_ocr_scans(args):
    file_path = (args.get("file_path") or "").strip()
    if not file_path:
        return _text_result("[!] Обязателен параметр file_path", is_error=True)
    cli = [file_path]
    if args.get("page_num"):
        cli.extend(["--page", str(args["page_num"])])
    if args.get("save_to_fts"):
        cli.append("--save")
    return _text_result(run_tool_script("ocr_scans.py", cli, timeout=60))


def tool_night_ops(args):
    cli = []
    if args.get("skip_server"):
        cli.append("--no-server")
    return _text_result(run_tool_script("night_ops.py", cli, timeout=60))


def _text_result(text, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


# ---------------- Реестр MCP-инструментов ----------------

def tool_def(name, description, properties, required=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


TOOLS = {
    "task_assign": (
        tool_task_assign,
        tool_def(
            "task_assign",
            "Положить задачу в очередь для локального воркера (Ollama, бесплатно). "
            "Использовать для итеративной исполнительной работы: анализ страниц, сканы, поиск по книгам, "
            "правки кода по готовому ТЗ. НЕ тянуть сырые данные в свой контекст — воркер вернет result_summary.",
            {
                "task": {"type": "string", "description": "Полная постановка задачи для воркера"},
                "worker_hint": {"type": "string", "description": "Подсказка: 'coder' (qwen2.5-coder:7b, код), '3b'/'summary' (выжимки), 'reason' (deepseek-r1:8b, рассуждения), 'fast' (1.5b)"},
                "acceptance": {"type": "string", "description": "Критерий приемки результата"},
            },
            ["task"],
        ),
    ),
    "task_status": (
        tool_task_status,
        tool_def(
            "task_status",
            "Статус задачи в очереди (или последние 10 задач, если task_id не указан).",
            {"task_id": {"type": "integer", "description": "ID задачи (опционально)"}},
        ),
    ),
    "task_result": (
        tool_task_result,
        tool_def(
            "task_result",
            "Получить result_summary выполненной задачи и пути к артефактам. "
            "Читай только выжимку — полные данные лежат в файлах артефактов.",
            {"task_id": {"type": "integer", "description": "ID задачи"}},
            ["task_id"],
        ),
    ),
    "book_search": (
        tool_book_search,
        tool_def(
            "book_search",
            "Быстрый FTS5-полнотекстовый поиск по индексу книг (SQLite). Возвращает книгу, страницу и фрагмент.",
            {
                "query": {"type": "string", "description": "Поисковый запрос, слова через пробел"},
                "book_filter": {"type": "string", "description": "Фильтр по имени книги (опционально)"},
            },
            ["query"],
        ),
    ),
    "web_fetch": (
        tool_web_fetch,
        tool_def(
            "web_fetch",
            "Загрузить веб-страницу и извлечь чистый текст (mode='text') или только HTTP-статус (mode='status').",
            {
                "url": {"type": "string", "description": "Полный URL"},
                "mode": {"type": "string", "description": "'text' или 'status'"},
            },
            ["url"],
        ),
    ),
    "network_scan": (
        tool_network_scan,
        tool_def(
            "network_scan",
            "Сканирование локальной подсети: активные IP + MAC из ARP-таблицы.",
            {"subnet_prefix": {"type": "string", "description": "Префикс подсети, по умолчанию 192.168.1"}},
        ),
    ),
    "memory": (
        tool_memory,
        tool_def(
            "memory",
            "Долговременная память агента: action='save' (topic+content), 'recall' (topic), 'list'.",
            {
                "action": {"type": "string", "description": "save | recall | list"},
                "topic": {"type": "string", "description": "Тема/ключевое слово"},
                "content": {"type": "string", "description": "Текст факта (для save)"},
            },
            ["action"],
        ),
    ),
    "server_status": (
        tool_server_status,
        tool_def("server_status", "Проверить Linux-сервер 192.168.1.100: пинг, SSH, RAM, диск.", {}),
    ),
    "server_wake": (
        tool_server_wake,
        tool_def("server_wake", "Разбудить Linux-сервер через Wake-on-LAN.", {}),
    ),
    "server_sleep": (
        tool_server_sleep,
        tool_def("server_sleep", "Отправить Linux-сервер в спящий режим S3.", {}),
    ),
    "server_exec": (
        tool_server_exec,
        tool_def(
            "server_exec",
            "Выполнить безопасную операцию на Linux-сервере через строгий белый список (Allowlist). Доступные операции: get_disk_space, docker_ps, ram_info, uptime_info, check_service_status, restart_service, tail_logs.",
            {
                "operation": {
                    "type": "string",
                    "description": "Разрешенная операция: get_disk_space, docker_ps, ram_info, uptime_info, check_service_status, restart_service, tail_logs",
                },
                "target": {
                    "type": "string",
                    "description": "Имя службы для check_service_status, restart_service, tail_logs (например: searxng, casaos, ollama, docker)",
                },
            },
            ["operation"],
        ),
    ),
    "status_report": (
        tool_status_report,
        tool_def(
            "status_report",
            "Единая экспресс-самодиагностика экосистемы: Ollama, очередь задач, размеры БД, сервер Linux и SearXNG.",
            {},
        ),
    ),
    "ask_books": (
        tool_ask_books,
        tool_def(
            "ask_books",
            "Спроси библиотеку: RAG-ответ по книгам с точными цитатами [Книга, стр. N] через локальную Ollama (0 токенов).",
            {
                "question": {"type": "string", "description": "Вопрос по технической теме"},
                "book_filter": {"type": "string", "description": "Фильтр по названию книги (опционально)"},
            },
            ["question"],
        ),
    ),
    "backup_manager": (
        tool_backup_manager,
        tool_def(
            "backup_manager",
            "Создать горячий SQLite-снапшот баз (очередь, книги) с ротацией и отправкой на Linux-сервер.",
            {
                "upload_to_server": {"type": "boolean", "description": "Синхронизировать на сервер (по умолчанию true)"},
                "keep_last": {"type": "integer", "description": "Хранить последних копий локально (по умолчанию 5)"},
            },
        ),
    ),
    "doc_reader": (
        tool_doc_reader,
        tool_def(
            "doc_reader",
            "Универсальное извлечение чистого текста из DOCX, XLSX, PPTX, CSV, TXT, MD, HTML.",
            {
                "file_path": {"type": "string", "description": "Путь к файлу"},
                "max_lines": {"type": "integer", "description": "Максимум строк (по умолчанию 150)"},
            },
            ["file_path"],
        ),
    ),
    "code_check": (
        tool_code_check,
        tool_def(
            "code_check",
            "Автопроверка синтаксиса, линтера (ruff) и тестов (pytest/cargo) для валидации правок кода.",
            {
                "target_path": {"type": "string", "description": "Путь к файлу или папке"},
                "run_tests": {"type": "boolean", "description": "Запустить pytest/cargo test (по умолчанию false)"},
                "fix_lint": {"type": "boolean", "description": "Автофикс через ruff (по умолчанию false)"},
            },
            ["target_path"],
        ),
    ),
    "auto_refactor": (
        tool_auto_refactor,
        tool_def(
            "auto_refactor",
            "Автономный цикл самоисправления кода (Self-Healing Code Loop): находит синтаксические ошибки, исправляет через qwen2.5-coder, валидирует себя и возвращает unified diff.",
            {
                "target_file": {"type": "string", "description": "Путь к исходному файлу"},
                "instruction": {"type": "string", "description": "Дополнительная инструкция по исправлению"},
                "max_attempts": {"type": "integer", "description": "Лимит попыток (по умолчанию 3)"},
            },
            ["target_file"],
        ),
    ),
    "pipeline_bom": (
        tool_pipeline_bom,
        tool_def(
            "pipeline_bom",
            "Автоматический конвейер Blueprint-to-BOM (Minimal CAD Takeoff): парсит чертежи PDF или спецификации, сопоставляет оборудование с каталогами (TOPMET, KRAAB, Neko) и формирует минималистичную спецификацию (№, Наименование, Кол-во).",
            {
                "file_path": {"type": "string", "description": "Путь к файлу чертежа (PDF) или спецификации"},
                "reserve_percent": {"type": "number", "description": "Технологический запас на раскрой лент и профилей в % (по умолчанию 5%)"},
                "output_csv": {"type": "string", "description": "Путь для сохранения CSV (опционально)"},
            },
            ["file_path"],
        ),
    ),
    "ocr_scans": (
        tool_ocr_scans,
        tool_def(
            "ocr_scans",
            "Распознавание текста (OCR) со сканированных страниц PDF, чертежей и картинок (RapidOCR).",
            {
                "file_path": {"type": "string", "description": "Путь к файлу"},
                "page_num": {"type": "integer", "description": "Номер страницы (по умолчанию 1)"},
                "save_to_fts": {"type": "boolean", "description": "Дописать в индекс FTS5 (по умолчанию false)"},
            },
            ["file_path"],
        ),
    ),
    "night_ops": (
        tool_night_ops,
        tool_def(
            "night_ops",
            "Регламентная ночная смена: цепочка (диагностика -> бэкап на Linux-сервер -> проверка кода -> отчет).",
            {
                "skip_server": {"type": "boolean", "description": "Пропустить отправку на сервер (по умолчанию false)"},
            },
        ),
    ),
}


# ---------------- MCP over stdio ----------------

def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_call(name, arguments):
    if name not in TOOLS:
        return _text_result(f"[!] Неизвестный инструмент: {name}", is_error=True)
    handler, _ = TOOLS[name]
    try:
        return handler(arguments or {})
    except Exception as e:
        log(f"Ошибка в {name}: {e!r}")
        return _text_result(f"[!] Внутренняя ошибка инструмента {name}: {e}", is_error=True)


def main():
    ensure_db()
    log("bridge_mcp запущен (stdio)")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method", "")
        mid = msg.get("id")
        try:
            if method == "initialize":
                params = msg.get("params") or {}
                send({
                    "jsonrpc": "2.0", "id": mid,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "ai-agent-bridge", "version": "1.0.0"},
                    },
                })
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                send({
                    "jsonrpc": "2.0", "id": mid,
                    "result": {"tools": [spec for _, spec in TOOLS.values()]},
                })
            elif method == "tools/call":
                params = msg.get("params") or {}
                result = handle_call(params.get("name"), params.get("arguments"))
                send({"jsonrpc": "2.0", "id": mid, "result": result})
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            elif mid is not None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32601, "message": f"Метод не найден: {method}"}})
        except Exception as e:
            log(f"Ошибка обработки {method}: {e!r}")
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32603, "message": f"Внутренняя ошибка: {e}"}})
    log("bridge_mcp остановлен (stdin закрыт)")


if __name__ == "__main__":
    main()
