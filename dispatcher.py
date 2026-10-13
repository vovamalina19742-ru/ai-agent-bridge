# -*- coding: utf-8 -*-
r"""
dispatcher.py — Интеллектуальный Диспетчер и Координатор задач (Этап 2: Compound AI Architecture).

Функции:
1. Классификация интента (Code-First Rule):
   - Чистая арифметика/вычисления -> Безопасный калькулятор на Python AST (0 токенов, <1 мс).
   - Прямой поиск в книгах/библиотеке -> SQLite FTS5 books_search (0 токенов, <50 мс).
   - Подсчет спецификаций/чертежей -> tools/pipeline_bom (Minimal CAD Takeoff, 0 токенов).
   - Многошаговые комплексные задания -> Декомпозиция на DAG-цепочку подзадач.
   - Задачи программирования/рефакторинга -> qwen2.5-coder:7b.
   - Глубокие архитектурные рассуждения -> deepseek-r1:8b.
   - Саммари/простые классификации -> qwen2.5:3b / 1.5b.
2. Декомпозиция сложных задач на этапы с передачей контекста между шагами.
3. Единый контракт возврата ExecutionResult (success, result, error, is_retryable).
"""

import os
import sys
import re
import ast
import math
import operator
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

# UTF-8 кодировка для Windows консоли
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from services import ExecutionResult
import orchestrator


class TaskCategory(Enum):
    DETERMINISTIC_CALC = "deterministic_calc"
    DIRECT_BOOK_SEARCH = "direct_book_search"
    DIRECT_BOM_PIPELINE = "direct_bom_pipeline"
    MULTI_STEP_PLAN = "multi_step_plan"
    LLM_CODING = "llm_coding"
    LLM_REASONING = "llm_reasoning"
    LLM_SUMMARY = "llm_summary"
    LLM_GENERAL = "llm_general"


# ---------------------------------------------------------------------------
# 1. БЕЗОПАСНЫЙ AST КАЛЬКУЛЯТОР (0 токенов, защита от eval инъекций)
# ---------------------------------------------------------------------------

ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

ALLOWED_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "ceil": math.ceil,
    "floor": math.floor,
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
}

ALLOWED_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
}


def safe_eval_math_expression(expr_str: str) -> Tuple[bool, Any]:
    """
    Безопасно вычисляет математическое выражение через AST обход без вызова eval().
    Возвращает (success: bool, result_or_error: Any).
    """
    try:
        # Предварительная очистка
        clean_expr = expr_str.strip().replace("^", "**").replace("×", "*").replace("÷", "/")
        tree = ast.parse(clean_expr, mode="eval")

        def _eval_node(node):
            if isinstance(node, ast.Expression):
                return _eval_node(node.body)
            elif isinstance(node, ast.Constant):  # Python 3.8+
                if isinstance(node.value, (int, float)):
                    return node.value
                raise ValueError(f"Недопустимая константа: {node.value}")
            elif isinstance(node, ast.BinOp):
                op_type = type(node.op)
                if op_type not in ALLOWED_OPERATORS:
                    raise ValueError(f"Оператор {op_type.__name__} не поддерживается")
                left = _eval_node(node.left)
                right = _eval_node(node.right)
                return ALLOWED_OPERATORS[op_type](left, right)
            elif isinstance(node, ast.UnaryOp):
                op_type = type(node.op)
                if op_type not in ALLOWED_OPERATORS:
                    raise ValueError(f"Унарный оператор {op_type.__name__} не поддерживается")
                operand = _eval_node(node.operand)
                return ALLOWED_OPERATORS[op_type](operand)
            elif isinstance(node, ast.Name):
                name = node.id.lower()
                if name in ALLOWED_CONSTANTS:
                    return ALLOWED_CONSTANTS[name]
                raise ValueError(f"Неизвестная переменная: {node.id}")
            elif isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    raise ValueError("Вызовы сложных объектов запрещены")
                func_name = node.func.id.lower()
                if func_name not in ALLOWED_FUNCTIONS:
                    raise ValueError(f"Функция {func_name}() не разрешена")
                args = [_eval_node(arg) for arg in node.args]
                return ALLOWED_FUNCTIONS[func_name](*args)
            else:
                raise ValueError(f"Недопустимый узел AST: {type(node).__name__}")

        val = _eval_node(tree)
        # Округляем до 6 знаков для аккуратности
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        elif isinstance(val, float):
            val = round(val, 6)
        return True, val
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# 2. КЛАССИФИКАТОР ЗАДАЧ (INTENT CLASSIFIER)
# ---------------------------------------------------------------------------

