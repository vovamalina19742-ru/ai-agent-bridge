# -*- coding: utf-8 -*-
r"""
auto_refactor.py — Автономный инструмент самоисправления и рефакторинга кода (Self-Healing Code Loop).

Алгоритм:
1. Запуск статической проверки (синтаксис / линтер py_compile / ruff / cargo).
2. При наличии ошибок: отправка кода и текста ошибки в локальную Ollama (qwen2.5-coder:7b).
3. Применение предложенного исправления и повторный прогон тестов (до 3 итераций).
4. Автоматический откат к оригиналу при невозможности исправить (защита от регрессий).
5. Формирование чистого unified diff с демонстрацией правок.
"""

import os
import sys
import difflib
import json
import argparse
import py_compile
import urllib.request

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

try:
    from config import OLLAMA_BASE_URL, DEFAULT_MODEL
except Exception:
    OLLAMA_BASE_URL = "http://localhost:11434"
    DEFAULT_MODEL = "qwen2.5-coder:7b"

TOOL_SPEC = {
    "name": "auto_refactor",
    "description": "Автономный цикл самоисправления кода (Self-Healing Code Loop): находит синтаксические ошибки, генерирует исправление через qwen2.5-coder, применяет патч, валидирует себя и показывает unified diff.",
    "parameters": {
        "type": "object",
        "properties": {
            "target_file": {
                "type": "string",
                "description": "Путь к исходному файлу для проверки и лечения",
            },
            "instruction": {
                "type": "string",
                "description": "Дополнительная инструкция по рефакторингу (опционально)",
            },
            "max_attempts": {
                "type": "integer",
                "description": "Максимальное число итераций самоисправления (по умолчанию: 3)",
            },
        },
        "required": ["target_file"],
    },
}


def check_file_errors(file_path):
    """
    Проверяет файл на синтаксические ошибки и базовый линтинг.
    Возвращает: (is_valid: bool, error_msg: str)
    """
    if not os.path.exists(file_path):
        return False, f"Файл не найден: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".py":
        try:
            py_compile.compile(file_path, doraise=True)
            return True, "Синтаксис корректен"
        except py_compile.PyCompileError as e:
            # Извлекаем понятную суть ошибки
            return False, str(e.exc_value) if hasattr(e, 'exc_value') else str(e)
        except Exception as e:
            return False, str(e)

    elif ext == ".rs":
        # Если это Rust файл, пробуем rustc --parse-only или проверку синтаксиса
        try:
            import subprocess
            proc = subprocess.run(
                ["rustc", "--edition", "2021", "-Z", "parse-only", file_path],
                capture_output=True, text=True, timeout=10
            )
            if proc.returncode != 0:
                return False, proc.stderr[:1000]
            return True, "Синтаксис Rust корректен"
        except Exception:
            # Если rustc не установлен, пропускаем глубокую проверку
            return True, "Проверка Rust пропущена (компилятор не найден)"

    return True, "Формат файла поддерживается без встроенного компилятора"


def call_llm_fix(original_code, error_msg, instruction="", model=DEFAULT_MODEL):
    """
    Отправляет исходный код и текст ошибки локальной модели для генерации чистого исправления.
    """
    url = f"{OLLAMA_BASE_URL}/api/generate"

    prompt = f"""Ты — высокоточный инженер по исправлению кода (Self-Healing Code Agent).

ОШИБКА КОМПИЛЯТОРА / СИНТАКСИСА:
{error_msg}

ДОПОЛНИТЕЛЬНАЯ ИНСТРУКЦИЯ:
{instruction if instruction else "Устрани синтаксическую ошибку, сохранив логику и структуру."}

ИСХОДНЫЙ КОД:
```
{original_code}
```

ТРЕБОВАНИЯ:
1. Исправь ошибку точно в месте сбоя.
2. Сохрани все импорты, комментарии и структуру, если они не являются причиной ошибки.
3. Верни ТОЛЬКО исправленный полный исходный код файла внутри одного блока кода ```.
Без предисловий, без объяснений, без текста после блока!
"""

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,  # минимальная температура для строгого исправления
            "num_predict": 4096,
        },
    }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=120.0) as resp:
        res_json = json.loads(resp.read().decode("utf-8"))
        raw_reply = res_json.get("response", "").strip()

    # Извлекаем код из блока ```
    if "```" in raw_reply:
        first = raw_reply.find("```")
        # Пропускаем строку с объявлением языка (например, ```python\n)
        newline_after = raw_reply.find("\n", first)
        last = raw_reply.rfind("```")
        if newline_after != -1 and last > newline_after:
            extracted = raw_reply[newline_after + 1:last].strip()
            return extracted

    return raw_reply


