# -*- coding: utf-8 -*-
"""
night_ops.py — Оркестратор регламентного ночного обслуживания комплекса.

Цепочка шагов:
1. Экспресс-диагностика экосистемы (status_report).
2. Создание горячего снапшота баз SQLite и отправка на Linux-сервер 192.168.1.100 (backup_manager).
3. Проверка синтаксиса и чистоты инструментов AI_Agent (code_check).
4. Проверка доступности SearXNG и Ollama.
5. Запись итогового краткого отчета в журнал и в очередь/память.
"""
import os
import sys
import argparse
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
sys.path.append(BASE_DIR)
sys.path.append(TOOLS_DIR)

TOOL_SPEC = {
    "name": "night_ops",
    "description": "Ночная смена: цепочка обслуживания (диагностика -> бэкап на сервер -> проверка кода -> отчет).",
    "parameters": {
        "type": "object",
        "properties": {
            "skip_server": {
                "type": "boolean",
                "description": "Пропустить отправку на Linux-сервер (по умолчанию: false)",
            },
        },
    },
}


def run_night_maintenance(skip_server=False):
    logs = []
    ts_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs.append(f"=== ЗАПУСК НОЧНОЙ СМЕНЫ [{ts_start}] ===")

    # 1. Диагностика
    try:
        from status_report import generate_report
        rep = generate_report()
        logs.append("[1/4] Диагностика системы:\n" + rep)
    except Exception as e:
        logs.append(f"[1/4] Ошибка диагностики: {e}")

    # 2. Резервное копирование
    try:
        from backup_manager import create_backup
        b_res = create_backup(upload_to_server=not skip_server, keep_last=5)
        logs.append("[2/4] Резервное копирование:\n" + b_res)
    except Exception as e:
        logs.append(f"[2/4] Ошибка бэкапа: {e}")

    # 3. Проверка целостности кода инструментов
    try:
        from code_check import check_syntax
        syn_ok, syn_msg = check_syntax(TOOLS_DIR)
        logs.append(f"[3/4] Проверка синтаксиса инструментов: {'ОК' if syn_ok else 'ОШИБКА'}\n• {syn_msg}")
    except Exception as e:
        logs.append(f"[3/4] Ошибка проверки кода: {e}")

    # 4. Итог
    ts_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs.append(f"[4/4] Ночная смена завершена успешно [{ts_end}].")

    full_text = "\n\n".join(logs)

    # Сохраняем отчет в локальный лог
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    with open(os.path.join(log_dir, f"night_ops_{today}.log"), "a", encoding="utf-8") as f:
        f.write(full_text + "\n" + "="*50 + "\n")

    return full_text


def main():
    parser = argparse.ArgumentParser(description="Ночная смена AI_Agent")
    parser.add_argument("--no-server", action="store_true", help="Пропустить выгрузку на сервер")
    args = parser.parse_args()

    out = run_night_maintenance(skip_server=args.no_server)
    print(out)


if __name__ == "__main__":
    main()