CALC_TRIGGER_PATTERNS = [
    r"^\s*(?:посчитай|вычисли|сколько будет|calculate|calc:)\s*(.+)$",
    r"^\s*([0-9\.\s\+\-\*\/\(\)\^\%×÷]+)\s*=?\s*$",
]

BOOK_SEARCH_PATTERNS = [
    r"(?:найди|поищи|что (?:пишут|говорится) в|поиск по|поищи)\s+(?:в\s+)?(?:книг[а-я]*|библиотек[а-я]*|литератур[а-я]*)\s+(?:по теме|про|о)?\s*(.+)",
    r"^search_books?:\s*(.+)$",
]

BOM_PATTERNS = [
    r"(?:посчитай|сделай|сформируй|составь|извлеки)\s+(?:спецификаци[а-я]*|смет[а-я]*|bom)\s+(?:из|по|чертеж[а-я]*)\s*(.+)?",
    r"pipeline_bom:\s*(.+)",
    r"\.(?:dxf|dwg)\b",
]

MULTI_STEP_MARKERS = [
    r"(?:1\.\s+.*\n\s*2\.\s+)",
    r"(?:шаг 1.*шаг 2)",
    r"(?:сначала\s+.*(?:затем|потом|после этого|далее))",
    r"(?:во-первых\s+.*во-вторых)",
]

CODE_MARKERS = (
    "напиши код", "исправь ошибку", "рефактор", "скрипт", "функци", "класс",
    "python", "rust", "bash", "powershell", "тест", "traceback", "linter",
    "def ", "import ", "fn ", "cargo", "pytest", "bug", "syntaxerror"
)

REASONING_MARKERS = (
    "докажи", "сравни архитектур", "глубокий анализ", "плюсы и минусы",
    "почему", "обоснуй выбор", "рассуждение", "алгоритмическая сложность",
    "theorem", "prove", "trade-off", "architecture design"
)


def classify_task(task_text: str, hint: str = "") -> Tuple[TaskCategory, Dict[str, Any]]:
    """
    Классифицирует задачу по принципу Code-First:
    Сначала проверяет возможность 100% детерминированного решения без LLM.
    """
    t = task_text.strip()
    t_lower = t.lower()
    h_lower = (hint or "").strip().lower()

    if h_lower == "calc":
        return TaskCategory.DETERMINISTIC_CALC, {"expr": t}
    if h_lower == "books":
        return TaskCategory.DIRECT_BOOK_SEARCH, {"query": t}
    if h_lower == "bom":
        return TaskCategory.DIRECT_BOM_PIPELINE, {"input": t}

    # 1. Проверка на многошаговую задачу (декомпозиция всегда в приоритете)
    for pattern in MULTI_STEP_MARKERS:
        if re.search(pattern, t, re.IGNORECASE | re.DOTALL):
            return TaskCategory.MULTI_STEP_PLAN, {"raw": t}

    # 2. Проверка на чистую арифметику
    for pattern in CALC_TRIGGER_PATTERNS:
        m = re.match(pattern, t, re.IGNORECASE)
        if m:
            expr_candidate = m.group(1).rstrip("?=")
            # Проверяем, парсится ли выражение AST калькулятором
            ok, _ = safe_eval_math_expression(expr_candidate)
            if ok:
                return TaskCategory.DETERMINISTIC_CALC, {"expr": expr_candidate}

    # 3. Проверка на прямой поиск по книгам
    for pattern in BOOK_SEARCH_PATTERNS:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            query = m.group(1).strip()
            return TaskCategory.DIRECT_BOOK_SEARCH, {"query": query}

    # 4. Проверка на пайплайн спецификаций (BOM)
    for pattern in BOM_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return TaskCategory.DIRECT_BOM_PIPELINE, {"input": t}

    # 5. Маршрутизация моделей по специализации
    if h_lower == "reason" or any(m in t_lower for m in REASONING_MARKERS):
        return TaskCategory.LLM_REASONING, {"model": "deepseek-r1:8b"}

    if h_lower in ("coder", "fast") or any(m in t_lower for m in CODE_MARKERS):
        if h_lower == "fast" and len(t) <= 200 and not any(m in t_lower for m in ("напиши", "исправь", "рефактор")):
            return TaskCategory.LLM_SUMMARY, {"model": "qwen2.5-coder:1.5b"}
        return TaskCategory.LLM_CODING, {"model": "qwen2.5-coder:7b"}

    if h_lower in ("summary", "3b") or len(t) < 150:
        return TaskCategory.LLM_SUMMARY, {"model": "qwen2.5:3b"}

    return TaskCategory.LLM_GENERAL, {"model": "qwen2.5-coder:7b"}