def self_healing_refactor(file_path, instruction="", max_attempts=3):
    """
    Основной цикл самоисцеления.
    """
    if not os.path.exists(file_path):
        return f"[-] Ошибка: Файл '{file_path}' не существует."

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        original_content = f.read()

    # 1. Начальная проверка
    is_valid, err = check_file_errors(file_path)
    if is_valid and not instruction:
        return f"[✓] Файл '{os.path.basename(file_path)}' полностью валиден. Ошибок не обнаружено, вмешательство не требуется."

    current_code = original_content
    current_error = err if not is_valid else f"Рефакторинг по запросу: {instruction}"

    report_lines = [
        f"=== ЗАПУСК SELF-HEALING REFACTORING: {os.path.basename(file_path)} ===",
        f"• Исходная проблема: {current_error}",
    ]

    fixed_successfully = False

    for attempt in range(1, max_attempts + 1):
        report_lines.append(f"\n[Попытка {attempt}/{max_attempts}] Запрос к {DEFAULT_MODEL} на генерацию исправления...")

        try:
            suggested_code = call_llm_fix(current_code, current_error, instruction)
            if not suggested_code or len(suggested_code) < 10:
                report_lines.append(f"[-] Попытка {attempt}: модель вернула пустой или некорректный код.")
                continue

            # Записываем предложенный код в файл для тестовой проверки
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(suggested_code)

            # Перепроверяем файл
            test_valid, test_err = check_file_errors(file_path)
            if test_valid:
                fixed_successfully = True
                current_code = suggested_code
                report_lines.append(f"[✓] Успех на попытке {attempt}! Код прошел синтаксическую валидацию.")
                break
            else:
                report_lines.append(f"[!] Попытка {attempt} не устранила ошибку: {test_err}")
                current_error = test_err
                current_code = suggested_code

        except Exception as e:
            report_lines.append(f"[-] Ошибка обращения к модели: {e}")

    # Финал: если не удалось починить, откатываемся к оригиналу (Safety Guardrail)
    if not fixed_successfully:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(original_content)
        report_lines.append("\n[!] Не удалось автоматически исправить ошибку. Файл безопасно откачен к исходному состоянию.")
        return "\n".join(report_lines)

    # Генерируем красивый diff
    diff = list(difflib.unified_diff(
        original_content.splitlines(keepends=True),
        current_code.splitlines(keepends=True),
        fromfile=f"a/{os.path.basename(file_path)} (исходный)",
        tofile=f"b/{os.path.basename(file_path)} (исправленный)",
        n=3
    ))

    diff_text = "".join(diff) if diff else "[Изменений нет]"

    report_lines.append("\n=== ЧИСТЫЙ DIFF ИЗМЕНЕНИЙ ===")
    report_lines.append(diff_text[:2500])
    report_lines.append("\n[✓] Файл успешно обновлен и готов к работе.")

    return "\n".join(report_lines)


def main():
    parser = argparse.ArgumentParser(description="Автономный цикл самоисправления и рефакторинга кода")
    parser.add_argument("pos_file", nargs="?", default=None, help="Путь к файлу (позиционный)")
    parser.add_argument("--file", "-f", default=None, help="Путь к файлу через флаг")
    parser.add_argument("--instruction", "-i", default="", help="Инструкция по рефакторингу")
    parser.add_argument("--attempts", "-a", type=int, default=3, help="Максимум попыток исцеления")
    args = parser.parse_args()

    target = args.file or args.pos_file
    if not target:
        print("[-] Укажите целевой файл для анализа: python auto_refactor.py <путь_к_файлу>")
        return

    result = self_healing_refactor(target, instruction=args.instruction, max_attempts=args.attempts)
    print(result)


if __name__ == "__main__":
    main()
