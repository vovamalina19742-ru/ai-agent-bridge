import os
import sys
import socket
import subprocess
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOOL_SPEC = {
    "name": "remote_server",
    "description": "Управление домашним Linux-сервером (192.168.1.100). Действия: 'wake' (разбудить через WOL), 'sleep' (отправить в спящий режим S3 Suspend), 'status' (проверить доступность сети и железа), 'exec' (выполнить безопасную bash команду на сервере).",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Одно из действий: 'wake', 'sleep', 'status', 'exec'"
            },
            "command": {
                "type": "string",
                "description": "Bash-команда для выполнения (только если action='exec')"
            }
        },
        "required": ["action"]
    }
}

# Загрузка параметров из конфигурации (.env)
try:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from config import REMOTE_HOST as SERVER_IP, REMOTE_USER as SERVER_USER, SERVER_MAC_PRIMARY, SERVER_MAC_SECONDARY
    MAC_ADDRESSES = [SERVER_MAC_PRIMARY.replace(":", ""), SERVER_MAC_SECONDARY.replace(":", "")]
except Exception:
    SERVER_IP = os.getenv("REMOTE_HOST", "192.168.1.100")
    SERVER_USER = os.getenv("REMOTE_USER", "ubuntu")
    MAC_ADDRESSES = [
        os.getenv("SERVER_MAC_PRIMARY", "00:11:22:33:44:55").replace(":", ""),
        os.getenv("SERVER_MAC_SECONDARY", "00:11:22:33:44:66").replace(":", "")
    ]

# Защитный черный список деструктивных системных команд (Guardrails)
FORBIDDEN_PATTERNS = [
    "rm -rf", "rm -fr", "mkfs", "dd if=", "dd of=", "shutdown", "reboot", "init 0", "init 6",
    ":(){", ">/dev/sd", "> /dev/sd", ">/dev/nvme", "> /dev/nvme",
    "chmod -r 777", "chmod 777", "chown -r", "wipefs", "poweroff", "halt"
]


def send_wol():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for mac in MAC_ADDRESSES:
            packet = b'\xff' * 6 + bytes.fromhex(mac) * 16
            for target_ip in ["192.168.1.255", "255.255.255.255", SERVER_IP]:
                try:
                    sock.sendto(packet, (target_ip, 9))
                except Exception:
                    pass
    print(f"[WOL] Пакет пробуждения (Magic Packet) отправлен на {SERVER_IP}. Сервер включается.")

def sleep_server():
    remote_cmd = "nohup bash -c 'sleep 2 && sudo systemctl suspend || sudo /usr/sbin/pm-suspend' >/dev/null 2>&1 &"
    cmd = [
        "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
        "-o", "PasswordAuthentication=no", "-o", "BatchMode=yes",
        f"{SERVER_USER}@{SERVER_IP}", remote_cmd
    ]
    try:
        res = subprocess.run(cmd, timeout=8, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[OK] Команда сна отправлена. Сервер {SERVER_IP} переходит в спящий режим S3 (кулеры останавливаются).")
        else:
            print(f"[!] Ошибка отправки команды сна: {res.stderr.strip()}")
    except Exception as e:
        print(f"[!] Не удалось подключиться к серверу: {e}")

def check_status():
    # 1. Быстрый пинг
    ping_cmd = ["ping", "-n", "1", "-w", "1000", SERVER_IP]
    ping_res = subprocess.run(ping_cmd, capture_output=True, text=True)
    is_alive = (ping_res.returncode == 0)

    if not is_alive:
        print(f"[-] Сервер {SERVER_IP} выключен или спит (Пинг: нет ответа). Используйте action='wake' для включения.")
        return

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
            print(f"[+] Сервер {SERVER_IP} ОНЛАЙН и готов к работе:\n{res.stdout.strip()}")
        else:
            print(f"[+] Сервер {SERVER_IP} пингуется, но SSH временно недоступен.")
    except Exception as e:
        print(f"[+] Сервер {SERVER_IP} отвечает на пинг. Ошибка SSH: {e}")

def exec_remote(command):
    if not command:
        print("[-] Ошибка: укажите команду для выполнения на сервере.")
        return

    # Проверка черного списка команд
    cmd_lower = command.lower()
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.lower() in cmd_lower:
            print(f"[!] БЕЗОПАСНОСТЬ: Команда заблокирована защитным фильтром (обнаружен деструктивный паттерн '{pattern}').")
            return


    cmd = [
        "ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "PasswordAuthentication=no", "-o", "BatchMode=yes",
        f"{SERVER_USER}@{SERVER_IP}", command
    ]
    try:
        res = subprocess.run(cmd, timeout=30, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.stdout:
            print(res.stdout.strip())
        if res.stderr:
            print(f"STDERR: {res.stderr.strip()}")
    except subprocess.TimeoutExpired:
        print("[!] Таймаут выполнения команды на сервере (30 сек).")
    except Exception as e:
        print(f"[!] Ошибка запуска команды на сервере: {e}")

def main():
    parser = argparse.ArgumentParser(description="Управление домашним Linux-сервером")
    parser.add_argument("action", choices=["wake", "sleep", "status", "exec"], help="Действие")
    parser.add_argument("command", nargs="?", default="", help="Bash-команда для exec")
    args = parser.parse_args()

    if args.action == "wake":
        send_wol()
    elif args.action == "sleep":
        sleep_server()
    elif args.action == "status":
        check_status()
    elif args.action == "exec":
        exec_remote(args.command)

if __name__ == "__main__":
    main()
