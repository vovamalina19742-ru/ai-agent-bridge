import os
import sys
import json
import urllib.request
import urllib.error
import argparse

# UTF-8 вывод
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOOL_SPEC = {
    "name": "consult_senior_ai",
    "description": "Консультация со Старшим ИИ-Оркестратором (Antigravity / Gemini). Используется для сложных архитектурных вопросов, проверки кода или когда локальной модели нужна помощь.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Вопрос или задача для Старшего ИИ"
            }
        },
        "required": ["query"]
    }
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
PARENT_ROOT = r"d:\Создание программ"

def find_gemini_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    
    # Ищем в возможных расположениях .env
    env_paths = [
        os.path.join(PARENT_ROOT, "scripts", ".env"),
        os.path.join(PARENT_ROOT, ".env"),
        os.path.join(PROJECT_ROOT, ".env")
    ]
    for p in env_paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GEMINI_API_KEY="):
                        return line.strip().split("=", 1)[1].strip("\"' ")
    return None

def consult_senior(query):
    api_key = find_gemini_api_key()
    
    # Записываем запрос в общий лог моста задач (Bridge Journal)
    bridge_log = os.path.join(PROJECT_ROOT, "bridge_tasks.json")
    try:
        history = []
        if os.path.exists(bridge_log):
            with open(bridge_log, "r", encoding="utf-8") as f:
                history = json.load(f)
        history.append({"query": query, "status": "sent"})
        with open(bridge_log, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    if not api_key:
        print("[Senior AI Bridge] Запрос зарегистрирован в bridge_tasks.json, но GEMINI_API_KEY не задан в .env.")
        print(f"Вопрос передан в буфер: {query}")
        return

    # Отправляем запрос в Gemini Flash-Lite
    model = "gemini-3.1-flash-lite"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"Ты — Старший ИИ-Оркестратор. Локальный агент qwen2.5-coder обратился к тебе за консультацией:\n\n{query}\n\nДай четкий, инженерный ответ на русском языке."}]
            }
        ]
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ans = data["candidates"][0]["content"]["parts"][0]["text"]
            print(f"[Ответ Старшего ИИ]:\n{ans.strip()}")
    except Exception as e:
        print(f"[!] Ошибка связи со Старшим ИИ: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Связь со Старшим ИИ-Оркестратором")
    parser.add_argument("query", type=str, help="Текст вопроса для Старшего ИИ")
    args = parser.parse_args()
    consult_senior(args.query)
