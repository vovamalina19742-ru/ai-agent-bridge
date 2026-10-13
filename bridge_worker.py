# -*- coding: utf-8 -*-
"""
bridge_worker.py — Отказоустойчивый исполнитель очереди задач из bridge_queue.db (Ollama).

Архитектура надежности (Sprint 2 / Fault-Tolerant Queue & Lease):
1. Механизм аренды задач (Task Lease): атомарный захват задачи с установкой locked_by и locked_until (+120с).
2. Защита от состояния гонки (Race Condition): reclaim освобождает ТОЛЬКО задачи с истекшим locked_until.
3. Фоновый Heartbeat: во время выполнения задачи воркер продлевает аренду каждые 15 сек.
4. Структурированный контракт ExecutionResult: исключена маскировка ошибок моделей под статус 'done'.
5. Ограничение повторов (Circuit Breaker): задачи с превышением max_attempts переводятся в 'failed'.
"""

import sys
import os
import time
import socket
import sqlite3
import threading
from datetime import datetime
from typing import Optional, Dict, Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import QUEUE_DB_PATH
import bridge_mcp

QUEUE_DB = QUEUE_DB_PATH
WORKER_LOG = os.path.join(BASE_DIR, "bridge_worker.log")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
LEASE_SECONDS = 120
HEARTBEAT_INTERVAL = 15
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


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Worker {WORKER_ID}] {msg}"
    try:
        with open(WORKER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(QUEUE_DB, timeout=15)
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def claim_task(worker_id: str = WORKER_ID, lease_seconds: int = LEASE_SECONDS) -> Optional[sqlite3.Row]:
    """
    Атомарно забирает старейшую доступную задачу (queued или retry) с блокировкой по аренде (Lease).
    Исключает захват задачи другим воркером на время действия аренды.
    """
    con = get_db()
    con.row_factory = sqlite3.Row
    lease_offset = f"+{lease_seconds} seconds"
    try:
        cur = con.execute("""
            UPDATE tasks
            SET status='assigned',
                locked_by=?,
                locked_until=datetime('now', ?),
                heartbeat_at=datetime('now'),
                attempts=attempts + 1,
                updated_at=datetime('now')
            WHERE id = (
                SELECT id FROM tasks
                WHERE status IN ('queued', 'retry')
                ORDER BY id ASC
                LIMIT 1
            )
            RETURNING *
        """, (worker_id, lease_offset))
        row = cur.fetchone()
        con.commit()
        return row
    finally:
        con.close()


def reclaim_stale_tasks():
    """
    Безопасный возврат зависших задач без состояния гонки (Race Condition).
    Возвращает в queued ТОЛЬКО те задачи, у которых реально истек срок аренды (locked_until < now).
    Если число попыток превысило max_attempts, задача переводится в 'failed'.
    """
    con = get_db()
    now = now_str()
    try:
        # 1. Задачи с истекшим сроком аренды, превысившие лимит попыток -> failed
        cur_fail = con.execute("""
            UPDATE tasks
            SET status='failed',
                error='Превышено максимальное число попыток выполнения (lease timeout / авария воркера)',
                locked_by=NULL,
                locked_until=NULL,
                updated_at=?
            WHERE status IN ('assigned', 'running')
              AND locked_until IS NOT NULL
              AND locked_until < datetime('now')
              AND attempts >= max_attempts
        """, (now,))
        n_failed = cur_fail.rowcount

        # 2. Задачи с истекшим сроком аренды, у которых еще есть попытки -> queued
        cur_reclaim = con.execute("""
            UPDATE tasks
            SET status='queued',
                locked_by=NULL,
                locked_until=NULL,
                updated_at=?
            WHERE status IN ('assigned', 'running')
              AND locked_until IS NOT NULL
              AND locked_until < datetime('now')
              AND attempts < max_attempts
        """, (now,))
        n_reclaimed = cur_reclaim.rowcount

        con.commit()
        if n_failed:
            log(f"[!] Переведено в FAILED зависших задач (превышен лимит попыток): {n_failed}")
        if n_reclaimed:
            log(f"[✓] Безопасно возвращено в очередь зависших задач (истек lease): {n_reclaimed}")
    finally:
        con.close()


class TaskHeartbeatThread:
    """
    Фоновый поток продления аренды (Heartbeat).
    Периодически обновляет locked_until, пока задача исполняется в ReAct-цикле.
    """
    def __init__(self, task_id: int, worker_id: str, lease_seconds: int = LEASE_SECONDS, interval: int = HEARTBEAT_INTERVAL):
        self.task_id = task_id
        self.worker_id = worker_id
        self.lease_offset = f"+{lease_seconds} seconds"
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2.0)

    def _run(self):
        while not self.stop_event.wait(self.interval):
            try:
                con = get_db()
                con.execute("""
                    UPDATE tasks
                    SET heartbeat_at=datetime('now'),
                        locked_until=datetime('now', ?),
                        updated_at=datetime('now')
                    WHERE id=? AND locked_by=? AND status='running'
                """, (self.lease_offset, self.task_id, self.worker_id))
                con.commit()
                con.close()
            except Exception:
                pass


