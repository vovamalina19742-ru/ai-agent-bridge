# -*- coding: utf-8 -*-
"""
backup_manager.py — Автоматическое резервное копирование баз данных и памяти экосистемы AI_Agent.

Возможности:
1. Создание консистентных «горячих» снапшотов баз SQLite через online backup API:
   - bridge_queue.db (очередь задач)
   - books_index.db (индекс книг)
   - agentmemory SQLite (если доступна)
2. Локальная ротация архивов (хранение N последних копий в D:\\AI_Agent\\backups).
3. Опциональная синхронизация снапшотов на Linux-сервер 192.168.1.100 (в /home/ubuntu/backups) через SCP/SSH.
"""
import os
import sys
import time
import shutil
import sqlite3
import argparse
import subprocess
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

try:
    from config import REMOTE_HOST, REMOTE_USER, DATABASE_PATH, QUEUE_DB_PATH
except Exception:
    REMOTE_HOST = "192.168.1.100"
    REMOTE_USER = "ubuntu"
    DATABASE_PATH = os.path.join(BASE_DIR, "books_index.db")
    QUEUE_DB_PATH = os.path.join(BASE_DIR, "bridge_queue.db")

BACKUP_LOCAL_DIR = os.path.join(BASE_DIR, "backups")

TOOL_SPEC = {
    "name": "backup_manager",
    "description": "Создание резервных копий баз данных AI_Agent (SQLite online backup) с ротацией и отправкой на Linux-сервер.",
    "parameters": {
        "type": "object",
        "properties": {
            "upload_to_server": {
                "type": "boolean",
                "description": "Отправить созданный снапшот на удаленный Linux-сервер (по умолчанию: true)",
            },
            "keep_last": {
                "type": "integer",
                "description": "Сколько последних бэкапов хранить локально (по умолчанию: 5)",
            },
        },
    },
}


def backup_sqlite(source_path, target_path):
    """Выполняет безопасный онлайн-снапшот SQLite базы (без блокировки пишущих процессов)."""
    if not os.path.exists(source_path):
        return False
    try:
        src = sqlite3.connect(source_path)
        dst = sqlite3.connect(target_path)
        with dst:
            src.backup(dst, pages=100)
        dst.close()
        src.close()
        return True
    except Exception as e:
        sys.stderr.write(f"[!] Ошибка бэкапа {source_path}: {e}\n")
        return False


def rotate_backups(keep_last=5):
    """Удаляет старые бэкапы, превышающие лимит keep_last."""
    if not os.path.exists(BACKUP_LOCAL_DIR):
        return
    folders = [
        os.path.join(BACKUP_LOCAL_DIR, f)
        for f in os.listdir(BACKUP_LOCAL_DIR)
        if os.path.isdir(os.path.join(BACKUP_LOCAL_DIR, f)) and f.startswith("backup_")
    ]
    folders.sort(key=os.path.getmtime)
    while len(folders) > keep_last:
        oldest = folders.pop(0)
        try:
            shutil.rmtree(oldest)
        except Exception:
            pass


def create_backup(upload_to_server=True, keep_last=5):
    os.makedirs(BACKUP_LOCAL_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_dir = os.path.join(BACKUP_LOCAL_DIR, f"backup_{ts}")
    os.makedirs(snapshot_dir, exist_ok=True)

    backed_up = []
    
    # 1. bridge_queue.db
    q_dst = os.path.join(snapshot_dir, "bridge_queue.db")
    if backup_sqlite(QUEUE_DB_PATH, q_dst):
        backed_up.append("bridge_queue.db")

    # 2. books_index.db
    b_dst = os.path.join(snapshot_dir, "books_index.db")
    if backup_sqlite(DATABASE_PATH, b_dst):
        backed_up.append("books_index.db")

    # 3. memory.db (если существует)
    mem_path = os.path.join(BASE_DIR, "memory.db")
    if os.path.exists(mem_path):
        m_dst = os.path.join(snapshot_dir, "memory.db")
        if backup_sqlite(mem_path, m_dst):
            backed_up.append("memory.db")

    rotate_backups(keep_last)

    server_status = "сервер не задействован"
    if upload_to_server and backed_up:
        # Проверяем доступность SSH сервера
        scp_cmd = [
            "scp", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
            "-o", "PasswordAuthentication=no", "-o", "BatchMode=yes",
            "-r", snapshot_dir, f"{REMOTE_USER}@{REMOTE_HOST}:~/backups/"
        ]
        try:
            # Создаем папку на сервере
            subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
                 "-o", "PasswordAuthentication=no", "-o", "BatchMode=yes",
                 f"{REMOTE_USER}@{REMOTE_HOST}", "mkdir -p ~/backups"],
                timeout=8, capture_output=True, text=True
            )
            res = subprocess.run(scp_cmd, timeout=30, capture_output=True, text=True)
            if res.returncode == 0:
                server_status = f"успешно синхронизирован на {REMOTE_HOST}:~/backups/"
            else:
                server_status = f"ошибка SCP: {res.stderr.strip()[:100]}"
        except Exception as e:
            server_status = f"сбой сети сервера: {e}"

    res_lines = [
        f"[+] Снапшот создан: backup_{ts}",
        f"• Локально сохранено: {', '.join(backed_up) if backed_up else 'нет баз'}",
        f"• Путь: {snapshot_dir}",
        f"• Сервер: {server_status}"
    ]
    return "\n".join(res_lines)


def main():
    parser = argparse.ArgumentParser(description="Бэкап баз данных AI_Agent")
    parser.add_argument("--no-server", action="store_true", help="Не отправлять на Linux-сервер")
    parser.add_argument("--keep", type=int, default=5, help="Хранить последних локальных копий")
    args = parser.parse_args()

    out = create_backup(upload_to_server=not args.no_server, keep_last=args.keep)
    print(out)


if __name__ == "__main__":
    main()
