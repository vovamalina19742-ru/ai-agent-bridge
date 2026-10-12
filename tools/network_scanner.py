import os
import sys
import subprocess
import threading
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOOL_SPEC = {
    "name": "network_scanner",
    "description": "Быстрое сканирование домашней сети (по умолчанию 192.168.1.x) для поиска всех активных IP-адресов и устройств.",
    "parameters": {
        "type": "object",
        "properties": {
            "subnet_prefix": {
                "type": "string",
                "description": "Префикс подсети (по умолчанию '192.168.1')"
            }
        }
    }
}

def ping_host(ip, alive_hosts):
    cmd = ["ping", "-n", "1", "-w", "400", ip]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode == 0:
        alive_hosts.append(ip)

def scan_network(prefix="192.168.1"):
    print(f"[*] Сканирование подсети {prefix}.1 - {prefix}.254...")
    alive = []
    threads = []

    # Многопоточный опрос первых 50 адресов + основных диапазонов (1-50, 100-120, 200-220)
    # или всей подсети
    for i in range(1, 255):
        ip = f"{prefix}.{i}"
        t = threading.Thread(target=ping_host, args=(ip, alive))
        threads.append(t)
        t.start()
        if len(threads) >= 60:
            for t in threads:
                t.join()
            threads = []

    for t in threads:
        t.join()

    # Считываем ARP таблицу Windows
    arp_res = subprocess.run(["arp", "-a"], capture_output=True, text=True, errors="replace")
    arp_lines = arp_res.stdout.splitlines()

    print(f"\n[+] Найдено активных узлов: {len(alive)}")
    print("-" * 50)
    print(f"{'IP-адрес':<18} {'MAC-адрес':<20} {'Тип'}")
    print("-" * 50)

    # Сортируем по 4-му октету
    alive.sort(key=lambda x: int(x.split('.')[-1]))

    for ip in alive:
        mac = "N/A"
        import re
        for line in arp_lines:
            if re.search(rf"\b{re.escape(ip)}\b", line):
                parts = line.split()
                if len(parts) >= 2:
                    mac = parts[1]
                break
        print(f"{ip:<18} {mac:<20} active")
    print("-" * 50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Сетевой сканер подсети")
    parser.add_argument("subnet_prefix", nargs="?", default="192.168.1", help="Префикс подсети")
    args = parser.parse_args()

    scan_network(args.subnet_prefix)