COMPLEX_MARKERS = ("напиши", "проанализ", "реализуй", "исправь", "рефактор", "сгенерируй",
                   "write", "analyz", "refactor", "code", "debug")


def route_model(hint: str, task: str = "") -> str:
    """Защитный маршрутизатор моделей."""
    h = (hint or "").strip().lower()
    if h == "fast":
        t = (task or "").lower()
        if len(task or "") > 300 or any(m in t for m in COMPLEX_MARKERS):
            log("hint=fast, но задача выглядит нетривиальной — маршрут повышен до qwen2.5-coder:7b")
            return "qwen2.5-coder:7b"
    return MODEL_ROUTES.get(h, "qwen2.5-coder:7b")


def process_task(row: sqlite3.Row):
    """
    Исполняет задачу через ReAct-оркестратор со строгим контрактом ExecutionResult.
    """
    import orchestrator

    tid = row["id"]
    model = route_model(row["worker_hint"], row["task"])
    orchestrator.MODEL_NAME = model
    attempts = row["attempts"]
    max_attempts = row["max_attempts"] or 3

    log(f"#{tid} [Попытка {attempts}/{max_attempts}] взята в работу, модель={model}, задача: {row['task'][:80]}")

    # Переводим в статус 'running'
    con = get_db()
    con.execute("UPDATE tasks SET status='running', updated_at=datetime('now') WHERE id=?", (tid,))
    con.commit()
    con.close()

    # Запускаем фоновый Heartbeat
    heartbeat = TaskHeartbeatThread(tid, WORKER_ID)
    heartbeat.start()

    system_prompt = (
        orchestrator.load_agents_rules()
        + "\n\nРЕЖИМ ВОРКЕРА ОЧЕРЕДИ: ты выполняешь одну задачу, поставленную диспетчером. "
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
        # Вызов с возвратом типизированного конверта ExecutionResult (Sprint 2)
        exec_res = orchestrator.execute_task(messages, ollama_tools, tools_map, max_iterations=6)
    except Exception as e:
        from services import ExecutionResult
        exec_res = ExecutionResult.fail(f"Исключение при выполнении задачи: {type(e).__name__}: {e}", is_retryable=False)
    finally:
        heartbeat.stop()

    con = get_db()
    try:
        if exec_res.success:
            summary = (exec_res.result or "").strip()
            if len(summary) > MAX_SUMMARY_CHARS:
                summary = summary[:MAX_SUMMARY_CHARS] + " [...обрезано]"

            con.execute("""
                UPDATE tasks
                SET status='done',
                    result_summary=?,
                    error='',
                    locked_by=NULL,
                    locked_until=NULL,
                    updated_at=datetime('now')
                WHERE id=?
            """, (summary, tid))
            con.commit()
            log(f"#{tid} DONE: {summary[:100]}")
        else:
            err_msg = exec_res.error or "Неизвестная ошибка выполнения"
            if exec_res.is_retryable and attempts < max_attempts:
                # Временный сбой (например таймаут сети) — отправляем на повтор
                con.execute("""
                    UPDATE tasks
                    SET status='retry',
                        error=?,
                        locked_by=NULL,
                        locked_until=NULL,
                        updated_at=datetime('now')
                    WHERE id=?
                """, (f"[RETRYABLE {attempts}/{max_attempts}] {err_msg}", tid))
                con.commit()
                log(f"#{tid} RETRY ({attempts}/{max_attempts}): {err_msg}")
            else:
                # Фатальная ошибка или превышение попыток -> FAILED
                con.execute("""
                    UPDATE tasks
                    SET status='failed',
                        error=?,
                        result_summary='',
                        locked_by=NULL,
                        locked_until=NULL,
                        updated_at=datetime('now')
                    WHERE id=?
                """, (err_msg, tid))
                con.commit()
                log(f"#{tid} FAILED: {err_msg}")
    finally:
        con.close()


def main():
    once = "--once" in sys.argv
    log(f"Воркер запущен (ID: {WORKER_ID}, режим: {'once' if once else 'непрерывный'})")

    # Гарантируем схему БД и миграцию полей lease
    bridge_mcp.ensure_db()

    # Безопасный возврат зависших задач с истекшим lease
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
