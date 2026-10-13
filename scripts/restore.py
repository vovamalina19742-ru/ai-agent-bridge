# -*- coding: utf-8 -*-
"""
restore.py — Автономная утилита безопасного восстановления баз данных из резервных копий.

Регламент восстановления:
1. Выбор и верификация резервной копии.
2. Проверка целостности всех файлов снапшота (PRAGMA integrity_check) ДО внесения изменений.
3. Создание аварийной точки отката (pre_restore_<ts>) текущих рабочих баз перед заменой.
4. Консистентное восстановление через SQLite Online Backup API.
5. Финальная проверка целостности восстановленных рабочих баз.
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime
from typing import List, Dict, Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import DATABASE_PATH, QUEUE_DB_PATH

BACKUPS_DIR = os.path.join(BASE_DIR, "backups")

TARGET_MAPPING = {
    "bridge_queue.db": QUEUE_DB_PATH,
    "books_index.db": DATABASE_PATH,
    "memory.db": os.path.join(BASE_DIR, "memory.db"),
}


def list_available_backups() -> List[Dict[str, Any]]:
    """Возвращает список всех доступных локальных снапшотов."""
    if not os.path.exists(BACKUPS_DIR):
        return []

    items = []
    for name in sorted(os.listdir(BACKUPS_DIR), reverse=True):
        full_path = os.path.join(BACKUPS_DIR, name)
        if os.path.isdir(full_path) and (name.startswith("backup_") or name.startswith("pre_restore_")):
            dbs = [f for f in os.listdir(full_path) if f.endswith(".db")]
            total_size_mb = sum(os.path.getsize(os.path.join(full_path, f)) for f in dbs) / (1024.0 * 1024.0)
            items.append({
                "name": name,
                "path": full_path,
                "dbs": dbs,
                "size_mb": total_size_mb,
                "mtime": datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%Y-%m-%d %H:%M:%S")
            })
    return items


def verify_db_integrity(db_path: str) -> bool:
    """Проверяет целостность базы через PRAGMA integrity_check."""
    if not os.path.exists(db_path):
        return False
    try:
        con = sqlite3.connect(db_path)
        cur = con.execute("PRAGMA integrity_check;")
        row = cur.fetchone()
        con.close()
        return bool(row and str(row[0]).lower() == "ok")
    except Exception:
        return False


def restore_snapshot(snapshot_name_or_path: str, skip_safety_backup: bool = False) -> bool:
    """Выполняет безопасное восстановление баз данных из указанного снапшота."""
    # Определяем путь к каталогу бэкапа
    if os.path.isabs(snapshot_name_or_path) and os.path.isdir(snapshot_name_or_path):
        src_dir = snapshot_name_or_path
    else:
        src_dir = os.path.join(BACKUPS_DIR, snapshot_name_or_path)

    if not os.path.exists(src_dir):
        print(f"[-] Ошибка: Каталог резервной копии '{src_dir}' не найден.")
        return False

    backup_dbs = [f for f in os.listdir(src_dir) if f.endswith(".db")]
    if not backup_dbs:
        print(f"[-] Ошибка: В каталоге '{os.path.basename(src_dir)}' не найдено файлов баз данных (*.db).")
        return False

    print("=" * 65)
    print(f"  ВОССТАНОВЛЕНИЕ БАЗ ДАННЫХ ИЗ СНАПШОТА: {os.path.basename(src_dir)}")
    print("=" * 65)

    # 1. Проверка целостности всех файлов в резервной копии ДО восстановления
    print("[1/4] Валидация целостности резервной копии (PRAGMA integrity_check)...")
    for db_name in backup_dbs:
        db_path = os.path.join(src_dir, db_name)
        if not verify_db_integrity(db_path):
            print(f" [!] КРИТИЧЕСКИЙ СБОЙ: Файл '{db_name}' в резервной копии поврежден!")
            print(" [!] Операция восстановления немедленно прервана. Рабочие базы не затронуты.")
            return False
        print(f"  ✓ {db_name}: целостность подтверждена (OK)")

    # 2. Создание аварийной точки отката (Safety Snapshot) текущего состояния
    if not skip_safety_backup:
        print("\n[2/4] Создание точки отката текущих рабочих баз (Safety Snapshot)...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_dir = os.path.join(BACKUPS_DIR, f"pre_restore_{ts}")
        os.makedirs(safety_dir, exist_ok=True)
        saved_count = 0
        for db_name, target_path in TARGET_MAPPING.items():
            if os.path.exists(target_path):
                dst = os.path.join(safety_dir, db_name)
                try:
                    src_conn = sqlite3.connect(target_path)
                    dst_conn = sqlite3.connect(dst)
                    with dst_conn:
                        src_conn.backup(dst_conn, pages=100)
                    dst_conn.close()
                    src_conn.close()
                    saved_count += 1
                    print(f"  + Сохранено текущее состояние {db_name} -> {os.path.basename(safety_dir)}")
                except Exception as e:
                    print(f"  ! Предупреждение: не удалось снять слепок {db_name}: {e}")

    # 3. Восстановление через SQLite Online Backup API
    print("\n[3/4] Восстановление данных в рабочие пути...")
    restored = []
    for db_name in backup_dbs:
        if db_name not in TARGET_MAPPING:
            print(f"  - Пропуск {db_name} (не зарегистрирован в TARGET_MAPPING)")
            continue

        target_path = TARGET_MAPPING[db_name]
        src_path = os.path.join(src_dir, db_name)

        try:
            # Гарантируем каталог назначения
            os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
            # Выполняем атомарный перенос через backup API
            src_conn = sqlite3.connect(src_path)
            dst_conn = sqlite3.connect(target_path)
            with dst_conn:
                src_conn.backup(dst_conn, pages=100)
            dst_conn.close()
            src_conn.close()
            restored.append(db_name)
            print(f"  ✓ Восстановлен: {db_name} -> {target_path}")
        except Exception as e:
            print(f"  ! Ошибка восстановления {db_name}: {e}")
            return False

    # 4. Финальная проверка восстановленных файлов
    print("\n[4/4] Финальная проверка восстановленной системы...")
    all_ok = True
    for db_name in restored:
        target_path = TARGET_MAPPING[db_name]
        if verify_db_integrity(target_path):
            print(f"  ✓ {db_name}: активная база проверена и готова к работе (OK)")
        else:
            print(f"  ! ОШИБКА: {db_name} после восстановления не прошла проверку!")
            all_ok = False

    print("-" * 65)
    if all_ok:
        print("[✓] Восстановление системы завершено успешно и безопасно!")
    else:
        print("[!] Внимание: Восстановление завершено с предупреждениями.")
    print("=" * 65)
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Безопасное восстановление баз данных AI_Agent")
    parser.add_argument("snapshot", nargs="?", default=None, help="Имя или путь к папке снапшота (например: backup_20261013_120000)")
    parser.add_argument("--list", "-l", action="store_true", help="Показать список доступных резервных копий")
    parser.add_argument("--no-safety", action="store_true", help="Не создавать точку отката перед восстановлением")
    args = parser.parse_args()

    if args.list or not args.snapshot:
        backups = list_available_backups()
        if not backups:
            print("[-] В каталоге 'backups/' нет доступных резервных копий.")
            return

        print("=" * 70)
        print("  ДОСТУПНЫЕ РЕЗЕРВНЫЕ КОПИИ (BACKUPS)")
        print("=" * 70)
        print(f"{'Имя снапшота':<30} {'Дата создания':<20} {'Размер':<10} Базы данных")
        print("-" * 70)
        for b in backups:
            print(f"{b['name']:<30} {b['mtime']:<20} {b['size_mb']:5.2f} MB   {', '.join(b['dbs'])}")
        print("=" * 70)
        print("Для восстановления выполните: python scripts/restore.py <имя_снапшота>")
        return

    restore_snapshot(args.snapshot, skip_safety_backup=args.no_safety)


if __name__ == "__main__":
    main()
