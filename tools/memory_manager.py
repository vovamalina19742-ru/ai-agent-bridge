# -*- coding: utf-8 -*-
r"""
memory_manager.py — Менеджер долговременной памяти и знаний (Этап 3: Epistemic Hygiene Architecture).

Возможности:
1. Эпистемическая гигиена и категоризация:
   - fact (факты, константы, параметры сети, спецификации)
   - decision (архитектурные решения: «что сделали и почему»)
   - lesson (извлеченные уроки: «какая ошибка была, как починили»)
   - reference (ссылки на документацию, книги, стандарты)
2. Отслеживание происхождения и уверенности (Grounding & Confidence):
   - verification_status: 'verified' (подтверждено тестом/юзером), 'empirical' (проверено в рантайме), 'hypothesis' (догадка модели)
   - confidence: 0.1 .. 1.0 (гипотезы ограничены max 0.5 для защиты от самозагрязнения)
   - source: источник факта ('user', 'test', 'linter', 'book', 'model')
3. Гибридный поиск: FTS-подстроки + векторные эмбеддинги nomic-embed + RRF ранжирование с фильтрацией по уверенности.
"""

import os
import sys
import json
import uuid
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Импорт движка эмбеддингов и реестра сервисов
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
MEMORY_FILE = os.path.join(PROJECT_ROOT, "memory.json")
CACHE_FILE = os.path.join(PROJECT_ROOT, "memory_embeddings.json")

# Инициализируем хранилище памяти SQLite WAL и выполняем авто-миграцию
_store = EpistemicMemoryStore()
_store.migrate_from_json_files(PROJECT_ROOT)

TOOL_SPEC = {
    "name": "manage_memory",
    "description": "Эпистемическая долговременная память агента: структурированные факты, архитектурные решения и извлеченные уроки с отслеживанием источника и степени уверенности.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Действие: 'save', 'save_decision', 'save_lesson', 'recall', 'verify', 'list'"
            },
            "topic": {
                "type": "string",
                "description": "Тема или ключевое слово"
            },
            "content": {
                "type": "string",
                "description": "Текст факта или решения"
            },
            "memory_type": {
                "type": "string",
                "enum": ["fact", "decision", "lesson", "reference"],
                "description": "Тип знания (по умолчанию: fact)"
            },
            "verification_status": {
                "type": "string",
                "enum": ["verified", "empirical", "hypothesis"],
                "description": "Статус проверки (по умолчанию: verified для user/test, hypothesis для моделей)"
            },
            "confidence": {
                "type": "number",
                "description": "Степень уверенности (0.1 .. 1.0)"
            },
            "source": {
                "type": "string",
                "description": "Источник знания ('user', 'test', 'linter', 'book', 'model')"
            },
            "tags": {
                "type": "string",
                "description": "Теги через запятую"
            }
        },
        "required": ["action"]
    }
}


