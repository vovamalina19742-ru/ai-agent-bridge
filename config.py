import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")

def load_env():
    """Загружает переменные из .env в os.environ (чистый Python, без сторонних зависимостей)."""
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

load_env()

# Настройки удаленного Linux-сервера (значения по умолчанию переопределяются в .env)
REMOTE_HOST = os.getenv("REMOTE_HOST", "192.168.1.100")
REMOTE_USER = os.getenv("REMOTE_USER", "ubuntu")
SERVER_MAC_PRIMARY = os.getenv("SERVER_MAC_PRIMARY", "00:11:22:33:44:55")
SERVER_MAC_SECONDARY = os.getenv("SERVER_MAC_SECONDARY", "00:11:22:33:44:66")

# Пути к хранилищам
BOOKS_DIR = os.getenv("BOOKS_DIR", r"G:\Мой диск\книги")
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "books_index.db"))
QUEUE_DB_PATH = os.getenv("QUEUE_DB_PATH", os.path.join(BASE_DIR, "bridge_queue.db"))

# Настройки Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen2.5-coder:7b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