# ---------------------------------------------------------------------------
# 3. ДЕКОМПОЗИЦИЯ МНОГОШАГОВЫХ ЗАДАЧ (TASK DECOMPOSER)
# ---------------------------------------------------------------------------

@dataclass
class SubTask:
    step: int
    title: str
    action_type: str  # 'calc', 'book_search', 'bom', 'code', 'llm'
    payload: str
    result: Optional[str] = None


class TaskDecomposer:
    """Разбивает комплексную задачу на упорядоченные подшаги."""

    @staticmethod
    def decompose(task_text: str) -> List[SubTask]:
        steps: List[SubTask] = []
        lines = [line.strip() for line in task_text.splitlines() if line.strip()]

        # Вариант 1: Нумерованный список "1. Шаг ... 2. Шаг ..."
        numbered_pattern = re.compile(r"^(\d+)[\.\)]\s*(.+)$")
        extracted_numbered = []
        for line in lines:
            m = numbered_pattern.match(line)
            if m:
                extracted_numbered.append((int(m.group(1)), m.group(2)))

        if len(extracted_numbered) >= 2:
            for idx, text in extracted_numbered:
                cat, _ = classify_task(text)
                steps.append(SubTask(
                    step=idx,
                    title=text[:60],
                    action_type=cat.value,
                    payload=text
                ))
            return steps

        # Вариант 2: Разделение по фразам-маркерам (сначала / затем / после этого)
        split_markers = r"(?:^|\s+)(?:сначала|затем|потом|после этого|далее|в конце|наконец)[\s,:]+"
        parts = [p.strip() for p in re.split(split_markers, task_text, flags=re.IGNORECASE) if p.strip()]
        if len(parts) >= 2:
            for i, part in enumerate(parts, 1):
                cat, _ = classify_task(part)
                steps.append(SubTask(
                    step=i,
                    title=part[:60],
                    action_type=cat.value,
                    payload=part
                ))
            return steps

        # Если декомпозиция на шаги не удалась — возвращаем единую задачу
        cat, _ = classify_task(task_text)
        return [SubTask(step=1, title="Основная задача", action_type=cat.value, payload=task_text)]


# ---------------------------------------------------------------------------
# 4. ДИСПЕТЧЕР И ИСПОЛНИТЕЛЬ (DISPATCH & EXECUTE)
# ---------------------------------------------------------------------------

def execute_direct_book_search(query: str) -> ExecutionResult:
    """Выполняет поиск по базе 362 книг напрямую через tools/search_books.py."""
    try:
        from tools.search_books import search_books
        res = search_books(query, limit=3)
        if isinstance(res, list) and res:
            out = [f"### 📚 Найдено в книгах по запросу: «{query}»:\n"]
            for i, item in enumerate(res, 1):
                book = item.get("book", "Книга")
                page = item.get("page", "?")
                snippet = item.get("snippet", "").strip()
                out.append(f"**{i}. {book} (стр. {page})**\n> {snippet}\n")
            return ExecutionResult.ok("\n".join(out), iterations=1)
        return ExecutionResult.ok(f"По запросу «{query}» в базе книг точных совпадений не найдено.", iterations=1)
    except Exception as e:
        return ExecutionResult.fail(f"Ошибка поиска в книгах: {e}", is_retryable=False)


