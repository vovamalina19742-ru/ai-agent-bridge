# -*- coding: utf-8 -*-
"""
bridge_worker.py — исполнитель очереди задач из bridge_queue.db (Ollama, бесплатно).

Забирает задачи со статусом 'queued' (FIFO), маршрутизирует модель по worker_hint,
выполняет через ReAct-цикл orchestrator.py (с инструментами), кладет result_summary.

Запуск:
  python bridge_worker.py          # непрерывный режим (Ctrl+C для остановки)
  python bridge_worker.py --once   # обработать одну задачу и выйти (для теста)

Подсказки модели (worker_hint из task_assign):
  coder / (пусто) -> qwen2.5-coder:7b   — код, правки, анализ репозиториев
  reason          -> deepseek-r1:8b     — рассуждения, нестандартные задачи
  summary / 3b    -> qwen2.5:3b         — выжимки, конспекты, суммаризация
  fast            -> qwen2.5-coder:1.5b — классификация, простые операции
"""
import sys
import os
import time
import sqlite3
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import QUEUE_DB_PATH

QUEUE_DB = QUEUE_DB_PATH
WORKER_LOG = os.path.join(BASE_DIR, "bridge_worker.log")

MAX_SUMMARY_CHARS = 2000
POLL_SECONDS = 5

MODEL_ROUTES = {
    "coder": "qwen2.5-coder:7b",
    "": "qwen2.5-coder:7b",
    "reason": "deepseek-r1:8b",
    "summary": "qwen2.5:3b",
    "3b": "qwen2.5:3b",
    "fast": "qwen2.5-coder:1.5b",
}


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(WORKER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    con = sqlite3.connect(QUEUE_DB, timeout=15)
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def claim_task():
    """Атомарно забирает старейшую queued-задачу (SQLite RETURNING)."""
    con = get_db()
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("""
            UPDATE tasks SET status='assigned', updated_at=?
            WHERE id = (SELECT id FROM tasks WHERE status='queued' ORDER BY id LIMIT 1)
            RETURNING *
        """, (now_str(),))
        row = cur.fetchone()
        con.commit()
        return row
    finally:
        con.close()


COMPLEX_MARKERS = ("напиши", "проанализ", "реализуй", "исправь", "рефактор", "сгенерируй",
                   "write", "analyz", "refactor", "code", "debug")


def route_model(hint, task=""):
    """Защитный маршрутизатор: 'fast' (1.5b) — только для тривиальных задач.
    Длинные задачи и задачи с маркерами сложности автоматически повышаются до 7b."""
    hint = (hint or "").strip().lower()
    if hint == "fast":
        t = (task or "").lower()
        if len(task or "") > 300 or any(m in t for m in COMPLEX_MARKERS):
            log("hint=fast, но задача выглядит нетривиальной — маршрут повышен до qwen2.5-coder:7b")
            return "qwen2.5-coder:7b"
    return MODEL_ROUTES.get(hint, "qwen2.5-coder:7b")


def process_task(row):
    import orchestrator  # реестр инструментов и ReAct-цикл проекта

    tid = row["id"]
    model = route_model(row["worker_hint"], row["task"])
    orchestrator.MODEL_NAME = model
    log(f"#{tid} взята в работу, модель={model}, задача: {row['task'][:80]}")

    system_prompt = (
        orchestrator.load_agents_rules()
        + "\n\nРЕЖИМ ВОркЕРА ОЧЕРЕДИ: ты выполняешь одну задачу, поставленную диспетчером. "
          "Используй инструменты, когда они нужны для дела. Финальный ответ — готовая выжимка результата "
          "(не более 300 слов), пригодная для проверки диспетчером без чтения сырых данных."
    )
    user_text = row["task"]
    if row["acceptance"]:
        user_text += f"\n\nКритерий приемки: {row['acceptance']}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    try:
        ollama_tools, tools_map = orchestrator.load_tools_registry()
        answer = orchestrator.chat_step(messages, ollama_tools, tools_map, max_iterations=6)
    except Exception as e:
        con = get_db()
        con.execute("UPDATE tasks SET status='failed', error=?, updated_at=? WHERE id=?",
                    (f"{type(e).__name__}: {e}", now_str(), tid))
        con.commit()
        con.close()
        log(f"#{tid} FAILED: {e}")
        return

    summary = (answer or "").strip()
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + " [...обрезано]"

    con = get_db()
    con.execute("UPDATE tasks SET status='done', result_summary=?, updated_at=? WHERE id=?",
                (summary, now_str(), tid))
    con.commit()
    con.close()
    log(f"#{tid} DONE: {summary[:100]}")


def reclaim_stale_tasks():
    """Возврат зависших assigned-задач в очередь (падение воркера после claim)."""
    con = get_db()
    cur = con.execute("UPDATE tasks SET status='queued', updated_at=? WHERE status='assigned'", (now_str(),))
    con.commit()
    n = cur.rowcount
    con.close()
    if n:
        log(f"Возвращено в очередь зависших задач: {n}")


def main():
    once = "--once" in sys.argv
    log(f"Воркер запущен (режим: {'once' if once else 'непрерывный'})")
    reclaim_stale_tasks()
    try:
        while True:
            row = claim_task()
            if row is None:
                if once:
                    log("Очередь пуста, выход (--once).")
                    return
                time.sleep(POLL_SECONDS)
                continue
            process_task(row)
            if once:
                return
    except KeyboardInterrupt:
        log("Воркер остановлен пользователем.")


if __name__ == "__main__":
    main()
