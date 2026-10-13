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
import uuid
import array
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =====================================================================
# 1. DatabasePool (SQLite Connection Pool с WAL & Busy Timeout)
# =====================================================================
class DatabasePool:
    """
    Потокобезопасный реестр соединений SQLite.
    Использует threading.local(), чтобы каждый поток имел собственное изолированное соединение.
    Автоматически переводит базы в PRAGMA journal_mode=WAL и ставит busy_timeout=5000,
    чтобы исключить блокировки при параллельном обращении инструментов.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatabasePool, cls).__new__(cls)
                cls._instance._local = threading.local()
                cls._instance._all_conns = []
            return cls._instance

    def get_connection(self, db_path: str) -> sqlite3.Connection:
        abs_path = os.path.abspath(db_path)
        if not hasattr(self._local, "connections"):
            self._local.connections = {}

        if abs_path not in self._local.connections:
            conn = sqlite3.connect(abs_path, timeout=10.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except Exception:
                pass
            self._local.connections[abs_path] = conn
            with self._lock:
                self._all_conns.append(conn)
        return self._local.connections[abs_path]

    def close_all(self):
        with self._lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()
            if hasattr(self._local, "connections"):
                self._local.connections.clear()


# =====================================================================
# 1.1 EpistemicMemoryStore (SQLite WAL Vector & Fact Storage)
# =====================================================================
class EpistemicMemoryStore:
    """
    Потокобезопасное хранилище долговременной эпистемической памяти и векторных эмбеддингов в SQLite WAL.
    Управляется через DatabasePool (zero-lock, concurrency-safe).
    Поддерживает бесшовную миграцию из устаревших JSON-файлов (memory.json, saved_links.json).
    """
    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            try:
                import config
                self.db_path = getattr(config, "MEMORY_DB_PATH", os.path.join(BASE_DIR, "memory.db"))
            except Exception:
                self.db_path = os.path.join(BASE_DIR, "memory.db")
        else:
            self.db_path = db_path
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        return DatabasePool().get_connection(self.db_path)

    def _init_schema(self):
        conn = self._get_conn()
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS epistemic_memory (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL DEFAULT 'fact',
                verification_status TEXT NOT NULL DEFAULT 'verified',
                confidence REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'user',
                tags TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT,
                embedding BLOB
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_topic ON epistemic_memory(topic);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_type ON epistemic_memory(memory_type);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_status ON epistemic_memory(verification_status);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_links (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                timestamp TEXT,
                tags TEXT,
                embedding BLOB
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_links_url ON saved_links(url);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings_cache (
                cache_key TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            """)

    @staticmethod
    def pack_embedding(emb: Optional[List[float]]) -> Optional[bytes]:
        if not emb:
            return None
        return array.array('f', emb).tobytes()

    @staticmethod
    def unpack_embedding(blob: Optional[bytes]) -> Optional[List[float]]:
        if not blob:
            return None
        arr = array.array('f')
        arr.frombytes(blob)
        return arr.tolist()

    def load_all_memories(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id, topic, content, memory_type, verification_status, confidence, source, tags, created_at, updated_at, metadata FROM epistemic_memory ORDER BY created_at ASC")
        rows = cursor.fetchall()
        result = []
        for r in rows:
            rec = {
                "id": r[0],
                "topic": r[1],
                "content": r[2],
                "memory_type": r[3],
                "verification_status": r[4],
                "confidence": r[5],
                "source": r[6],
                "tags": json.loads(r[7]) if r[7] else [],
                "created_at": r[8],
                "updated_at": r[9],
            }
            if r[10]:
                try:
                    rec["metadata"] = json.loads(r[10])
                except Exception:
                    pass
            result.append(rec)
        return result

    def save_memory_record(self, rec: Dict[str, Any]) -> None:
        conn = self._get_conn()
        tags_json = json.dumps(rec.get("tags", []), ensure_ascii=False) if rec.get("tags") is not None else None
        meta_json = json.dumps(rec.get("metadata", {}), ensure_ascii=False) if rec.get("metadata") is not None else None
        with conn:
            conn.execute("""
            INSERT INTO epistemic_memory (id, topic, content, memory_type, verification_status, confidence, source, tags, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                topic=excluded.topic,
                content=excluded.content,
                memory_type=excluded.memory_type,
                verification_status=excluded.verification_status,
                confidence=excluded.confidence,
                source=excluded.source,
                tags=excluded.tags,
                updated_at=excluded.updated_at,
                metadata=excluded.metadata;
            """, (
                rec["id"],
                rec.get("topic", ""),
                rec.get("content", ""),
                rec.get("memory_type", "fact"),
                rec.get("verification_status", "verified"),
                float(rec.get("confidence", 1.0)),
                rec.get("source", "user"),
                tags_json,
                rec.get("created_at", ""),
                rec.get("updated_at", rec.get("created_at", "")),
                meta_json
            ))

    def save_all_memories(self, records: List[Dict[str, Any]]) -> None:
        conn = self._get_conn()
        with conn:
            for rec in records:
                self.save_memory_record(rec)

    def delete_memory_record(self, record_id: str) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute("DELETE FROM epistemic_memory WHERE id = ?", (record_id,))

    def load_embedding_cache(self) -> Dict[str, List[float]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT cache_key, embedding FROM embeddings_cache")
        cache = {}
        for row in cursor.fetchall():
            unpacked = self.unpack_embedding(row[1])
            if unpacked:
                cache[row[0]] = unpacked
        return cache

    def save_embedding_cache_batch(self, mapping: Dict[str, List[float]]) -> None:
        if not mapping:
            return
        conn = self._get_conn()
        now_str = datetime.now().isoformat()
        with conn:
            for k, emb in mapping.items():
                packed = self.pack_embedding(emb)
                if packed:
                    conn.execute("""
                    INSERT INTO embeddings_cache (cache_key, embedding, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET embedding=excluded.embedding;
                    """, (k, packed, now_str))

    def load_saved_links(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id, url, title, summary, timestamp, tags FROM saved_links ORDER BY timestamp DESC")
        result = []
        for r in cursor.fetchall():
            result.append({
                "id": r[0],
                "url": r[1],
                "title": r[2] or "",
                "summary": r[3] or "",
                "timestamp": r[4] or "",
                "tags": json.loads(r[5]) if r[5] else []
            })
        return result

    def save_saved_link(self, url: str, title: str, summary: str, timestamp: str = "", tags: Optional[List[str]] = None) -> None:
        conn = self._get_conn()
        link_id = f"link_{uuid.uuid5(uuid.NAMESPACE_URL, url).hex[:12]}"
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        with conn:
            conn.execute("""
            INSERT INTO saved_links (id, url, title, summary, timestamp, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                summary=excluded.summary,
                timestamp=excluded.timestamp,
                tags=excluded.tags;
            """, (link_id, url, title, summary, timestamp, tags_json))

    def save_all_saved_links(self, links: List[Dict[str, Any]]) -> None:
        conn = self._get_conn()
        with conn:
            for it in links:
                url = it.get("url", "")
                if not url:
                    continue
                link_id = it.get("id") or f"link_{uuid.uuid5(uuid.NAMESPACE_URL, url).hex[:12]}"
                tags_json = json.dumps(it.get("tags", []), ensure_ascii=False)
                conn.execute("""
                INSERT INTO saved_links (id, url, title, summary, timestamp, tags)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    summary=excluded.summary,
                    timestamp=excluded.timestamp,
                    tags=excluded.tags;
                """, (link_id, url, it.get("title", ""), it.get("summary", ""), it.get("timestamp", ""), tags_json))

    def migrate_from_json_files(self, project_root: str) -> None:
        """Автоматическая миграция из существующих JSON файлов в SQLite WAL."""
        mem_file = os.path.join(project_root, "memory.json")
        cache_file = os.path.join(project_root, "memory_embeddings.json")
        links_file = os.path.join(project_root, "saved_links.json")
        links_cache_file = os.path.join(project_root, "links_embeddings.json")

        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM epistemic_memory")
        mem_count = cur.fetchone()[0]

        if mem_count == 0 and os.path.exists(mem_file):
            try:
                with open(mem_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    self.save_all_memories(data)
            except Exception as e:
                print(f"[!] Ошибка миграции {mem_file}: {e}", file=sys.stderr)

        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                if isinstance(cdata, dict) and cdata:
                    self.save_embedding_cache_batch(cdata)
            except Exception:
                pass

        cur.execute("SELECT COUNT(*) FROM saved_links")
        links_count = cur.fetchone()[0]
        if links_count == 0 and os.path.exists(links_file):
            try:
                with open(links_file, "r", encoding="utf-8") as f:
                    ldata = json.load(f)
                if isinstance(ldata, list) and ldata:
                    self.save_all_saved_links(ldata)
            except Exception as e:
                print(f"[!] Ошибка миграции {links_file}: {e}", file=sys.stderr)

        if os.path.exists(links_cache_file):
            try:
                with open(links_cache_file, "r", encoding="utf-8") as f:
                    lcdata = json.load(f)
                if isinstance(lcdata, dict) and lcdata:
                    self.save_embedding_cache_batch(lcdata)
            except Exception:
                pass


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


@dataclass
class ExecutionResult:
    """
    Канонический контракт выполнения задачи/шага ReAct-оркестратора.
    Исключает маскировку системных и сетевых ошибок под успешные строковые ответы.
    """
    success: bool
    result: Optional[str] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    is_retryable: bool = False
    iterations: int = 0

    @classmethod
    def ok(cls, result: str, iterations: int = 1, warnings: List[str] = None) -> "ExecutionResult":
        return cls(
            success=True,
            result=result,
            error=None,
            warnings=warnings or [],
            is_retryable=False,
            iterations=iterations
        )

    @classmethod
    def fail(cls, error: str, is_retryable: bool = False, iterations: int = 0, warnings: List[str] = None) -> "ExecutionResult":
        return cls(
            success=False,
            result=None,
            error=error,
            warnings=warnings or [],
            is_retryable=is_retryable,
            iterations=iterations
        )


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


from config import OLLAMA_BASE_URL, REMOTE_OLLAMA_URL, DEFAULT_MODEL

# Глобальные инстансы сервисов
db_pool = DatabasePool()
primary_provider = ModelProvider(OLLAMA_BASE_URL, DEFAULT_MODEL)
fallback_provider = ModelProvider(REMOTE_OLLAMA_URL, DEFAULT_MODEL)

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
