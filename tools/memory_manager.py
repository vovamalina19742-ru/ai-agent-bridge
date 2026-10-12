import os
import sys
import json
import argparse
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Импорт движка эмбеддингов
try:
    import embedding_engine
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    import embedding_engine

TOOL_SPEC = {
    "name": "manage_memory",
    "description": "Долговременная память агента. Позволяет сохранять важные знания, выводы и архитектурные решения навсегда, а также гибридно (семантически и по ключевым словам) вспоминать их по теме.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Действие: 'save' (сохранить знание/факт), 'recall' (найти/вспомнить по теме), 'list' (показать список тем)"
            },
            "topic": {
                "type": "string",
                "description": "Тема или ключевое слово (например: 'Ruflo', 'TencentDB', 'IP роутера')"
            },
            "content": {
                "type": "string",
                "description": "Текст факта, выжимки или решения для сохранения (только при action='save')"
            }
        },
        "required": ["action"]
    }
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
MEMORY_FILE = os.path.join(PROJECT_ROOT, "memory.json")
CACHE_FILE = os.path.join(PROJECT_ROOT, "memory_embeddings.json")

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_memory(data):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache):
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass

def get_or_create_embeddings(items):
    """Обеспечивает кэширование эмбеддингов фактов памяти."""
    cache = load_cache()
    updated = False
    items_to_embed = []
    keys_to_embed = []

    for item in items:
        topic = item.get("topic", "")
        ts = item.get("updated_at", item.get("created_at", ""))
        key = f"{topic}@@{ts}"
        if key not in cache:
            text = f"{topic}: {item.get('content', '')}"
            items_to_embed.append(text)
            keys_to_embed.append(key)

    if items_to_embed:
        embeddings = embedding_engine.get_embeddings_batch(items_to_embed)
        if embeddings and len(embeddings) == len(items_to_embed):
            for key, emb in zip(keys_to_embed, embeddings):
                cache[key] = emb
            updated = True

    if updated:
        save_cache(cache)
    return cache

def action_save(topic, content):
    if not topic or not content:
        print("[-] Ошибка: Для сохранения укажите topic (тему) и content (текст знания).")
        return

    data = load_memory()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    updated = False
    for item in data:
        if item.get("topic", "").lower() == topic.lower():
            item["content"] = content
            item["updated_at"] = now_str
            updated = True
            break

    if not updated:
        data.append({
            "topic": topic,
            "content": content,
            "created_at": now_str
        })

    save_memory(data)
    # Сразу обновляем эмбеддинг для новой записи
    get_or_create_embeddings(data)

    print(f"[✓] Успешно сохранено в долговременную память!")
    print(f"📌 Тема: {topic}")
    print(f"📝 Содержимое: {content}")

def action_recall(topic):
    data = load_memory()
    if not data:
        print("[-] Долговременная память пуста.")
        return

    if not topic:
        action_list()
        return

    t_clean = topic.strip()
    t_lower = t_clean.lower()

    # 1. Поиск по подстрокам (точные слова)
    keyword_matches = []
    for idx, item in enumerate(data):
        top = item.get("topic", "").lower()
        cnt = item.get("content", "").lower()
        if t_lower in top or t_lower in cnt:
            keyword_matches.append(idx)

    # 2. Семантический поиск
    semantic_matches = []
    sim_scores = {}
    q_emb = embedding_engine.get_embedding(t_clean)
    if q_emb:
        cache = get_or_create_embeddings(data)
        for idx, item in enumerate(data):
            ts = item.get("updated_at", item.get("created_at", ""))
            key = f"{item.get('topic', '')}@@{ts}"
            doc_emb = cache.get(key)
            if doc_emb:
                sim = embedding_engine.cosine_similarity(q_emb, doc_emb)
                sim_scores[idx] = sim
                if sim >= 0.40:
                    semantic_matches.append((idx, sim))
        semantic_matches.sort(key=lambda x: x[1], reverse=True)
        sem_ranked_ids = [idx for idx, _ in semantic_matches]
    else:
        sem_ranked_ids = []

    # 3. RRF фьюжн
    if sem_ranked_ids:
        rrf_ranks = embedding_engine.rrf_score(keyword_matches, sem_ranked_ids, k=60)
        final_ids = [item_id for item_id, _ in rrf_ranks]
    else:
        final_ids = keyword_matches

    if final_ids:
        print(f"[+] Найдено записей в памяти ({len(final_ids)}):")
        print("-" * 50)
        for idx in final_ids:
            m = data[idx]
            sim = sim_scores.get(idx)
            tags = []
            if idx in keyword_matches:
                tags.append("точное слово")
            if sim is not None and sim >= 0.40:
                tags.append(f"семантика: {sim:.0%}")
            tag_str = f" [{', '.join(tags)}]" if tags else ""

            date_str = m.get('updated_at', m.get('created_at', 'N/A'))
            print(f"📌 [{m.get('topic')}]{tag_str} (от {date_str}):")
            print(f"   {m.get('content')}\n")
    else:
        print(f"[-] В памяти нет информации по запросу '{topic}'.")

def action_list():
    data = load_memory()
    if not data:
        print("[-] Долговременная память пуста.")
        return

    print(f"[+] Всего записей в памяти: {len(data)}")
    print("-" * 50)
    for i, item in enumerate(data, 1):
        print(f"{i}. {item.get('topic')} — {item.get('content')[:80]}...")
    print("-" * 50)

def main():
    parser = argparse.ArgumentParser(description="Менеджер долговременной памяти агента (Гибридный семантический и ключевой поиск)")
    parser.add_argument("action", choices=["save", "recall", "list"], help="Действие")
    parser.add_argument("topic", nargs="?", default="", help="Тема или поисковый запрос")
    parser.add_argument("content", nargs="?", default="", help="Текст для сохранения")
    args = parser.parse_args()

    if args.action == "save":
        action_save(args.topic, args.content)
    elif args.action == "recall":
        action_recall(args.topic)
    elif args.action == "list":
        action_list()

if __name__ == "__main__":
    main()
