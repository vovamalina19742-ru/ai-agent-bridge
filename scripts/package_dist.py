# -*- coding: utf-8 -*-
"""
package_dist.py — Автоматическая сборка чистого дистрибутива под разработку.
Исключает рабочие базы данных, журналы, конфиги с секретами (.env), байт-код и историю git.
"""

import os
import sys
import zipfile
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXCLUDE_DIRS = {
    ".git", "__pycache__", "data", "logs", "backups", ".idea", ".vscode", "tmp", ".pytest_cache"
}
EXCLUDE_EXTS = {
    ".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log", ".env"
}
EXCLUDE_FILES = {
    ".env", "bridge_queue.db", "books_index.db", "bridge_mcp.log", "bridge_worker.log"
}


def package_clean_distribution(output_filename: str = None) -> str:
    if not output_filename:
        output_filename = os.path.join(BASE_DIR, "AI_Agent_Clean_Dist.zip")

    output_basename = os.path.basename(output_filename)
    print("=" * 60)
    print("  СБОРКА ЧИСТОГО ДИСТРИБУТИВА (DEVELOPMENT / AUDIT PACKAGE)")
    print("=" * 60)

    count = 0
    total_bytes = 0

    with zipfile.ZipFile(output_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(BASE_DIR):
            # Фильтруем каталоги на лету
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

            for file in sorted(files):
                ext = os.path.splitext(file)[1].lower()
                if ext in EXCLUDE_EXTS or file in EXCLUDE_FILES or file.endswith(".zip"):
                    continue

                full_path = os.path.join(root, file)
                arcname = os.path.relpath(full_path, BASE_DIR)

                if arcname == output_basename:
                    continue

                zipf.write(full_path, arcname)
                size_kb = os.path.getsize(full_path) / 1024.0
                total_bytes += os.path.getsize(full_path)
                count += 1
                print(f" [+] {arcname:<45} ({size_kb:6.1f} KB)")

    zip_size_mb = os.path.getsize(output_filename) / (1024.0 * 1024.0)
    print("-" * 60)
    print(f"[✓] Упаковано файлов: {count} (исходный размер: {total_bytes / (1024 * 1024):.2f} MB)")
    print(f"[✓] Чистый архив создан: {output_filename} ({zip_size_mb:.2f} MB)")
    print("[✓] Конфиденциальные данные (.env, *.db, *.log, *.pyc) успешно изолированы.")
    print("=" * 60)
    return output_filename


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    package_clean_distribution(out)
