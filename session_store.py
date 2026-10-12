import os
import sys
import json
import time

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")

def save_session(history, model, summary=None, summary_upto=0):
    """
    Атомарно сохраняет состояние сессии диалога в session.json через временный файл.
    Исключает повреждение файла при аварийном завершении или сбое питания.
    """
    if not history:
        return False

    data = {
        "version": 1,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": model,
        "history": history,
        "summary": summary,
        "summary_upto": summary_upto
    }
    
    tmp_file = SESSION_FILE + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, SESSION_FILE)
        return True
    except Exception as e:
        print(f"[!] Ошибка сохранения session.json: {e}")
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass
        return False

def load_session():
    """
    Загружает сессию из session.json.
    Возвращает dict с данными сессии или None в случае отсутствия/ошибки.
    """
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "history" in data:
            return data
    except Exception as e:
        print(f"[!] Ошибка чтения session.json: {e}")
    return None

def clear_session():
    """Удаляет файл сохраненной сессии."""
    if os.path.exists(SESSION_FILE):
        try:
            os.remove(SESSION_FILE)
            return True
        except Exception as e:
            print(f"[!] Ошибка удаления session.json: {e}")
    return False

def summarize_history(dialogue_messages, summarizer_model="qwen2.5:3b", timeout=20):
    """
    Опциональная суммаризация длинного диалога локальной быстрой моделью (qwen2.5:3b).
    Сжимает историю в компактный список фактов без потери сути.
    """
    if len(dialogue_messages) < 10:
        return None

    try:
        import requests
        lines = []
        for m in dialogue_messages:
            role = m.get("role", "")
            content = str(m.get("content", "")).strip()
            if role in ("user", "assistant") and content:
                # Обрезаем сверхдлинный контент страниц
                if len(content) > 600:
                    content = content[:600] + "... [сокращено]"
                lines.append(f"{role.upper()}: {content}")
        
        text_to_summarize = "\n".join(lines[-20:])
        prompt = (
            "Сделай краткую выжимку ключевых тем, принятых решений и фактов из следующего диалога "
            "(до 5-7 маркированных пунктов). Пиши только выжимку без вступлений:\n\n"
            f"{text_to_summarize}"
        )

        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": summarizer_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            },
            timeout=timeout
        )
        if resp.status_code == 200:
            summary_text = resp.json().get("message", {}).get("content", "").strip()
            if summary_text:
                return summary_text
    except Exception:
        pass
    return None

def restore_conversation(session_data, current_system_prompt, max_history=24):
    """
    Корректно восстанавливает историю диалога:
    1. Заменяет системный промпт на актуальный (правила/память могли обновиться).
    2. Вставляет выжимку (summary), если она была сформирована ранее.
    3. Применяет тримминг к последним N репликам для сохранения бюджета контекста 32k.
    """
    raw_history = session_data.get("history", [])
    # Отсекаем старый системный промпт
    dialogue_messages = [m for m in raw_history if m.get("role") != "system"]

    # Берем последние max_history сообщений диалога
    trimmed_messages = dialogue_messages[-max_history:] if len(dialogue_messages) > max_history else dialogue_messages

    restored = [{"role": "system", "content": current_system_prompt}]
    
    summary = session_data.get("summary")
    if summary:
        restored.append({
            "role": "system",
            "content": f"Выжимка предыдущей части диалога:\n{summary}"
        })

    restored.extend(trimmed_messages)
    return restored

