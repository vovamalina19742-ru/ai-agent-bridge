import os
import sys
import time
import argparse
import sqlite3
import pymupdf as fitz

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Отключаем внутренние ошибки mupdf в консоли
try:
    fitz.TOOLS.mupdf_display_errors(False)
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "..")))
    from config import BOOKS_DIR as DEFAULT_BOOKS_DIR, DATABASE_PATH as DB_PATH
except Exception:
    DEFAULT_BOOKS_DIR = os.getenv("BOOKS_DIR", r"G:\Мой диск\книги")
    DB_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "..", "books_index.db"))
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150


TOOL_SPEC = {
    "name": "book_indexer",
    "description": "Индексирует книги в полнотекстовую базу SQLite FTS5 для быстрого поиска. Поддерживает инкрементальное обновление по дате файла.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "'status' для проверки состояния индекса или 'index' для запуска индексации",
                "enum": ["status", "index"]
            },
            "limit": {
                "type": "integer",
                "description": "Максимальное количество книг для индексации за этот запуск (по умолчанию без ограничений)"
            }
        }
    }
}

def ensure_db(con):
    """Инициализирует схему БД и таблицы FTS5."""
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            book_name TEXT,
            mtime REAL,
            pages INTEGER,
            indexed_at TEXT
        )
    """)
    con.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS book_fts USING fts5(
            book UNINDEXED,
            page UNINDEXED,
            body,
            prefix='2 3',
            tokenize='unicode61 remove_diacritics 2'
        )
    """)
    con.commit()

def iter_chunks(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    step = size - overlap
    if step <= 0:
        step = size
    for i in range(0, max(len(text), 1), step):
        chunk = text[i:i+size].strip()
        if len(chunk) > 60:
            yield chunk

def index_book(con, file_path):
    """Извлекает текст из PDF и записывает чанки в FTS5."""
    doc = fitz.open(file_path)
    pages = len(doc)
    book_name = os.path.basename(file_path)

    # Очищаем старые записи по этой книге перед записью
    con.execute("DELETE FROM book_fts WHERE book = ?", (book_name,))

    buf = ""
    buf_page = 1
    total_chunks = 0

    for pno in range(pages):
        try:
            page_text = doc[pno].get_text()
            if not page_text:
                continue
            buf += f"\n[Стр. {pno+1}] " + page_text

            while len(buf) >= CHUNK_SIZE:
                cut = buf.rfind(" ", 0, CHUNK_SIZE)
                cut = cut if cut > 400 else CHUNK_SIZE
                for chunk in iter_chunks(buf[:cut]):
                    con.execute(
                        "INSERT INTO book_fts(book, page, body) VALUES (?, ?, ?)",
                        (book_name, buf_page, chunk)
                    )
                    total_chunks += 1
                buf_page = pno + 1
                buf = buf[cut - CHUNK_OVERLAP:]
        except Exception:
            continue

    if buf.strip():
        for chunk in iter_chunks(buf):
            con.execute(
                "INSERT INTO book_fts(book, page, body) VALUES (?, ?, ?)",
                (book_name, buf_page, chunk)
            )
            total_chunks += 1

    doc.close()

    mtime = os.path.getmtime(file_path)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        "INSERT OR REPLACE INTO files(path, book_name, mtime, pages, indexed_at) VALUES (?, ?, ?, ?, ?)",
        (file_path, book_name, mtime, pages, now_str)
    )
    return total_chunks, pages

def get_status(con):
    """Возвращает текущую статистику индекса."""
    try:
        cur = con.cursor()
        files_count = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks_count = cur.execute("SELECT COUNT(*) FROM book_fts").fetchone()[0]
        db_size_mb = 0
        if os.path.exists(DB_PATH):
            db_size_mb = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
        return f"[i] Статус индекса FTS5:\n  - Проиндексировано книг: {files_count}\n  - Всего текстовых чанков: {chunks_count}\n  - Размер базы: {db_size_mb} МБ\n  - Путь к базе: {DB_PATH}"
    except Exception as e:
        return f"[!] Ошибка получения статуса: {e}"

def run_indexer(books_dir=DEFAULT_BOOKS_DIR, limit=None):
    if not os.path.exists(books_dir):
        print(f"[!] Каталог книг не найден: {books_dir}")
        return

    db_real_path = os.path.abspath(DB_PATH)
    con = sqlite3.connect(db_real_path)
    ensure_db(con)

    known = {r[0]: r[1] for r in con.execute("SELECT path, mtime FROM files")}

    all_pdfs = []
    for root, _, files in os.walk(books_dir):
        for f in files:
            if f.lower().endswith(".pdf"):
                all_pdfs.append(os.path.join(root, f))

    print(f"[*] Всего найдено PDF в каталоге: {len(all_pdfs)}. Ранее в базе: {len(known)}.")

    done = 0
    skipped = 0
    errors = 0
    t0 = time.time()

    for path in all_pdfs:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue

        if known.get(path) == mtime:
            skipped += 1
            continue

        if limit is not None and done >= limit:
            print(f"[!] Достигнут лимит индексации ({limit} книг).")
            break

        f_name = os.path.basename(path)
        try:
            chunks, pages = index_book(con, path)
            con.commit()
            done += 1
            elapsed = time.time() - t0
            print(f"[+] [{done}] {f_name} (стр: {pages}, чанков: {chunks}) [{elapsed:.1f}с]")
        except Exception as e:
            errors += 1
            con.rollback()
            print(f"[!] Ошибка индексации {f_name}: {e}")

    con.commit()
    print("\n" + get_status(con))
    print(f"[*] Сессия завершена: новых {done}, пропущено {skipped}, ошибок {errors}, общее время {time.time()-t0:.1f}с")
    con.close()

def main():
    parser = argparse.ArgumentParser(description="SQLite FTS5 Индексатор библиотеки книг")
    parser.add_argument("--action", choices=["status", "index"], default="index", help="Действие")
    parser.add_argument("--dir", default=DEFAULT_BOOKS_DIR, help="Каталог книг")
    parser.add_argument("--limit", type=int, default=None, help="Лимит книг для индексации")
    args = parser.parse_args()

    db_real_path = os.path.abspath(DB_PATH)
    con = sqlite3.connect(db_real_path)
    ensure_db(con)

    if args.action == "status":
        print(get_status(con))
        con.close()
        return

    con.close()
    run_indexer(books_dir=args.dir, limit=args.limit)

if __name__ == "__main__":
    main()
