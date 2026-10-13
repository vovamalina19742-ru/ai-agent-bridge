import os
import sys
import json
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Импорт локального движка эмбеддингов и реестра сервисов
try:
    import embedding_engine
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    import embedding_engine

try:
    from services import EpistemicMemoryStore
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from services import EpistemicMemoryStore

TOOL_SPEC = {
    "name": "search_saved_links",
    "description": "Гибридный семантический и ключевой поиск по ранее изученным веб-ссылкам, статьям и GitHub-репозиториям в базе saved_links.json (находит релевантное даже при несовпадении точных слов).",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Поисковый запрос, тема, концепция или фрагмент названия (например: 'агенты swarms', 'быстрый инференс deepseek', 'claude code')"
            }
        },
        "required": ["query"]
    }
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
SAVED_LINKS_FILE = os.path.join(PROJECT_ROOT, "saved_links.json")
CACHE_FILE = os.path.join(PROJECT_ROOT, "links_embeddings.json")

_store = EpistemicMemoryStore()
_store.migrate_from_json_files(PROJECT_ROOT)

def load_saved_links():
    """Загружает сохраненные ссылки из SQLite WAL через DatabasePool."""
    try:
        links = _store.load_saved_links()
        if links:
            return links
    except Exception:
        pass
    if os.path.exists(SAVED_LINKS_FILE):
        try:
            with open(SAVED_LINKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Ошибка чтения saved_links.json: {e}")
            return []
    return []

def load_cache():
    """Загружает кэш эмбеддингов ссылок из SQLite WAL."""
    try:
        cache = _store.load_embedding_cache()
        if cache:
            return cache
    except Exception:
        pass
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache):
    """Сохраняет кэш эмбеддингов ссылок в SQLite WAL."""
    try:
        _store.save_embedding_cache_batch(cache)
        try:
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
            os.replace(tmp, CACHE_FILE)
        except Exception:
            pass
    except Exception:
        pass

def get_or_create_embeddings(links):
    """Обеспечивает наличие кэшированных эмбеддингов для всех сохраненных ссылок."""
    cache = load_cache()
    updated = False
    items_to_embed = []
    indices_to_embed = []

    for idx, item in enumerate(links):
        url = item.get("url", "")
        ts = item.get("timestamp", "")
        key = f"{url}@@{ts}"
        if key not in cache:
            text = f"{item.get('title', '')}\n{item.get('summary', '')}"
            items_to_embed.append(text)
            indices_to_embed.append((idx, key))

    if items_to_embed:
        embeddings = embedding_engine.get_embeddings_batch(items_to_embed)
        if embeddings and len(embeddings) == len(items_to_embed):
            for (_, key), emb in zip(indices_to_embed, embeddings):
                cache[key] = emb
            updated = True

    if updated:
        save_cache(cache)

    return cache

def search_links(query):
    links = load_saved_links()
    if not links:
        print("[-] База сохраненных ссылок пуста.")
        return

    q_clean = (query or "").strip()
    if not q_clean or q_clean.lower() in ("all", "list", "*"):
        print(f"[+] Всего в базе сохраненных ссылок: {len(links)}")
        print("=" * 60)
        for i, m in enumerate(links, 1):
            print(f"{i}. 📌 {m.get('title', 'Без названия')}")
            print(f"   🔗 URL: {m.get('url')}")
            print(f"   📅 Дата: {m.get('timestamp', 'N/A')}")
            snippet = m.get('summary', '').strip()
            if snippet:
                print(f"   📝 Выжимка: {snippet[:200]}...")
            print("-" * 60)
        return

    # 1. Ключевой поиск (подстроки)
    q_lower = q_clean.lower()
    keyword_matches = []
    for idx, item in enumerate(links):
        u = item.get("url", "").lower()
        t = item.get("title", "").lower()
        s = item.get("summary", "").lower()
        if q_lower in u or q_lower in t or q_lower in s:
            keyword_matches.append(idx)

    # 2. Семантический поиск (Ollama nomic-embed-text)
    semantic_matches = []
    q_emb = embedding_engine.get_embedding(q_clean)
    sim_scores = {}

    if q_emb:
        cache = get_or_create_embeddings(links)
        for idx, item in enumerate(links):
            key = f"{item.get('url', '')}@@{item.get('timestamp', '')}"
            doc_emb = cache.get(key)
            if doc_emb:
                sim = embedding_engine.cosine_similarity(q_emb, doc_emb)
                sim_scores[idx] = sim
                if sim >= 0.40: # порог семантической близости
                    semantic_matches.append((idx, sim))
        # Сортируем по убыванию косинусного сходства
        semantic_matches.sort(key=lambda x: x[1], reverse=True)
        sem_ranked_ids = [idx for idx, _ in semantic_matches]
    else:
        sem_ranked_ids = []

    # 3. Гибридное слияние рангов (RRF)
    if sem_ranked_ids:
        rrf_ranks = embedding_engine.rrf_score(keyword_matches, sem_ranked_ids, k=60)
        final_ids = [item_id for item_id, score in rrf_ranks]
    else:
        final_ids = keyword_matches

    if final_ids:
        print(f"[✓] Найдено релевантных ссылок ({len(final_ids)}):")
        print("=" * 60)
        for rank, idx in enumerate(final_ids[:5], 1):
            m = links[idx]
            sim = sim_scores.get(idx)
            match_tags = []
            if idx in keyword_matches:
                match_tags.append("точное слово")
            if sim is not None and sim >= 0.40:
                match_tags.append(f"семантика: {sim:.0%}")

            tag_str = f" [{', '.join(match_tags)}]" if match_tags else ""
            print(f"{rank}. 📌 {m.get('title', 'Без названия')}{tag_str}")
            print(f"   🔗 URL: {m.get('url')}")
            print(f"   📅 Дата: {m.get('timestamp', 'N/A')}")
            snippet = m.get('summary', '').strip()
            if snippet:
                print(f"   📝 Выжимка: {snippet[:260]}...")
            print("-" * 60)
    else:
        print(f"[-] По запросу '{query}' совпадений не найдено (ни по ключевым словам, ни семантически).")

def main():
    parser = argparse.ArgumentParser(description="Семантический и ключевой поиск по сохраненным веб-ссылкам")
    parser.add_argument("query", nargs="?", default="", help="Поисковый запрос")
    args = parser.parse_args()

    search_links(args.query)

if __name__ == "__main__":
    main()
