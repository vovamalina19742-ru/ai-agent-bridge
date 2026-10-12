import os
import sys
import re
import argparse
import sqlite3
import pymupdf as fitz

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Отключаем вывод внутренних ошибок MuPDF
try:
    fitz.TOOLS.mupdf_display_errors(False)
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "..")))
    from config import BOOKS_DIR, DATABASE_PATH as DB_PATH
except Exception:
    DB_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "..", "books_index.db"))
    BOOKS_DIR = os.getenv("BOOKS_DIR", r"G:\Мой диск\книги")

TOOL_SPEC = {
    "name": "search_books",
    "description": "Мгновенный поиск инженерных решений, терминов и фрагментов по библиотеке PDF-книг через SQLite FTS5 индекс (или прямой поиск в PDF при отсутствии индекса).",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Ключевое слово или термин для поиска (например, 'iptables', 'tcp handshake', 'vlan', 'sql injection')"
            },
            "book_name": {
                "type": "string",
                "description": "Необязательно: фильтр по названию книги (например, 'linux', 'python', 'сети')"
            }
        },
        "required": ["query"]
    }
}

def clean_snippet(text):
    """Очищает сниппет от лишних переносов строк и мусорных пробелов."""
    if not text:
        return ""
    # Заменяем цепочки пробелов и переносов на один пробел
    cleaned = re.sub(r'[\r\n\t]+', ' ', text)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)
    return cleaned.strip()

def search_fts(query, book_filter=None, limit=5):
    """Выполняет быстрый поиск по индексу SQLite FTS5 с BM25-ранжированием."""
    if not os.path.exists(DB_PATH):
        return None

    try:
        con = sqlite3.connect(DB_PATH)
        # Проверяем, существует ли таблица и есть ли в ней записи
        cur = con.cursor()
        count = cur.execute("SELECT COUNT(*) FROM book_fts").fetchone()[0]
        if count == 0:
            con.close()
            return None

        # Формируем поисковое FTS-выражение с префиксами для каждого слова
        raw_words = re.findall(r'[\w\d]+', query.lower(), re.UNICODE)
        if not raw_words:
            con.close()
            return []

        # Поиск с префиксами: "слово1"* AND "слово2"*
        terms = [f'"{w}"*' for w in raw_words]
        match_expr = " AND ".join(terms)

        sql = """
            SELECT book, page, snippet(book_fts, 2, '【', '】', '...', 15) AS snip, bm25(book_fts) AS rank
            FROM book_fts
            WHERE book_fts MATCH ?
        """
        params = [match_expr]

        if book_filter:
            sql += " AND book LIKE ?"
            params.append(f"%{book_filter}%")

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = cur.execute(sql, params).fetchall()
        con.close()
        return rows
    except Exception as e:
        print(f"[!] Предупреждение FTS5: {e}")
        return None

def fallback_pdf_search(query, book_filter=None, max_books=2):
    """Резервный медленный поиск по сырым PDF (если база FTS еще не проиндексирована)."""
    if not os.path.exists(BOOKS_DIR):
        print(f"[!] Ошибка: Каталог книг не найден: {BOOKS_DIR}")
        return

    print(f"[*] База FTS5 пуста/недоступна. Выполняю прямой поиск '{query}' по файлам на Диске...")
    all_pdfs = []
    for root, _, files in os.walk(BOOKS_DIR):
        for f in files:
            if f.lower().endswith(".pdf"):
                all_pdfs.append(os.path.join(root, f))

    if book_filter:
        b_lower = book_filter.lower().strip()
        filtered = [p for p in all_pdfs if b_lower in os.path.basename(p).lower()]
        if filtered:
            all_pdfs = filtered

    found_books = 0
    q_lower = query.lower()

    for pdf_path in all_pdfs:
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            continue

        matches = []
        for pno in range(min(len(doc), 80)):
            try:
                text = doc[pno].get_text()
                if q_lower in text.lower():
                    clean_t = clean_snippet(text)
                    matches.append({"page": pno + 1, "snippet": clean_t[:250]})
                    if len(matches) >= 2:
                        break
            except Exception:
                continue
        doc.close()

        if matches:
            found_books += 1
            print(f"\n📖 [Книга]: {os.path.basename(pdf_path)}")
            for m in matches:
                print(f"   📍 Стр. {m['page']}: ... {m['snippet']} ...")
            if found_books >= max_books:
                break

    if found_books == 0:
        print(f"[-] Совпадений по запросу '{query}' не найдено.")

def execute_search(query, book_filter=None, limit=5):
    query = (query or "").strip()
    if not query:
        print("[-] Пустой поисковый запрос.")
        return

    # 1. Попытка высокоскоростного поиска через SQLite FTS5
    fts_results = search_fts(query, book_filter, limit=limit)

    if fts_results is not None:
        if not fts_results:
            print(f"[-] По запросу '{query}' совпадений в индексе FTS5 не найдено.")
            return

        print(f"[✓] Найдено совпадений в FTS5 индексе ({len(fts_results)}):")
        for r in fts_results:
            book_title, page, snippet, rank = r
            print(f"\n📖 [Книга]: {book_title}")
            print(f"   📍 Стр. {page}: {clean_snippet(snippet)}")
        return

    # 2. Резервный поиск по сырым файлам
    fallback_pdf_search(query, book_filter)

def main():
    parser = argparse.ArgumentParser(description="Поиск по инженерным PDF-книгам (SQLite FTS5 / Прямой)")
    parser.add_argument("pos_query", type=str, nargs="?", default="", help="Поисковый запрос (позиционный)")
    parser.add_argument("pos_book", type=str, nargs="?", default="", help="Фильтр по названию книги (позиционный)")
    parser.add_argument("--query", "-q", type=str, default=None, help="Поисковый запрос (флаг)")
    parser.add_argument("--book-name", "-b", type=str, default=None, help="Фильтр книги (флаг)")
    parser.add_argument("--limit", "-l", type=int, default=5, help="Количество результатов")
    args = parser.parse_args()

    final_query = args.query if args.query is not None else args.pos_query
    final_book = args.book_name if args.book_name is not None else args.pos_book

    execute_search(final_query, book_filter=final_book if final_book else None, limit=args.limit)

if __name__ == "__main__":
    main()
