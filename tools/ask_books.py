# -*- coding: utf-8 -*-
"""
ask_books.py — RAG-ответ по библиотеке книг (FTS5 + локальная Ollama qwen2.5-coder / qwen2.5).

Алгоритм:
1. Вопрос пользователя -> извлечение ключевых терминов.
2. Полнотекстовый поиск FTS5 по books_index.db -> выбор топ-5 релевантных страниц с контекстом.
3. Формирование компактного системного промпта с цитатами.
4. Запрос к локальной Ollama (чистый Python urllib, 0 платных токенов).
5. Итоговый ответ строго по цитатам с указанием источника: [Книга, стр. N].
"""
import os
import sys
import re
import json
import sqlite3
import argparse
import urllib.request

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

try:
    from config import DATABASE_PATH, OLLAMA_BASE_URL, DEFAULT_MODEL
except Exception:
    DATABASE_PATH = os.path.join(BASE_DIR, "books_index.db")
    OLLAMA_BASE_URL = "http://localhost:11434"
    DEFAULT_MODEL = "qwen2.5-coder:7b"

TOOL_SPEC = {
    "name": "ask_books",
    "description": "Умный RAG-ответ на вопрос по библиотеке книг через FTS5-поиск и локальную Ollama с обязательными ссылками на страницы.",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Вопрос пользователя по инженерной/технической теме",
            },
            "book_filter": {
                "type": "string",
                "description": "Фильтр по названию книги (опционально)",
            },
        },
        "required": ["question"],
    },
}


def search_fts_snippets(query, book_filter=None, limit=5):
    if not os.path.exists(DATABASE_PATH):
        return []

    try:
        con = sqlite3.connect(DATABASE_PATH)
        cur = con.cursor()

        raw_words = re.findall(r"[\w\d]+", query.lower(), re.UNICODE)
        # Отфильтровываем слишком короткие слова-паразиты
        stopwords = {"как", "что", "где", "когда", "почему", "зачем", "это", "для", "или", "при", "про", "the", "and", "how", "what"}
        meaningful = [w for w in raw_words if len(w) >= 3 and w not in stopwords]
        if not meaningful:
            meaningful = raw_words[:3]

        if not meaningful:
            con.close()
            return []

        terms = [f'"{w}"*' for w in meaningful]
        match_expr = " OR ".join(terms)

        sql = """
            SELECT book, page, snippet(book_fts, 2, '', '', '...', 35) AS snip, bm25(book_fts) AS rank
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
        sys.stderr.write(f"[!] FTS5 ошибка: {e}\n")
        return []


def query_ollama(prompt, model=DEFAULT_MODEL):
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 512,
        },
    }
    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=40.0) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            return res_json.get("response", "").strip()
    except Exception as e:
        return f"[!] Ошибка вызова Ollama ({model}): {e}"


def ask_library(question, book_filter=None):
    snippets = search_fts_snippets(question, book_filter=book_filter, limit=5)

    if not snippets:
        return f"[-] По запросу '{question}' в библиотеке книг не найдено точных совпадений в FTS5-индексе."

    context_blocks = []
    for book, page, snip, _ in snippets:
        book_short = os.path.basename(book)
        context_blocks.append(f"ИСТОЧНИК: [{book_short}, стр. {page}]\nФРАГМЕНТ: {snip.strip()}\n")

    context_text = "\n".join(context_blocks)

    system_prompt = f"""Ты — инженерный ассистент технической библиотеки.
Ответь на вопрос пользователя СТРОГО на основе приведенных фрагментов из книг.
Обязательно указывай ссылки на источники в формате [Книга, стр. N].
Если во фрагментах нет точного ответа на вопрос, честно напиши: "В найденных фрагментах книг информации недостаточно."

КОНТЕКСТ ИЗ БИБЛИОТЕКИ:
{context_text}

ВОПРОС: {question}

ОТВЕТ:"""

    answer = query_ollama(system_prompt)
    return answer


def main():
    parser = argparse.ArgumentParser(description="Спроси библиотеку: RAG-ответ по книгам")
    parser.add_argument("question", help="Вопрос")
    parser.add_argument("--book", "-b", default=None, help="Фильтр по имени книги")
    args = parser.parse_args()

    res = ask_library(args.question, book_filter=args.book)
    print(res)


if __name__ == "__main__":
    main()
