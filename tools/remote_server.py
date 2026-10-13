# -*- coding: utf-8 -*-
r"""
remote_server.py — Безопасное управление удаленным Linux-сервером (192.168.1.100).

Архитектура безопасности (Allowlist Security Matrix):
1. Полный отказ от выполнения произвольных bash-команд через черный список.
2. Фиксированный белый список безопасных структурированных операций (ALLOWED_OPERATIONS).
3. Строгая валидация аргументов через регулярные выражения и белый список служб (ALLOWED_SERVICES).
4. Передача аргументов массивом в subprocess.run без shell=True (исключает Command Injection).
"""

import os
import sys
import socket
import subprocess
import argparse
from typing import Dict, Any, List

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# Централизованная конфигурация
try:
    from config import (
        REMOTE_HOST as SERVER_IP,
        REMOTE_USER as SERVER_USER,
        SERVER_MAC_PRIMARY,
        SERVER_MAC_SECONDARY
    )
    MAC_ADDRESSES = [SERVER_MAC_PRIMARY.replace(":", ""), SERVER_MAC_SECONDARY.replace(":", "")]
except Exception:
    SERVER_IP = os.getenv("REMOTE_HOST", "192.168.1.100")
    SERVER_USER = os.getenv("REMOTE_USER", "ubuntu")
    MAC_ADDRESSES = [
        os.getenv("SERVER_MAC_PRIMARY", "00:11:22:33:44:55").replace(":", ""),
        os.getenv("SERVER_MAC_SECONDARY", "00:11:22:33:44:66").replace(":", "")
    ]

# ---------------- Белый список разрешенных служб ----------------
ALLOWED_SERVICES = {
    "searxng", "casaos", "ollama", "docker", "ssh", "smbd", "ufw", "cron", "containerd"
}

# ---------------- Матрица разрешенных операций (Allowlist) ----------------
ALLOWED_OPERATIONS = {
    "get_disk_space": {
        "cmd": ["df", "-h", "/", "/DATA"],
        "needs_arg": False,
        "desc": "Свободное место на корневом разделе и /DATA"
    },
    "docker_ps": {
        "cmd": ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
        "needs_arg": False,
        "desc": "Статус запущенных Docker-контейнеров"
    },
    "ram_info": {
        "cmd": ["free", "-h"],
        "needs_arg": False,
        "desc": "Использование оперативной памяти (RAM)"
    },
    "uptime_info": {
        "cmd": ["uptime"],
        "needs_arg": False,
        "desc": "Время непрерывной работы и средняя загрузка (load average)"
    },
    "check_service_status": {
        "cmd": ["systemctl", "is-active"],
        "needs_arg": True,
        "desc": "Проверка активности службы (active / inactive)"
    },
    "restart_service": {
        "cmd": ["sudo", "systemctl", "restart"],
        "needs_arg": True,
        "desc": "Перезапуск разрешенной службы"
    },
    "tail_logs": {
        "cmd": ["journalctl", "-u"],
        "extra_args": ["--lines", "50", "--no-pager"],
        "needs_arg": True,
        "desc": "Последние 50 строк журнала указанной службы"
    }
}

TOOL_SPEC = {
    "name": "remote_server",
    "description": "Безопасное управление удаленным Linux-сервером через строгий белый список операций (Allowlist). Действия: 'wake' (WOL), 'sleep' (S3 Suspend), 'status' (проверка доступности), 'exec' (вызов разрешенной операции: get_disk_space, docker_ps, ram_info, uptime_info, check_service_status, restart_service, tail_logs).",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["wake", "sleep", "status", "exec"],
                "description": "Действие: 'wake', 'sleep', 'status', 'exec'"
            },
            "operation": {
                "type": "string",
                "description": "Разрешенная операция для 'exec': get_disk_space, docker_ps, ram_info, uptime_info, check_service_status, restart_service, tail_logs"
            },
            "target": {
                "type": "string",
                "description": "Имя службы для check_service_status, restart_service, tail_logs (например: searxng, casaos, ollama, docker)"
            }
        },
        "required": ["action"]
    }
}


