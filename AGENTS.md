# РЕГЛАМЕНТ И АРХИТЕКТУРА ЛОКАЛЬНОГО АГЕНТА (AI_Agent)
## Модель ядра: `qwen2.5-coder:7b` (Ollama, Zero-Cost Tokens) | Web-UI: `:3080`

### 1. Архитектура и принципы
- **Автономный ReAct-цикл:** Оркестратор анализирует задачу, подбирает инструмент, запускает его локально и возвращает результат в контекст модели.
- **Динамический реестр и семантические алиасы (Semantic Action Naming):** 
  - Каждый скрипт в `tools/` объявляет структуру `TOOL_SPEC`.
  - Модели 7B отдаются говорящие расширенные имена (`search_technical_books_library_rag`, `fetch_external_webpage_content`, `check_system_and_services_health`).
  - Роутер `orchestrator.py` поддерживает двойной маппинг (35 ключей на 17 файлов), обеспечивая 100% точность вызова.
- **Единый пул соединений и сервисов (`services.py`):**
  - Потокобезопасный `DatabasePool` с автоматическим `PRAGMA journal_mode=WAL;` и `busy_timeout=5000` (защита от "database is locked").
  - `ModelProvider` с автоматическим Failover: если локальная Ollama перегружена, запрос уходит на удаленный узел Linux (адрес задаётся в `.env` → `REMOTE_HOST`).
  - `ToolExecutionResult` (Graceful Degradation): изолирует падения утилит и возвращает структурированный JSON с подсказкой для модели.
- **Трехуровневая память и персистентность сессий:** 
  1. Долговременная сессия (`session_store.py` $\to$ `session.json`): атомарное сохранение истории диалога.
  2. Фактологическая память (`memory.json` $\to$ `memory_manager.py`): сохранение постоянных фактов с гибридным поиском (`nomic-embed-text`).
  3. Сохраненная база ссылок (`saved_links.json` $\to$ `web_fetcher.py` + `search_saved_links.py`): RRF-поиск по изученным проектам.

---

### 2. Подключенные инструменты (tools/)

| Инструмент | Семантическое имя для LLM | Описание |
| :--- | :--- | :--- |
| **`ask_books.py`** | `search_technical_books_library_rag` | Умный RAG-ответ по 424+ книгам с цитатами `[Книга, стр. N]` (FTS5 + Ollama) |
| **`search_books.py`** | `find_raw_books_fts_index` | Быстрый полнотекстовый поиск фрагментов по базе `books_index.db` |
| **`book_indexer.py`** | `book_indexer` | Инкрементальный индексатор PDF-книг в SQLite FTS5 с русским `unicode61` |
| **`web_fetcher.py`** | `fetch_external_webpage_content` | Веб-скрейпер чистого текста с защитой SSL и сохранением в `saved_links.json` |
| **`search_saved_links.py`** | `search_previously_studied_links_and_repos` | Гибридный RRF-поиск по ранее изученным проектам и ссылкам |
| **`memory_manager.py`** | `save_or_recall_permanent_memory` | Долговременная память ключевых архитектурных фактов и решений |
| **`status_report.py`** | `check_system_and_services_health` | Единая сводка здоровья экосистемы (Ollama, БД, SearXNG, сервер) |
| **`backup_manager.py`** | `manage_hot_database_backups` | Горячий бэкап SQLite БД, ротация и синхронизация на Linux-сервер |
| **`doc_reader.py`** | `read_and_parse_local_document_file` | Универсальный экстрактор текста DOCX, XLSX, PPTX, CSV, TXT, HTML |
| **`code_check.py`** | `verify_and_lint_code_syntax` | Верификатор кода (Python AST, ruff, pytest, cargo check) |
| **`auto_refactor.py`** | `self_healing_code_refactor_and_fix` | Цикл самоисправления кода: находит баги, лечит через qwen2.5-coder, дает diff |
| **`pipeline_bom.py`** | `generate_blueprint_bom_takeoff_specification` | Конвейер Blueprint-to-BOM: извлечение оборудования, привязка к каталогам, экспорт CSV |
| **`ocr_scans.py`** | `recognize_text_from_images_and_scans` | Локальный OCR чертежей и сканов на русском и английском (RapidOCR) |
| **`cad_extractor.py`** | `extract_equipment_from_cad_and_blueprints` | Парсинг и подсчет оборудования по чертежам и схемам |
| **`network_scanner.py`** | `scan_local_network_hosts_and_ips` | Многопоточный ping-sweep локальной подсети (из `.env`) с извлечением MAC |
| **`port_checker.py`** | `check_network_port_open` | Проверка доступности портов TCP на любом хосте |
| **`remote_server.py`** | `execute_commands_on_linux_server` | Управление Linux-узлом (wake, sleep, status, exec с guardrails) |
| **`night_ops.py`** | `run_nightly_maintenance_chain` | Автоматизированный ночной пайплайн обслуживания |
| **`consult_senior_ai.py`** | `consult_with_senior_architect_ai` | Эскалация сложных задач Старшему ИИ через мост `bridge_tasks.json` |

---

### 3. Веб-интерфейс и мониторинг (`web_ui.py`)
- Запуск локального дашборда:
  ```bash
  python web_ui.py
  ```
- Доступен по адресу: `http://127.0.0.1:3080`.
- Включает живой чат, индикацию статуса узлов и инспектор цепочки ReAct (`[Thought] -> [Tool] -> [Output] -> [Synthesis]`).

---

### 4. Жесткие правила обработки внешних ссылок (Anti-Hallucination)
- **Запрет подмены контекста:** При получении внешней ссылки категорически запрещено подменять её содержимое списком локальных инструментов.
- **Обязательный вызов инструмента:** Модель обязана задействовать `web_fetcher` для физической загрузки данных.
- **Анализ только полученных данных:** Ответ строится строго на основе текста из `<web_content>`.
- **Поиск по изученным материалам:** На вопрос «что мы смотрели про X» агент ОБЯЗАН вызывать `search_saved_links`.
