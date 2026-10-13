# -*- coding: utf-8 -*-
"""
config.py — Единый источник конфигурации для всей экосистемы AI_Agent.
Централизованно загружает и валидирует параметры среды (.env).
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")


class ConfigError(Exception):
    """Критическая ошибка конфигурации среды."""
    pass


def _load_env_file():
    """Загружает переменные из .env в os.environ (с поддержкой dotenv или встроенным парсером)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE)
    except ImportError:
        if os.path.exists(ENV_FILE):
            try:
                with open(ENV_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass


_load_env_file()


def _get_env(key: str, default: str = None, required: bool = False) -> str:
    val = os.getenv(key, default)
    if required and (val is None or val.strip() == ""):
        raise ConfigError(f"Критическая ошибка конфигурации: отсутствует обязательная переменная '{key}'.")
    return val


# ---------------- Базовые пути и хранилище ----------------
DATABASE_PATH = _get_env("DATABASE_PATH", os.path.join(BASE_DIR, "books_index.db"))
QUEUE_DB_PATH = _get_env("QUEUE_DB_PATH", os.path.join(BASE_DIR, "bridge_queue.db"))
BOOKS_DIR = _get_env("BOOKS_DIR", r"G:\Мой диск\книги")

# ---------------- Настройки локальной и удаленной Ollama ----------------
OLLAMA_BASE_URL = _get_env("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = _get_env("DEFAULT_MODEL", "qwen2.5-coder:7b")
EMBEDDING_MODEL = _get_env("EMBEDDING_MODEL", "nomic-embed-text")

# ---------------- Настройки удаленного Linux-сервера ----------------
REMOTE_HOST = _get_env("REMOTE_HOST", "192.168.1.100")
REMOTE_USER = _get_env("REMOTE_USER", "ubuntu")
SERVER_MAC_PRIMARY = _get_env("SERVER_MAC_PRIMARY", "00:11:22:33:44:55")
SERVER_MAC_SECONDARY = _get_env("SERVER_MAC_SECONDARY", "00:11:22:33:44:66")
REMOTE_OLLAMA_URL = _get_env("REMOTE_OLLAMA_URL", f"http://{REMOTE_HOST}:11434")

# ---------------- Настройки веб-сервисов ----------------
WEB_UI_PORT = int(_get_env("WEB_UI_PORT", "3080"))
SEARXNG_URL = _get_env("SEARXNG_URL", f"http://{REMOTE_HOST}:8080")
