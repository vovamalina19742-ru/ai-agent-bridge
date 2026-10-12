import os
import sys
import json
import math
import requests

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
DEFAULT_EMBED_MODEL = "nomic-embed-text"

def cosine_similarity(v1, v2):
    """Вычисляет косинусное сходство между двумя векторами (чистый Python, без numpy)."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        norm1 += a * a
        norm2 += b * b
    if norm1 <= 0.0 or norm2 <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm1) * math.sqrt(norm2))

def get_embeddings_batch(texts, model=DEFAULT_EMBED_MODEL, timeout=30):
    """
    Получает эмбеддинги для списка текстов через Ollama API /api/embed.
    Возвращает список векторов (списков float) или None при ошибке/недоступности.
    """
    if not texts:
        return []
    try:
        resp = requests.post(
            OLLAMA_EMBED_URL,
            json={"model": model, "input": texts},
            timeout=timeout
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("embeddings", [])
    except Exception:
        pass
    return None

def get_embedding(text, model=DEFAULT_EMBED_MODEL, timeout=15):
    """Получает вектор для одного текста."""
    res = get_embeddings_batch([text], model=model, timeout=timeout)
    if res and len(res) > 0:
        return res[0]
    return None

def rrf_score(rank_list_a, rank_list_b, k=60):
    """
    Reciprocal Rank Fusion (RRF) для объединения списков ранжирования:
    score(d) = 1/(k + rank_a) + 1/(k + rank_b)
    """
    scores = {}
    for rank, item_id in enumerate(rank_list_a, 1):
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    for rank, item_id in enumerate(rank_list_b, 1):
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