def normalize_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Приводит сырую запись к канонической структуре Epistemic Hygiene."""
    rec = dict(raw)
    if "id" not in rec:
        rec["id"] = "mem_" + uuid.uuid4().hex[:8]
    rec["topic"] = rec.get("topic", "").strip()
    rec["content"] = rec.get("content", "").strip()
    rec["memory_type"] = rec.get("memory_type", "fact")
    source = rec.get("source", "user")
    rec["source"] = source
    verification_status = rec.get("verification_status", "verified")

    # Если источник - модель или llm, по умолчанию только гипотеза с низкой уверенностью
    if str(source).lower() in ("model", "llm", "agent") and verification_status != "verified":
        verification_status = "hypothesis"

    rec["verification_status"] = verification_status

    # Защита от самозагрязнения: неподтвержденные гипотезы модели не могут иметь confidence > 0.5
    conf = float(rec.get("confidence", 1.0))
    if rec["verification_status"] == "hypothesis" and conf > 0.5:
        conf = 0.5
    rec["confidence"] = max(0.1, min(1.0, conf))

    if "created_at" not in rec:
        rec["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if "tags" not in rec or not isinstance(rec["tags"], list):
        raw_tags = rec.get("tags", "")
        if isinstance(raw_tags, str):
            rec["tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
        else:
            rec["tags"] = []
    return rec


def load_memory() -> List[Dict[str, Any]]:
    """Загружает память из SQLite WAL через DatabasePool с авто-нормализацией."""
    try:
        memories = _store.load_all_memories()
        if memories:
            return [normalize_record(item) for item in memories]
    except Exception:
        pass
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [normalize_record(item) for item in data]
        except Exception:
            return []
    return []


def save_memory(data: List[Dict[str, Any]]) -> bool:
    """Сохраняет память в SQLite WAL через DatabasePool (с синхронизацией JSON)."""
    try:
        _store.save_all_memories(data)
        try:
            tmp = MEMORY_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, MEMORY_FILE)
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[-] Ошибка сохранения памяти: {e}", file=sys.stderr)
        return False


def load_cache() -> Dict[str, Any]:
    """Загружает кэш эмбеддингов из SQLite WAL."""
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


def save_cache(cache: Dict[str, Any]) -> None:
    """Сохраняет кэш эмбеддингов в SQLite WAL."""
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


def get_or_create_embeddings(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Обеспечивает кэширование эмбеддингов фактов памяти с очисткой устаревших ключей."""
    cache = load_cache()
    items_to_embed = []
    keys_to_embed = []
    active_keys = set()

    for item in items:
        topic = item.get("topic", "")
        rec_id = item.get("id", "")
        ts = item.get("updated_at", item.get("created_at", ""))
        key = f"{rec_id}@@{topic}@@{ts}"
        active_keys.add(key)
        if key not in cache:
            tags_str = ", ".join(item.get("tags", []))
            text = f"[{item.get('memory_type')}] {topic}: {item.get('content', '')} (Теги: {tags_str})"
            items_to_embed.append(text)
            keys_to_embed.append(key)

    # Удаляем устаревшие ключи для обновленных записей
    active_ids = {item.get("id") for item in items if item.get("id")}
    pruned_cache = {k: v for k, v in cache.items() if (k.split("@@")[0] not in active_ids) or (k in active_keys)}

    if items_to_embed:
        embeddings = embedding_engine.get_embeddings_batch(items_to_embed)
        if embeddings and len(embeddings) == len(items_to_embed):
            for key, emb in zip(keys_to_embed, embeddings):
                pruned_cache[key] = emb
            save_cache(pruned_cache)
            return pruned_cache
    elif len(pruned_cache) != len(cache):
        save_cache(pruned_cache)

    return pruned_cache


