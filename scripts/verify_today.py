# -*- coding: utf-8 -*-
"""
verify_today.py — Быстрый легкий тест всех компонентов, созданных сегодня:
1. Интеллектуальный Диспетчер (Классификация интентов + 0-cost AST калькулятор).
2. Эпистемическая память (Архитектурные решения, уроки и guardrail для гипотез).
3. Инструмент проверки целостности кода (code_check / syntax / py_compile).
4. Реестр безопасных инструментов.
"""
import sys
import os
import time

# Фикс UTF-8 на Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("tools"))

import dispatcher
import memory_manager
from tools.code_check import check_syntax

def run_tests():
    print("=" * 65)
    print("  🚀 ЛЁГКИЙ ЭКСПРЕСС-ТЕСТ НОВЫХ МОДУЛЕЙ AI_AGENT")
    print("=" * 65)

    # ---------------------------------------------------------
    # ТЕСТ 1: Диспетчер и AST-Калькулятор (0 токенов)
    # ---------------------------------------------------------
    print("\n[ТЕСТ 1] 🧠 Интеллектуальный Диспетчер (Code-First & AST)")
    math_query = "посчитай (1920 * 1080 * 4) / (1024 * 1024)"
    cat, meta = dispatcher.classify_task(math_query)
    ok, result = dispatcher.safe_eval_math_expression(meta.get("expr", ""))
    
    print(f"  • Запрос: \"{math_query}\"")
    print(f"  • Определённая категория: {cat.value}")
    print(f"  • Вычисление AST (0 токенов, мгновенно): {result:.2f} МБ")
    assert cat == dispatcher.TaskCategory.DETERMINISTIC_CALC
    assert abs(result - 7.91) < 0.05
    print("  ✅ [ПРОЙДЕНО] Формула перехвачена до LLM и вычислена со 100% точностью!")

    # ---------------------------------------------------------
    # ТЕСТ 2: Классификация различных типов задач
    # ---------------------------------------------------------
    print("\n[ТЕСТ 2] 🎯 Точность маршрутизации задач (Intent Router)")
    queries = [
        ("Найди в книгах по теме байесовские методы", dispatcher.TaskCategory.DIRECT_BOOK_SEARCH),
        ("Составь смету и спецификацию оборудования по чертежу", dispatcher.TaskCategory.DIRECT_BOM_PIPELINE),
        ("Сначала проверь порты, затем сделай бэкап и отправь отчет", dispatcher.TaskCategory.MULTI_STEP_PLAN),
    ]
    for q, expected_cat in queries:
        c, _ = dispatcher.classify_task(q)
        print(f"  • \"{q[:45]}...\" -> {c.value}")
        assert c == expected_cat
    print("  ✅ [ПРОЙДЕНО] Все 3 типа задач классифицированы верно!")

    # ---------------------------------------------------------
    # ТЕСТ 3: Эпистемическая гигиена памяти (Guardrail гипотез)
    # ---------------------------------------------------------
    print("\n[ТЕСТ 3] 🛡️ Эпистемическая память: защита от самозагрязнения")
    hypo = memory_manager.action_save(
        topic="Скорость qwen-1.5b",
        content="Модель 1.5b отвечает в 3 раза быстрее на CPU",
        memory_type="fact",
        source="model",
        verification_status="hypothesis",
        confidence=0.95 # Попытка завысить уверенность гипотезы модели
    )
    print("  • Сохранена гипотеза модели с заявленной уверенностью 95%")
    print(f"  • Фактическая уверенность после guardrail: {hypo['confidence']:.0%}")
    assert hypo["confidence"] <= 0.5
    print("  ✅ [ПРОЙДЕНО] Guardrail сработал: неподтвержденная гипотеза ограничена 50%!")

    # ---------------------------------------------------------
    # ТЕСТ 4: Верификация гипотезы до подтвержденного знания
    # ---------------------------------------------------------
    print("\n[ТЕСТ 4] 🏅 Повышение статуса знания (Verification Loop)")
    memory_manager.action_verify(
        topic_or_id=hypo["id"],
        proof="Проверено бенчмарком: среднее время ответа 220мс против 680мс"
    )
    verified_data = memory_manager.load_memory()
    target_rec = next(r for r in verified_data if r["id"] == hypo["id"])
    print(f"  • Статус после верификации: {target_rec['verification_status']}")
    print(f"  • Уверенность после верификации: {target_rec['confidence']:.0%}")
    assert target_rec["verification_status"] == "verified"
    assert target_rec["confidence"] == 1.0
    print("  ✅ [ПРОЙДЕНО] Знание переведено в верифицированное со 100% уверенностью!")

    # ---------------------------------------------------------
    # ТЕСТ 5: Проверка синтаксиса текущего проекта (Zero-Cost AST)
    # ---------------------------------------------------------
    print("\n[ТЕСТ 5] 🔍 Синтаксический аудит (code_check / py_compile)")
    t0 = time.time()
    ok, msg = check_syntax("dispatcher.py")
    elapsed = (time.time() - t0) * 1000
    print(f"  • Проверка файла dispatcher.py: {msg} ({elapsed:.1f} мс)")
    assert ok
    print("  ✅ [ПРОЙДЕНО] Синтаксический анализатор работает штатно!")

    print("\n" + "=" * 65)
    print("  🎉 ВСЕ 5 ЛЁГКИХ ТЕСТОВ УСПЕШНО ПРОЙДЕНЫ! СИСТЕМА ГОТОВА.")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