def execute_direct_bom(input_text: str) -> ExecutionResult:
    """Выполняет прямой запуск конвейера спецификаций Minimal CAD Takeoff."""
    try:
        # Ищем путь к файлу в тексте задачи
        dxf_match = re.search(r"([a-zA-Z]:[\\/][^ \r\n\t]+\.(?:dxf|dwg))|([^ \r\n\t]+\.(?:dxf|dwg))", input_text)
        if dxf_match:
            dxf_path = dxf_match.group(0)
            from tools.pipeline_bom import run_bom_pipeline
            report = run_bom_pipeline(dxf_path)
            return ExecutionResult.ok(f"### 📐 Спецификация оборудования (Minimal CAD Takeoff):\n\n{report}", iterations=1)
        else:
            return ExecutionResult.fail("Не указан путь к файлу чертежа (.dxf/.dwg)", is_retryable=False)
    except Exception as e:
        return ExecutionResult.fail(f"Ошибка BOM пайплайна: {e}", is_retryable=False)


def dispatch_and_execute(task_text: str, worker_hint: str = "") -> ExecutionResult:
    """
    Главная точка входа: оценивает задачу, применяет детерминированные инструменты
    или специализированные SLM и возвращает гарантированный ExecutionResult.
    """
    category, meta = classify_task(task_text, hint=worker_hint)

    # 1. ДЕТЕРМИНИРОВАННЫЙ КАЛЬКУЛЯТОР (0 токенов, <1 мс)
    if category == TaskCategory.DETERMINISTIC_CALC:
        expr = meta.get("expr", task_text)
        ok, res = safe_eval_math_expression(expr)
        if ok:
            return ExecutionResult.ok(f"Результат вычисления `{expr}` = **{res}**", iterations=1)
        # Если калькулятор не справился с формулировкой — передаем далее в LLM
        pass

    # 2. ПРЯМОЙ ПОИСК В КНИГАХ (0 токенов, <50 мс)
    if category == TaskCategory.DIRECT_BOOK_SEARCH:
        query = meta.get("query", task_text)
        return execute_direct_book_search(query)

    # 3. ПРЯМОЙ BOM ПАЙПЛАЙН (0 токенов)
    if category == TaskCategory.DIRECT_BOM_PIPELINE:
        bom_res = execute_direct_bom(task_text)
        if bom_res.success:
            return bom_res
        # Если путь не распознан — продолжаем через ReAct оркестратор
        pass

    # 4. МНОГОШАГОВЫЙ ПЛАН (TASK DECOMPOSITION)
    if category == TaskCategory.MULTI_STEP_PLAN:
        steps = TaskDecomposer.decompose(task_text)
        if len(steps) > 1:
            step_reports = []
            context_accumulator = []
            for s in steps:
                step_task_text = s.payload
                if context_accumulator:
                    # Добавляем краткую выжимку предыдущих результатов в контекст
                    step_task_text += f"\n[Контекст предыдущих шагов]:\n" + "\n".join(context_accumulator[-2:])

                step_res = dispatch_and_execute(step_task_text, worker_hint=worker_hint)
                if not step_res.success:
                    return ExecutionResult.fail(
                        f"Сбой на шаге {s.step} ({s.title}): {step_res.error}",
                        is_retryable=step_res.is_retryable,
                        iterations=len(step_reports) + 1
                    )
                s.result = step_res.result
                step_reports.append(f"#### Этап {s.step}: {s.title}\n{step_res.result}\n")
                context_accumulator.append(f"Шаг {s.step} ({s.title}): {step_res.result[:150]}")

            summary_output = (
                f"### 📋 Комплексный отчет выполнения ({len(steps)} этапа):\n\n"
                + "\n---\n".join(step_reports)
            )
            return ExecutionResult.ok(summary_output, iterations=len(steps))

    # 5. ВЫЗОВ СПЕЦИАЛИЗИРОВАННОЙ МОДЕЛИ ЧЕРЕЗ ORCHESTRATOR
    chosen_model = meta.get("model", "qwen2.5-coder:7b")
    orchestrator.MODEL_NAME = chosen_model
    ollama_tools, tools_map = orchestrator.load_tools_registry()
    system_prompt = orchestrator.load_agents_rules()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_text}
    ]
    return orchestrator.execute_task(messages, ollama_tools, tools_map)

