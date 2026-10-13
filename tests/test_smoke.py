import os
import sys
import sqlite3
import unittest

# Добавляем корень проекта в путь
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

class TestAIAgentSmoke(unittest.TestCase):

    def test_safety_guardrails(self):
        """Проверка блокировки деструктивных команд и инъекций матрицей Allowlist"""
        from tools.remote_server import execute_remote_allowed_action, ALLOWED_OPERATIONS

        dangerous_commands = [
            "rm -rf /",
            "mkfs.ext4 /dev/sda1",
            "dd if=/dev/zero of=/dev/sda",
            "chmod -R 777 /",
            "init 0",
            ":(){ :|:& };:",
            "cat /etc/shadow"
        ]

        # 1. Попытка выполнить произвольную команду отклоняется
        for cmd in dangerous_commands:
            res = execute_remote_allowed_action(cmd)
            self.assertFalse(res["success"], f"Опасная команда не была заблокирована Allowlist: {cmd}")
            self.assertIn("отклонена политикой безопасности", res["error"])

        # 2. Попытка инъекции через имя службы блокируется
        injection_targets = ["searxng; rm -rf /", "ollama | reboot", "docker && cat /etc/passwd"]
        for target in injection_targets:
            res = execute_remote_allowed_action("restart_service", target=target)
            self.assertFalse(res["success"])
            self.assertIn("Некорректный или пустой аргумент", res["error"])

        # 3. Попытка обращения к неразрешенной службе блокируется
        res_unauthorized = execute_remote_allowed_action("restart_service", target="unauthorized_service")
        self.assertFalse(res_unauthorized["success"])
        self.assertIn("не входит в белый список", res_unauthorized["error"])

        # 4. Проверка присутствия базовых разрешенных операций
        self.assertIn("get_disk_space", ALLOWED_OPERATIONS)
        self.assertIn("docker_ps", ALLOWED_OPERATIONS)

    def test_env_loading(self):
        """Проверка наличия конфигурационного шаблона и ключей"""
        env_example_path = os.path.join(BASE_DIR, ".env.example")
        self.assertTrue(os.path.exists(env_example_path), "Файл .env.example отсутствует в корне проекта!")
        with open(env_example_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("REMOTE_HOST", content)
            self.assertIn("BOOKS_DIR", content)
            self.assertIn("QUEUE_DB_PATH", content)

    def test_sqlite_fts5_unicode(self):
        """Проверка работоспособности SQLite FTS5 с токенизатором unicode61"""
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(body, tokenize='unicode61 remove_diacritics 2')")
        con.execute("INSERT INTO t VALUES ('Маршрутизация сетей и протокол BGP')")
        res = con.execute("SELECT * FROM t WHERE t MATCH 'маршрут*'").fetchall()
        self.assertEqual(len(res), 1, "FTS5 префиксный поиск на русском не сработал!")
        con.close()

    def test_cosine_similarity(self):
        """Проверка математики косинусного сходства без сторонних библиотек"""
        import embedding_engine

        v1 = [1.0, 0.0, 0.0]
        v2 = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(embedding_engine.cosine_similarity(v1, v2), 1.0, places=4)

        v3 = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(embedding_engine.cosine_similarity(v1, v3), 0.0, places=4)

    def test_defensive_routing(self):
        """Проверка детерминированного защитного маршрутизатора в воркере"""
        import bridge_worker

        # Простая короткая задача без маркеров остается на fast
        self.assertEqual(bridge_worker.route_model("fast", "Привет"), "qwen2.5-coder:1.5b")

        # Задача с маркером сложности повышается до 7b
        self.assertEqual(bridge_worker.route_model("fast", "Напиши скрипт"), "qwen2.5-coder:7b")

        # Длинная задача (>300 символов) повышается до 7b
        long_task = "x" * 350
        self.assertEqual(bridge_worker.route_model("fast", long_task), "qwen2.5-coder:7b")

    def test_unified_config(self):
        """Проверка централизованной конфигурации и валидации обязательных параметров"""
        import config

        self.assertTrue(hasattr(config, "QUEUE_DB_PATH"))
        self.assertTrue(hasattr(config, "DATABASE_PATH"))
        self.assertTrue(hasattr(config, "OLLAMA_BASE_URL"))
        self.assertTrue(hasattr(config, "REMOTE_HOST"))
        self.assertTrue(hasattr(config, "REMOTE_USER"))
        self.assertTrue(hasattr(config, "WEB_UI_PORT"))

        # Проверка генерации исключения при отсутствии обязательной переменной
        with self.assertRaises(config.ConfigError):
            config._get_env("NON_EXISTENT_VAR_12345", required=True)

    def test_execution_result_envelope(self):
        """Проверка контракта ExecutionResult (Sprint 2)"""
        from services import ExecutionResult

        res_ok = ExecutionResult.ok("Готово", iterations=2)
        self.assertTrue(res_ok.success)
        self.assertEqual(res_ok.result, "Готово")
        self.assertIsNone(res_ok.error)

        res_fail = ExecutionResult.fail("Таймаут Ollama", is_retryable=True, iterations=1)
        self.assertFalse(res_fail.success)
        self.assertIsNone(res_fail.result)
        self.assertTrue(res_fail.is_retryable)
        self.assertEqual(res_fail.error, "Таймаут Ollama")

    def test_task_lease_and_stale_reclaim(self):
        """Проверка атомарной аренды (Lease) и безопасного реклейма очереди задач (Sprint 2)"""
        import bridge_worker

        # Создаем временную тестовую базу в памяти с аналогичной структурой
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("""
            CREATE TABLE tasks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT, updated_at TEXT,
                task TEXT NOT NULL,
                worker_hint TEXT DEFAULT '',
                acceptance TEXT DEFAULT '',
                status TEXT DEFAULT 'queued',
                result_summary TEXT DEFAULT '',
                artifacts TEXT DEFAULT '',
                error TEXT DEFAULT '',
                locked_by TEXT DEFAULT NULL,
                locked_until TEXT DEFAULT NULL,
                heartbeat_at TEXT DEFAULT NULL,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3
            )
        """)
        # 1. Добавляем задачу
        con.execute("INSERT INTO tasks(task, status) VALUES ('Тестовая задача', 'queued')")
        con.commit()

        # 2. Атомарный claim с lease на 60 сек
        cur = con.execute("""
            UPDATE tasks
            SET status='assigned', locked_by='worker_1', locked_until=datetime('now', '+60 seconds'), attempts=attempts+1
            WHERE id = (SELECT id FROM tasks WHERE status='queued' LIMIT 1)
            RETURNING *
        """)
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "assigned")
        self.assertEqual(row["locked_by"], "worker_1")
        self.assertEqual(row["attempts"], 1)

        # 3. Другой воркер пытается reclaim пока lease активен -> 0 задач возвращено
        cur_reclaim = con.execute("""
            UPDATE tasks SET status='queued', locked_by=NULL, locked_until=NULL
            WHERE status IN ('assigned', 'running')
              AND locked_until IS NOT NULL
              AND locked_until < datetime('now')
        """)
        self.assertEqual(cur_reclaim.rowcount, 0, "Активная аренда была ошибочно перехвачена!")

        # 4. Истекшая аренда (в прошлом) -> должна быть возвращена в queued
        con.execute("UPDATE tasks SET locked_until=datetime('now', '-10 seconds') WHERE id=1")
        con.commit()
        cur_reclaim_expired = con.execute("""
            UPDATE tasks SET status='queued', locked_by=NULL, locked_until=NULL
            WHERE status IN ('assigned', 'running')
              AND locked_until IS NOT NULL
              AND locked_until < datetime('now')
              AND attempts < max_attempts
        """)
        self.assertEqual(cur_reclaim_expired.rowcount, 1, "Истекшая аренда не была возвращена в очередь!")

        # 5. Истекшая аренда с превышением max_attempts -> переводится в failed
        con.execute("UPDATE tasks SET status='assigned', attempts=3, max_attempts=3, locked_until=datetime('now', '-10 seconds') WHERE id=1")
        con.commit()
        cur_fail = con.execute("""
            UPDATE tasks SET status='failed', error='Превышен лимит попыток'
            WHERE status IN ('assigned', 'running')
              AND locked_until IS NOT NULL
              AND locked_until < datetime('now')
              AND attempts >= max_attempts
        """)
        self.assertEqual(cur_fail.rowcount, 1, "Зависшая задача не переведена в failed при attempts >= max_attempts!")
        con.close()

    def test_backup_integrity(self):
        """Проверка валидации PRAGMA integrity_check при создании бэкапов (Sprint 3)"""
        import tempfile
        import shutil
        from tools.backup_manager import verify_sqlite_integrity

        tmp_dir = tempfile.mkdtemp()
        try:
            valid_db = os.path.join(tmp_dir, "valid.db")
            with sqlite3.connect(valid_db) as conn:
                conn.execute("CREATE TABLE test (id INT, val TEXT)")
                conn.execute("INSERT INTO test VALUES (1, 'hello')")
            self.assertTrue(verify_sqlite_integrity(valid_db), "Корректная база не прошла проверку integrity_check!")

            corrupt_db = os.path.join(tmp_dir, "corrupt.db")
            with open(corrupt_db, "wb") as f:
                f.write(b"NOT A SQLITE DATABASE HEADER TRASH DATA")
            self.assertFalse(verify_sqlite_integrity(corrupt_db), "Поврежденный файл ошибочно прошел проверку integrity_check!")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_web_ui_session_isolation(self):
        """Проверка изоляции сессий и валидации лимитов сообщений в Web-UI (Sprint 3)"""
        from web_ui import SessionManager

        mgr = SessionManager(ttl_seconds=60, max_sessions=10)
        # 1. Создаем две разные сессии
        sid1, msgs1 = mgr.get_or_create(None)
        sid2, msgs2 = mgr.get_or_create(None)
        self.assertNotEqual(sid1, sid2, "Сессии должны иметь уникальные идентификаторы!")

        # 2. Модификация сообщений в сессии 1 не должна влиять на сессию 2
        msgs1.append({"role": "user", "content": "Секретное сообщение сессии 1"})
        _, msgs2_refetched = mgr.get_or_create(sid2)
        self.assertEqual(len(msgs2_refetched), 1, "Сессия 2 загрязнилась сообщениями из сессии 1!")

        # 3. Сброс сессии 1 очищает только её историю
        mgr.clear(sid1)
        _, msgs1_cleared = mgr.get_or_create(sid1)
        self.assertEqual(len(msgs1_cleared), 1, "Сессия 1 не сбросилась к системному промпту!")

    def test_dispatcher_safe_eval(self):
        """Проверка безопасного AST калькулятора (Этап 2: Диспетчер)"""
        from dispatcher import safe_eval_math_expression

        # Корректные выражения
        ok, res = safe_eval_math_expression("2 + 2 * 3")
        self.assertTrue(ok)
        self.assertEqual(res, 8)

        ok, res = safe_eval_math_expression("(150 * 1.2) / 3 + sqrt(16)")
        self.assertTrue(ok)
        self.assertAlmostEqual(res, 64.0)

        # Опасные инъекции блокируются AST парсером
        ok_evil, _ = safe_eval_math_expression("__import__('os').system('calc')")
        self.assertFalse(ok_evil, "Опасный вызов __import__ не был заблокирован!")

        ok_evil2, _ = safe_eval_math_expression("open('/etc/passwd').read()")
        self.assertFalse(ok_evil2, "Опасный вызов open() не был заблокирован!")

    def test_dispatcher_intent_classification(self):
        """Проверка классификации интентов и правила Code-First (Этап 2)"""
        from dispatcher import classify_task, TaskCategory

        # Арифметика -> DETERMINISTIC_CALC
        cat, meta = classify_task("Посчитай (25 * 4) + 10")
        self.assertEqual(cat, TaskCategory.DETERMINISTIC_CALC)
        self.assertIn("expr", meta)

        # Поиск по книгам -> DIRECT_BOOK_SEARCH
        cat, meta = classify_task("Найди в книгах про протокол BGP")
        self.assertEqual(cat, TaskCategory.DIRECT_BOOK_SEARCH)
        self.assertIn("query", meta)

        # Многошаговый план -> MULTI_STEP_PLAN
        multi_prompt = "1. Сначала посчитай 10 * 5\n2. Затем найди в книгах по теме Rust"
        cat, meta = classify_task(multi_prompt)
        self.assertEqual(cat, TaskCategory.MULTI_STEP_PLAN)

        # Задача на код -> LLM_CODING
        cat, meta = classify_task("Напиши скрипт на Python для парсинга логов")
        self.assertEqual(cat, TaskCategory.LLM_CODING)

    def test_task_decomposer_and_execution(self):
        """Проверка декомпозиции на подшаги и детерминированного выполнения (Этап 2)"""
        from dispatcher import TaskDecomposer, dispatch_and_execute

        multi_prompt = "1. Посчитай 120 + 35\n2. Посчитай 50 * 4"
        steps = TaskDecomposer.decompose(multi_prompt)
        self.assertEqual(len(steps), 2, "Многошаговая задача не разбита на 2 шага!")
        self.assertEqual(steps[0].step, 1)
        self.assertEqual(steps[1].step, 2)

        # Выполнение детерминированного вычисления через диспетчер (0 токенов)
        exec_res = dispatch_and_execute("Посчитай (100 + 20) * 5")
        self.assertTrue(exec_res.success)
        self.assertIn("600", exec_res.result)
        self.assertEqual(exec_res.iterations, 1)

    def test_epistemic_memory_guardrails(self):
        """Проверка эпистемической гигиены, защиты от самозагрязнения и верификации (Этап 3)"""
        import tempfile
        import shutil
        from tools import memory_manager

        tmp_dir = tempfile.mkdtemp()
        orig_file = memory_manager.MEMORY_FILE
        orig_cache = memory_manager.CACHE_FILE
        try:
            test_mem_file = os.path.join(tmp_dir, "test_memory.json")
            test_cache_file = os.path.join(tmp_dir, "test_cache.json")
            memory_manager.MEMORY_FILE = test_mem_file
            memory_manager.CACHE_FILE = test_cache_file

            # 1. Попытка сохранить гипотезу с завышенной уверенностью 0.95 -> срезается до 0.5
            rec1 = memory_manager.action_save(
                topic="Гипотеза производительности",
                content="Возможно, модель 3b работает быстрее 1.5b",
                memory_type="fact",
                verification_status="hypothesis",
                confidence=0.95,
                source="model"
            )
            self.assertEqual(rec1["verification_status"], "hypothesis")
            self.assertLessEqual(rec1["confidence"], 0.5, "Гипотеза не была ограничена до confidence <= 0.5!")

            # 2. Сохранение проверенного решения
            rec2 = memory_manager.action_save_decision(
                topic="Выбор СУБД",
                decision="SQLite с WAL-режимом",
                rationale="Минимальный оверхед по памяти и атомарная аренда"
            )
            self.assertEqual(rec2["memory_type"], "decision")
            self.assertEqual(rec2["verification_status"], "verified")
            self.assertEqual(rec2["confidence"], 1.0)

            # 3. Сохранение урока (gotcha)
            rec3 = memory_manager.action_save_lesson(
                topic="SQLite date comparison",
                mistake="datetime('now') возвращает UTC строку",
                fix="Использовать одинаковые модификаторы времени"
            )
            self.assertEqual(rec3["memory_type"], "lesson")

            # 4. Верификация гипотезы rec1 -> повышает до 100%
            ok_v = memory_manager.action_verify(rec1["id"], "Подтверждено бенчмарком")
            self.assertTrue(ok_v)
            data_reloaded = memory_manager.load_memory()
            verified_rec = next(r for r in data_reloaded if r["id"] == rec1["id"])
            self.assertEqual(verified_rec["verification_status"], "verified")
            self.assertEqual(verified_rec["confidence"], 1.0)
        finally:
            memory_manager.MEMORY_FILE = orig_file
            memory_manager.CACHE_FILE = orig_cache
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
