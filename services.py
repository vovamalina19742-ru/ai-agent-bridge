r"""
Модуль единого реестра сервисов (IoC / Service Registry & Connection Pooling)
для экосистемы D:\AI_Agent.

Реализует:
1. DatabasePool — потокобезопасный пул соединений SQLite с WAL-режимом (защита от "database is locked").
2. ModelProvider — абстрактный адаптер моделей (Local Ollama / Remote Linux 192.168.1.100).
3. ToolExecutionResult — Rich Result Envelope с изоляцией сбоев (Graceful Degradation).
"""

import os
import sys
import sqlite3
import threading
import json
import subprocess
from typing import Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =====================================================================
# 1. DatabasePool (SQLite Connection Pool с WAL & Busy Timeout)
# =====================================================================
class DatabasePool:
    """
    Потокобезопасный реестр соединений SQLite.
    Автоматически переводит базы в PRAGMA journal_mode=WAL и ставит busy_timeout=5000,
    чтобы исключить блокировки при параллельном обращении инструментов.
    """
    _instance = None
    _lock = threading.Lock()
    _connections: Dict[str, sqlite3.Connection] = {}

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatabasePool, cls).__new__(cls)
            return cls._instance

    def get_connection(self, db_path: str) -> sqlite3.Connection:
        abs_path = os.path.abspath(db_path)
        with self._lock:
            if abs_path not in self._connections:
                conn = sqlite3.connect(abs_path, timeout=10.0, check_same_thread=False)
                try:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA busy_timeout=5000;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                except Exception:
                    pass
                self._connections[abs_path] = conn
            return self._connections[abs_path]

    def close_all(self):
        with self._lock:
            for path, conn in self._connections.items():
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()


# =====================================================================
# 2. Rich Result Envelope & Graceful Degradation
# =====================================================================
class ToolExecutionResult:
    """
    Стандартизированный контейнер результата выполнения утилиты.
    Изолирует сбои: возвращает структурированный ответ и понятную подсказку для LLM.
    """
    def __init__(
        self,
        success: bool,
        tool_name: str,
        output: str = "",
        error_type: Optional[str] = None,
        error_details: Optional[str] = None,
        hint: Optional[str] = None
    ):
        self.success = success
        self.tool_name = tool_name
        self.output = output
        self.error_type = error_type
        self.error_details = error_details
        self.hint = hint

    def to_agent_format(self) -> str:
        """
        Форматирует результат для подачи в контекст LLM (роль tool).
        """
        if self.success:
            return self.output.strip() if self.output else "[Инструмент успешно завершился без текстового вывода]"
        
        # Структурированное сообщение об ошибке (Graceful Degradation)
        err_payload = {
            "status": "error",
            "tool": self.tool_name,
            "error_type": self.error_type or "UNKNOWN_ERROR",
            "details": (self.error_details or "")[:1000],
            "hint": self.hint or "Попробуй альтернативный инструмент или сообщи пользователю суть проблемы."
        }
        return f"[!] СБОЙ ИНСТРУМЕНТА:\n{json.dumps(err_payload, ensure_ascii=False, indent=2)}"


# =====================================================================
# 3. ModelProvider (Адаптер моделей с поддержкой локального/удаленного узла)
# =====================================================================
class ModelProvider:
    """
    Адаптер для обращения к моделям Ollama (локально или на удаленном узле).
    """
    def __init__(self, host: str = "http://localhost:11434", default_model: str = "qwen2.5-coder:7b"):
        self.host = host.rstrip("/")
        self.default_model = default_model
        self.chat_endpoint = f"{self.host}/api/chat"
        self.tags_endpoint = f"{self.host}/api/tags"

    def is_available(self, timeout: float = 2.0) -> bool:
        try:
            import requests
            r = requests.get(self.tags_endpoint, timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False


# Глобальные инстансы сервисов
db_pool = DatabasePool()
primary_provider = ModelProvider("http://localhost:11434", "qwen2.5-coder:7b")
fallback_provider = ModelProvider("http://192.168.1.100:11434", "qwen2.5-coder:7b")

# =====================================================================
# 4. Semantic Tool Aliases (Семантические имена для 7B/8B моделей)
# =====================================================================
SEMANTIC_TOOL_ALIASES = {
    # Книги и RAG
    "ask_books": "search_technical_books_library_rag",
    "search_books": "find_raw_books_fts_index",
    
    # Веб и Сеть
    "web_fetcher": "fetch_external_webpage_content",
    "network_scanner": "scan_local_network_hosts_and_ips",
    "port_checker": "check_network_port_open",
    
    # Память и сохраненные ссылки
    "memory_manager": "save_or_recall_permanent_memory",
    "search_saved_links": "search_previously_studied_links_and_repos",
    
    # Документы, код и сканы
    "doc_reader": "read_and_parse_local_document_file",
    "code_check": "verify_and_lint_code_syntax",
    "auto_refactor": "self_healing_code_refactor_and_fix",
    "ocr_scans": "recognize_text_from_images_and_scans",
    "cad_extractor": "extract_equipment_from_cad_and_blueprints",
    "pipeline_bom": "generate_blueprint_bom_takeoff_specification",
    
    # Статус, бэкапы, ночные операции и сервер
    "status_report": "check_system_and_services_health",
    "backup_manager": "manage_hot_database_backups",
    "night_ops": "run_nightly_maintenance_chain",
    "remote_server": "execute_commands_on_linux_server",
    "consult_senior_ai": "consult_with_senior_architect_ai",
}