def send_wol() -> Dict[str, Any]:
    """Отправка Wake-on-LAN Magic Packet."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for mac in MAC_ADDRESSES:
            packet = b'\xff' * 6 + bytes.fromhex(mac) * 16
            for target_ip in ["192.168.1.255", "255.255.255.255", SERVER_IP]:
                try:
                    sock.sendto(packet, (target_ip, 9))
                except Exception:
                    pass
    msg = f"[WOL] Пакет пробуждения (Magic Packet) отправлен на {SERVER_IP}. Сервер включается."
    print(msg)
    return {"success": True, "message": msg}


def sleep_server() -> Dict[str, Any]:
    """Перевод сервера в спящий режим S3 Suspend."""
    remote_cmd = "nohup bash -c 'sleep 2 && sudo systemctl suspend || sudo /usr/sbin/pm-suspend' >/dev/null 2>&1 &"
    cmd = [
        "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
        "-o", "PasswordAuthentication=no", "-o", "BatchMode=yes",
        f"{SERVER_USER}@{SERVER_IP}", remote_cmd
    ]
    try:
        res = subprocess.run(cmd, timeout=8, capture_output=True, text=True)
        if res.returncode == 0:
            msg = f"[OK] Команда сна отправлена. Сервер {SERVER_IP} переходит в спящий режим S3."
            print(msg)
            return {"success": True, "message": msg}
        else:
            err = f"[!] Ошибка отправки команды сна: {res.stderr.strip()}"
            print(err)
            return {"success": False, "error": err}
    except Exception as e:
        err = f"[!] Не удалось подключиться к серверу: {e}"
        print(err)
        return {"success": False, "error": err}


def check_status() -> Dict[str, Any]:
    """Быстрая проверка доступности и базовых метрик сервера."""
    # 1. Пинг
    ping_cmd = ["ping", "-n", "1", "-w", "1000", SERVER_IP]
    ping_res = subprocess.run(ping_cmd, capture_output=True, text=True)
    is_alive = (ping_res.returncode == 0)

    if not is_alive:
        msg = f"[-] Сервер {SERVER_IP} выключен или спит (Пинг: нет ответа). Используйте action='wake' для включения."
        print(msg)
        return {"success": False, "alive": False, "message": msg}

    # 2. SSH зонд
    ssh_probe = "free -m | grep Mem | awk '{print \"RAM: \" $3 \"MB / \" $2 \"MB\"}'; df -h / | tail -n 1 | awk '{print \"Диск /: \" $4 \" свободно\"}'"
    cmd = [
        "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
        "-o", "PasswordAuthentication=no", "-o", "BatchMode=yes",
        f"{SERVER_USER}@{SERVER_IP}", ssh_probe
    ]
    try:
        res = subprocess.run(cmd, timeout=8, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0:
            out = f"[+] Сервер {SERVER_IP} ОНЛАЙН и готов к работе:\n{res.stdout.strip()}"
            print(out)
            return {"success": True, "alive": True, "output": res.stdout.strip()}
        else:
            msg = f"[+] Сервер {SERVER_IP} пингуется, но SSH временно недоступен."
            print(msg)
            return {"success": True, "alive": True, "ssh_ready": False, "message": msg}
    except Exception as e:
        msg = f"[+] Сервер {SERVER_IP} отвечает на пинг. Ошибка SSH: {e}"
        print(msg)
        return {"success": True, "alive": True, "ssh_ready": False, "error": str(e)}


def execute_remote_allowed_action(action: str, target: str = "") -> Dict[str, Any]:
    """
    Выполнение строго разрешенной операции из белого списка (Allowlist).
    Исключает произвольные shell-команды и экранирует аргументы.
    """
    clean_action = (action or "").strip().lower()
    if clean_action not in ALLOWED_OPERATIONS:
        valid_ops = ", ".join(sorted(ALLOWED_OPERATIONS.keys()))
        return {
            "success": False,
            "error": f"Операция '{action}' отклонена политикой безопасности. Разрешены только: {valid_ops}"
        }

    op_config = ALLOWED_OPERATIONS[clean_action]
    remote_args = list(op_config["cmd"])

    if op_config.get("needs_arg"):
        clean_target = (target or "").strip().lower()
        # Строгая проверка имени службы: только буквы, цифры, дефис и подчеркивание
        if not clean_target or not clean_target.replace("_", "").replace("-", "").isalnum():
            return {
                "success": False,
                "error": f"Некорректный или пустой аргумент target='{target}' для операции '{action}'."
            }

        # Проверка по белому списку служб
        if clean_target not in ALLOWED_SERVICES:
            valid_services = ", ".join(sorted(ALLOWED_SERVICES))
            return {
                "success": False,
                "error": f"Служба '{target}' не входит в белый список разрешенных служб: [{valid_services}]"
            }

        remote_args.append(clean_target)
        if "extra_args" in op_config:
            remote_args.extend(op_config["extra_args"])

    # Безопасная сборка SSH вызова без shell=True
    ssh_cmd = [
        "ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
        "-o", "PasswordAuthentication=no", "-o", "BatchMode=yes",
        f"{SERVER_USER}@{SERVER_IP}"
    ] + remote_args

    try:
        res = subprocess.run(
            ssh_cmd, timeout=20, capture_output=True,
            text=True, encoding="utf-8", errors="replace"
        )
        stdout = (res.stdout or "").strip()
        stderr = (res.stderr or "").strip()
        return {
            "success": res.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": res.returncode,
            "error": None if res.returncode == 0 else (stderr or f"Код выхода: {res.returncode}")
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Таймаут выполнения операции '{action}' (20 сек)."}
    except Exception as e:
        return {"success": False, "error": f"Ошибка выполнения операции '{action}': {e}"}


def main():
    parser = argparse.ArgumentParser(description="Безопасное управление удаленным сервером (Allowlist)")
    parser.add_argument("action", nargs="?", default="status", help="Действие: wake, sleep, status, exec или имя операции")
    parser.add_argument("target_or_op", nargs="?", default="", help="Имя операции (при action=exec) или имя службы")
    parser.add_argument("--target", "-t", default="", help="Имя службы (target)")
    args = parser.parse_args()

    act = (args.action or "").strip().lower()

    if act == "wake":
        send_wol()
    elif act == "sleep":
        sleep_server()
    elif act == "status":
        check_status()
    elif act == "exec":
        op = args.target_or_op
        target = args.target
        res = execute_remote_allowed_action(op, target)
        if res["success"]:
            print(res["stdout"])
        else:
            print(f"[-] Ошибка: {res['error']}")
    elif act in ALLOWED_OPERATIONS:
        # Прямой вызов: python remote_server.py docker_ps
        target = args.target_or_op or args.target
        res = execute_remote_allowed_action(act, target)
        if res["success"]:
            print(res["stdout"])
        else:
            print(f"[-] Ошибка: {res['error']}")
    else:
        print(f"[-] Неизвестное действие '{act}'. Допустимо: wake, sleep, status, exec, или одна из разрешенных операций: {list(ALLOWED_OPERATIONS.keys())}")


if __name__ == "__main__":
    main()
