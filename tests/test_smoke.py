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

if __name__ == "__main__":
    unittest.main()