def action_save(
    topic: str,
    content: str,
    memory_type: str = "fact",
    source: str = "user",
    verification_status: str = "verified",
    confidence: float = 1.0,
    tags: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Сохраняет знание в долговременную память с контролем эпистемической гигиены."""
    if not topic or not content:
        print("[-] Ошибка: Для сохранения укажите topic (тему) и content (содержимое).")
        return {}

    data = load_memory()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Проверяем, существует ли уже запись по теме и типу
    target_idx = None
    for idx, item in enumerate(data):
        if (
            item.get("topic", "").lower() == topic.lower()
            and item.get("memory_type", "fact") == memory_type
        ):
            target_idx = idx
            break

    # Защита от самозагрязнения
    if str(source).lower() in ("model", "llm", "agent") and verification_status != "verified":
        verification_status = "hypothesis"

    conf = float(confidence)
    if verification_status == "hypothesis" and conf > 0.5:
        conf = 0.5

    if target_idx is not None:
        rec = data[target_idx]
        rec["content"] = content
        rec["source"] = source
        rec["verification_status"] = verification_status
        rec["confidence"] = conf
        rec["updated_at"] = now_str
        if tags:
            rec["tags"] = sorted(list(set(rec.get("tags", []) + tags)))
    else:
        rec = {
            "id": "mem_" + uuid.uuid4().hex[:8],
            "topic": topic,
            "content": content,
            "memory_type": memory_type,
            "source": source,
            "verification_status": verification_status,
            "confidence": conf,
            "created_at": now_str,
            "tags": tags or []
        }
        data.append(rec)

    save_memory(data)
    get_or_create_embeddings(data)

    status_icon = "✅" if verification_status == "verified" else "🧪" if verification_status == "empirical" else "⚠️"
    print("[✓] Успешно сохранено в долговременную память!")
    print(f"📌 [{rec['memory_type'].upper()}] {topic} {status_icon} (Уверенность: {rec['confidence']:.0%}, Источник: {source})")
    print(f"📝 {content}")
    return rec


def action_save_decision(topic: str, decision: str, rationale: str = "", tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Хелпер сохранения архитектурного решения."""
    full_content = f"Решение: {decision}"
    if rationale:
        full_content += f"\nОбоснование: {rationale}"
    all_tags = (tags or []) + ["architecture", "decision"]
    return action_save(
        topic=topic,
        content=full_content,
        memory_type="decision",
        source="user",
        verification_status="verified",
        confidence=1.0,
        tags=all_tags
    )


def action_save_lesson(topic: str, mistake: str, fix: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Хелпер сохранения извлеченного урока / ошибки."""
    full_content = f"Проблема: {mistake}\nИсправление: {fix}"
    all_tags = (tags or []) + ["lesson", "gotcha"]
    return action_save(
        topic=topic,
        content=full_content,
        memory_type="lesson",
        source="test",
        verification_status="verified",
        confidence=1.0,
        tags=all_tags
    )


def action_verify(topic_or_id: str, proof: str = "") -> bool:
    """Верифицирует запись, повышая её уверенность до 100%."""
    data = load_memory()
    target = None
    target_clean = topic_or_id.strip().lower()

    for item in data:
        if item.get("id") == topic_or_id or item.get("topic", "").lower() == target_clean:
            target = item
            break

    if not target:
        print(f"[-] Запись '{topic_or_id}' не найдена в памяти.")
        return False

    target["verification_status"] = "verified"
    target["confidence"] = 1.0
    target["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if proof:
        target["content"] += f"\n[Верификация {target['updated_at']}]: {proof}"

    save_memory(data)
    get_or_create_embeddings(data)
    print(f"[✓] Запись '{target['topic']}' успешно верифицирована (Уверенность: 100%, статус: verified).")
    return True


def action_recall(
    topic: str,
    memory_type: Optional[str] = None,
    min_confidence: float = 0.4
) -> List[Dict[str, Any]]:
    """
    Гибридный семантический и ключевой поиск с фильтрацией по уверенности и типам.
    """
    data = load_memory()
    if not data:
        print("[-] Долговременная память пуста.")
        return []

    if not topic:
        action_list(memory_type=memory_type)
        return []

    t_clean = topic.strip()
    t_lower = t_clean.lower()

    # Фильтрация по минимальной уверенности и типу
    candidates = []
    for idx, item in enumerate(data):
        if item.get("confidence", 1.0) < min_confidence:
            continue
        if memory_type and item.get("memory_type") != memory_type:
            continue
        candidates.append((idx, item))

    if not candidates:
        print(f"[-] В памяти нет информации по запросу '{topic}' (с уверенностью >= {min_confidence:.0%}).")
        return []

    # 1. Поиск по подстрокам
    keyword_matches = []
    for idx, item in candidates:
        top = item.get("topic", "").lower()
        cnt = item.get("content", "").lower()
        tags = " ".join(item.get("tags", [])).lower()
        if t_lower in top or t_lower in cnt or t_lower in tags:
            keyword_matches.append(idx)

    # 2. Семантический поиск
    semantic_matches = []
    sim_scores = {}
    q_emb = embedding_engine.get_embedding(t_clean)
    if q_emb:
        cache = get_or_create_embeddings(data)
        for idx, item in candidates:
            rec_id = item.get("id", "")
            ts = item.get("updated_at", item.get("created_at", ""))
            key = f"{rec_id}@@{item.get('topic', '')}@@{ts}"
            doc_emb = cache.get(key)
            if doc_emb:
                sim = embedding_engine.cosine_similarity(q_emb, doc_emb)
                sim_scores[idx] = sim
                if sim >= 0.35:
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

    results = []
    if final_ids:
        print(f"[+] Найдено подтвержденных записей в памяти ({len(final_ids)}):")
        print("-" * 60)
        for idx in final_ids:
            m = data[idx]
            results.append(m)
            sim = sim_scores.get(idx)
            badges = []
            if m.get("verification_status") == "verified":
                badges.append("✅ VERIFIED")
            elif m.get("verification_status") == "empirical":
                badges.append("🧪 EMPIRICAL")
            else:
                badges.append("⚠️ HYPOTHESIS")

            badges.append(f"{m.get('confidence', 1.0):.0%}")

            if idx in keyword_matches:
                badges.append("точное слово")
            if sim is not None and sim >= 0.35:
                badges.append(f"семантика: {sim:.0%}")

            tag_str = f" [{', '.join(badges)}]"
            date_str = m.get("updated_at", m.get("created_at", "N/A"))
            print(f"📌 [{m.get('memory_type', 'fact').upper()}] {m.get('topic')}{tag_str} (от {date_str}, src: {m.get('source')}):")
            print(f"   {m.get('content')}\n")
    else:
        print(f"[-] В памяти нет совпадений по запросу '{topic}'.")
    return results


def action_list(memory_type: Optional[str] = None) -> None:
    data = load_memory()
    if memory_type:
        data = [d for d in data if d.get("memory_type") == memory_type]

    if not data:
        print("[-] Долговременная память пуста.")
        return

    print(f"[+] Всего записей в памяти: {len(data)}" + (f" [тип: {memory_type}]" if memory_type else ""))
    print("-" * 60)
    for i, item in enumerate(data, 1):
        status = "✅" if item.get("verification_status") == "verified" else "⚠️"
        conf = f"{item.get('confidence', 1.0):.0%}"
        m_type = item.get("memory_type", "fact").upper()
        print(f"{i}. [{m_type}] {item.get('topic')} {status} ({conf}) — {item.get('content')[:75]}...")
    print("-" * 60)


def main():
    parser = argparse.ArgumentParser(description="Менеджер эпистемической долговременной памяти (Epistemic Hygiene)")
    parser.add_argument("action", choices=["save", "save_decision", "save_lesson", "recall", "verify", "list"], help="Действие")
    parser.add_argument("topic", nargs="?", default="", help="Тема или поисковый запрос")
    parser.add_argument("content", nargs="?", default="", help="Текст для сохранения")
    parser.add_argument("--type", dest="memory_type", default=None, choices=["fact", "decision", "lesson", "reference"], help="Тип записи")
    parser.add_argument("--status", dest="verification_status", default="verified", choices=["verified", "empirical", "hypothesis"], help="Статус проверки")
    parser.add_argument("--confidence", type=float, default=1.0, help="Степень уверенности (0.1..1.0)")
    parser.add_argument("--source", default="user", help="Источник знания")
    parser.add_argument("--tags", default="", help="Теги через запятую")
    parser.add_argument("--min-confidence", type=float, default=0.4, help="Минимальная уверенность для поиска")
    args = parser.parse_args()

    tags_list = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None

    if args.action == "save":
        action_save(
            topic=args.topic,
            content=args.content,
            memory_type=args.memory_type or "fact",
            source=args.source,
            verification_status=args.verification_status,
            confidence=args.confidence,
            tags=tags_list
        )
    elif args.action == "save_decision":
        action_save_decision(topic=args.topic, decision=args.content, tags=tags_list)
    elif args.action == "save_lesson":
        action_save_lesson(topic=args.topic, mistake=args.topic, fix=args.content, tags=tags_list)
    elif args.action == "verify":
        action_verify(args.topic, proof=args.content)
    elif args.action == "recall":
        action_recall(args.topic, memory_type=args.memory_type, min_confidence=args.min_confidence)
    elif args.action == "list":
        action_list(memory_type=args.memory_type)


if __name__ == "__main__":
    main()
