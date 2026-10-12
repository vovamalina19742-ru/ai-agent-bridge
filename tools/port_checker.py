import socket
import argparse
import sys

TOOL_SPEC = {
    "name": "check_port",
    "description": "Проверяет открыт ли конкретный сетевой TCP-порт на хосте (IP или доменное имя).",
    "parameters": {
        "type": "object",
        "properties": {
            "host": {
                "type": "string",
                "description": "Целевой IP-адрес или доменное имя (например, 192.168.1.1)"
            },
            "port": {
                "type": "integer",
                "description": "Номер TCP порта для проверки (например, 80, 443, 22, 8080)"
            }
        },
        "required": ["host", "port"]
    }
}

def check_port(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.5)
    try:
        result = sock.connect_ex((host, int(port)))
        if result == 0:
            print(f"[+] Port {port} is OPEN on {host}")
            return True
        else:
            print(f"[-] Port {port} is CLOSED on {host}")
            return False
    except socket.error as e:
        print(f"[!] Socket error on {host}:{port} -> {e}")
        return False
    finally:
        sock.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check if a port is open on a host.")
    parser.add_argument('host', type=str, help="The host to check.")
    parser.add_argument('port', type=int, help="The port to check.")
    args = parser.parse_args()
    check_port(args.host, args.port)
