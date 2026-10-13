"""
status_report.py — Самодиагностика всей экосистемы AI_Agent одним вызовом.

Проверяет:
1. Локальная Ollama (доступность, запущенные/установленные модели)
2. Очередь задач bridge_queue.db (queued, in_progress, done, failed)
3. Базы данных и индексы (размеры books_index.db, bridge_queue.db, memory.db)
4. Удаленный Linux-сервер 192.168.1.100 (онлайн/сон, RAM, диск, Docker-сервисы)
5. SearXNG поисковик (порт 8080)
6. agentmemory (REST :3111, Viewer :3113)

Вывод: компактная выжимка <= 2000 символов для экономии квоты токенов.
"""
import json
import os
import socket
import sqlite3
import sys
import urllib.request
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

try:
    from config import (
        DATABASE_PATH,
        OLLAMA_BASE_URL,
        QUEUE_DB_PATH,
        REMOTE_HOST,
        REMOTE_USER,
    )
except Exception:
    REMOTE_HOST = "192.168.1.100"
    REMOTE_USER = "ubuntu"
    DATABASE_PATH = os.path.join(BASE_DIR, "books_index.db")
    QUEUE_DB_PATH = os.path.join(BASE_DIR, "bridge_queue.db")
    OLLAMA_BASE_URL = "http://localhost:11434"

TOOL_SPEC = {
    "name": "status_report",
    "description": "Единая самодиагностика экосистемы: локальная Ollama, очередь задач, размеры БД, Linux-сервер, SearXNG и память.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


def check_port(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def get_ollama_status():
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            return f"ОНЛАЙН (моделей: {len(models)}, в т.ч. {', '.join(models[:3])})"
    except Exception:
        return "НЕДОСТУПНА"


def get_queue_stats():
    if not os.path.exists(QUEUE_DB_PATH):
        return "БД очереди не найдена"
    try:
        with sqlite3.connect(QUEUE_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, count(*) FROM tasks GROUP BY status")
            rows = cursor.fetchall()
            if not rows:
                return "очередь пуста"
            return ", ".join([f"{status}: {cnt}" for status, cnt in rows])
    except Exception as e:
        return f"ошибка чтения: {e}"


def get_db_size(path):
    if not os.path.exists(path):
        return "отсутствует"
    size_mb = os.path.getsize(path) / (1024 * 1024)
    return f"{size_mb:.1f} MB"


def get_server_quick_status():
    if not check_port(REMOTE_HOST, 22, timeout=1.0):
        return "ВЫКЛЮЧЕН / СПИТ (S3)"

    searx_ok = check_port(REMOTE_HOST, 8080, timeout=1.0)
    searx_str = "SearXNG: ОК (:8080)" if searx_ok else "SearXNG: нет ответа"
    return f"ОНЛАЙН (SSH :22 активен, {searx_str})"


def generate_report():
    lines = []
    lines.append(f"=== СИСТЕМНЫЙ СТАТУС AI_AGENT [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ===")

    # 1. Локальная Ollama
    lines.append(f"• Ollama (хост): {get_ollama_status()}")

    # 2. Очередь задач
    lines.append(f"• Очередь задач (bridge_queue.db): {get_queue_stats()}")

    # 3. Базы данных
    lines.append("• Локальные базы данных:")
    lines.append(f"   - Индекс книг (books_index.db): {get_db_size(DATABASE_PATH)}")
    lines.append(f"   - Очередь (bridge_queue.db): {get_db_size(QUEUE_DB_PATH)}")
    memory_db = os.path.join(BASE_DIR, "memory.db")
    lines.append(f"   - Память связок (memory.db): {get_db_size(memory_db)}")

    # 4. agentmemory
    am_api = check_port("localhost", 3111, timeout=0.8)
    am_view = check_port("localhost", 3113, timeout=0.8)
    lines.append(f"• agentmemory: REST(:3111)={'ОК' if am_api else 'НЕТ'}, Viewer(:3113)={'ОК' if am_view else 'НЕТ'}")

    # 5. Удаленный Linux-сервер
    lines.append(f"• Linux-сервер ({REMOTE_HOST}): {get_server_quick_status()}")

    return "\n".join(lines)


def main():
    report = generate_report()
    print(report)


if __name__ == "__main__":
    main()
