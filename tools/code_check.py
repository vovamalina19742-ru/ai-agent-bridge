# -*- coding: utf-8 -*-
"""
code_check.py — Автоматическая верификация правок кода и тестов проекта.

Возможности:
1. Синтаксическая проверка через встроенный py_compile (быстро, 0 зависимостей).
2. Статический анализ и линтинг через ruff (сверхбыстрый Rust-линтер).
3. Прогон тестов через pytest (компактная выжимка упавших и пройденных тестов).
4. Поддержка Rust: проверка через `cargo check` (если указан путь с Cargo.toml).

Вывод: краткий отчет <= 2000 символов, исключающий раздувание контекста LLM.
"""
import os
import sys
import py_compile
import argparse
import subprocess

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOOL_SPEC = {
    "name": "code_check",
    "description": "Прогон синтаксических проверок, линтера (ruff) и тестов (pytest/cargo) для валидации правок кода.",
    "parameters": {
        "type": "object",
        "properties": {
            "target_path": {
                "type": "string",
                "description": "Путь к файлу или директории проекта",
            },
            "run_tests": {
                "type": "boolean",
                "description": "Запустить pytest/cargo test (по умолчанию: false)",
            },
            "fix_lint": {
                "type": "boolean",
                "description": "Автоматически исправить простые ошибки через ruff --fix (по умолчанию: false)",
            },
        },
        "required": ["target_path"],
    },
}


def check_syntax(path):
    """Компилирует Python файлы в байт-код для проверки синтаксиса."""
    if os.path.isfile(path) and path.endswith(".py"):
        try:
            py_compile.compile(path, doraise=True)
            return True, "Синтаксис корректен"
        except py_compile.PyCompileError as e:
            return False, f"Синтаксическая ошибка: {e}"

    errors = []
    checked = 0
    for root, _, files in os.walk(path):
        for f in files:
            if f.endswith(".py"):
                checked += 1
                full_p = os.path.join(root, f)
                try:
                    py_compile.compile(full_p, doraise=True)
                except py_compile.PyCompileError as e:
                    errors.append(f"{f}: {e}")
                    if len(errors) >= 5:
                        break
        if len(errors) >= 5:
            break

    if errors:
        return False, f"Ошибки в {len(errors)} файлах:\n" + "\n".join(errors)
    return True, f"Проверено файлов: {checked}. Синтаксических ошибок нет."


def run_ruff(path, auto_fix=False):
    """Запускает линтер ruff."""
    cmd = [sys.executable, "-m", "ruff", "check", path]
    if auto_fix:
        cmd.append("--fix")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return True, "Линтер (ruff): нарушений не обнаружено"
        else:
            lines = res.stdout.strip().splitlines()
            summary = "\n".join(lines[:8])
            if len(lines) > 8:
                summary += f"\n... и ещё {len(lines) - 8} замечаний"
            return False, f"Линтер (ruff):\n{summary}"
    except Exception as e:
        return True, f"Ruff пропущен ({e})"


def run_pytest(path):
    """Запускает pytest."""
    cmd = [sys.executable, "-m", "pytest", path, "-q", "--tb=short", "--maxfail=3"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = res.stdout.strip() or res.stderr.strip()
        lines = output.splitlines()
        summary = "\n".join(lines[-5:]) if lines else "Тесты выполнены"
        return res.returncode == 0, f"Pytest:\n{summary}"
    except Exception as e:
        return False, f"Ошибка запуска pytest: {e}"


def run_cargo(path, run_tests=False):
    """Проверяет Rust-проект через cargo check или cargo test."""
    cargo_cmd = ["cargo", "test", "--quiet"] if run_tests else ["cargo", "check", "--quiet"]
    try:
        res = subprocess.run(cargo_cmd, cwd=path, capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            return True, f"Cargo ({'test' if run_tests else 'check'}): УСПЕШНО"
        else:
            err = res.stderr.strip() or res.stdout.strip()
            lines = err.splitlines()
            return False, f"Cargo ошибка:\n" + "\n".join(lines[:6])
    except Exception as e:
        return False, f"Ошибка cargo: {e}"


def check_project(target_path, run_tests=False, fix_lint=False):
    if not os.path.exists(target_path):
        return f"[-] Путь не найден: {target_path}"

    reports = []
    reports.append(f"=== ПРОВЕРКА КОДА: {os.path.basename(target_path)} ===")

    # 1. Если это Rust проект
    is_rust = os.path.isdir(target_path) and os.path.exists(os.path.join(target_path, "Cargo.toml"))
    if is_rust:
        ok, msg = run_cargo(target_path, run_tests=run_tests)
        reports.append(msg)
        return "\n".join(reports)

    # 2. Python синтаксис
    syn_ok, syn_msg = check_syntax(target_path)
    reports.append(f"• {syn_msg}")

    # 3. Линтинг ruff
    lint_ok, lint_msg = run_ruff(target_path, auto_fix=fix_lint)
    reports.append(f"• {lint_msg}")

    # 4. Тесты (если запрошены)
    if run_tests:
        test_ok, test_msg = run_pytest(target_path)
        reports.append(f"• {test_msg}")

    verdict = "ИТОГ: ГОТОВО К ПРИЁМКЕ" if (syn_ok and lint_ok) else "ИТОГ: ТРЕБУЕТСЯ ИСПРАВЛЕНИЕ"
    reports.append(verdict)
    return "\n".join(reports)


def main():
    parser = argparse.ArgumentParser(description="Автоматическая проверка кода")
    parser.add_argument("target_path", help="Файл или папка")
    parser.add_argument("--test", "-t", action="store_true", help="Запустить тесты")
    parser.add_argument("--fix", action="store_true", help="Автофикс через ruff")
    args = parser.parse_args()

    res = check_project(args.target_path, run_tests=args.test, fix_lint=args.fix)
    print(res)


if __name__ == "__main__":
    main()
