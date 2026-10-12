# -*- coding: utf-8 -*-
"""
doc_reader.py — Универсальное извлечение чистого текста из офисных документов.

Поддерживаемые форматы:
- DOCX (Word документы) — параграфы и таблицы (python-docx)
- XLSX (Excel таблицы) — листы, строки, ячейки (openpyxl)
- PPTX (Презентации) — слайды, текстовые блоки (python-pptx)
- TXT, MD, CSV, JSON, LOG, PY, YAML, XML, HTML — прямое чтение с автоопределением кодировок (utf-8 / cp1251)

Вывод: чистый структурированный текст, оптимизированный под контекст LLM (до 4000 символов в stdout).
"""
import os
import sys
import json
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOOL_SPEC = {
    "name": "doc_reader",
    "description": "Универсальное извлечение текста из офисных и текстовых документов (DOCX, XLSX, PPTX, CSV, TXT, MD, HTML).",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Полный или относительный путь к файлу документа",
            },
            "max_lines": {
                "type": "integer",
                "description": "Максимум строк для извлечения (по умолчанию: 150)",
            },
        },
        "required": ["file_path"],
    },
}


def read_docx(path, max_lines=150):
    try:
        import docx
        doc = docx.Document(path)
        lines = []
        for p in doc.paragraphs:
            text = p.text.strip()
            if text:
                lines.append(text)
                if len(lines) >= max_lines:
                    break
        for table in doc.tables:
            if len(lines) >= max_lines:
                break
            for row in table.rows:
                row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if row_text:
                    lines.append(f"[Таблица]: {row_text}")
                    if len(lines) >= max_lines:
                        break
        return "\n".join(lines)
    except Exception as e:
        return f"[!] Ошибка чтения DOCX: {e}"


def read_xlsx(path, max_lines=150):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        lines = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            lines.append(f"--- Лист: {sheet_name} ---")
            for row in sheet.iter_rows(values_only=True):
                non_empty = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
                if non_empty:
                    lines.append(" | ".join(non_empty))
                    if len(lines) >= max_lines:
                        break
            if len(lines) >= max_lines:
                break
        wb.close()
        return "\n".join(lines)
    except Exception as e:
        return f"[!] Ошибка чтения XLSX: {e}"


def read_pptx(path, max_lines=150):
    try:
        from pptx import Presentation
        prs = Presentation(path)
        lines = []
        for idx, slide in enumerate(prs.slides, 1):
            lines.append(f"--- Слайд {idx} ---")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        text = paragraph.text.strip()
                        if text:
                            lines.append(text)
                            if len(lines) >= max_lines:
                                break
            if len(lines) >= max_lines:
                break
        return "\n".join(lines)
    except Exception as e:
        return f"[!] Ошибка чтения PPTX: {e}"


def read_plain(path, max_lines=150):
    encodings = ["utf-8", "cp1251", "latin-1"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, errors="replace") as f:
                lines = []
                for _ in range(max_lines):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line.rstrip())
                return "\n".join(lines)
        except Exception:
            continue
    return "[!] Не удалось прочитать текстовый файл ни в одной из кодировок."


def read_document(file_path, max_lines=150):
    if not os.path.exists(file_path):
        return f"[-] Файл не найден: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".docx":
        return read_docx(file_path, max_lines=max_lines)
    elif ext in [".xlsx", ".xlsm"]:
        return read_xlsx(file_path, max_lines=max_lines)
    elif ext == ".pptx":
        return read_pptx(file_path, max_lines=max_lines)
    elif ext in [".txt", ".md", ".csv", ".json", ".log", ".py", ".yaml", ".yml", ".xml", ".html", ".htm"]:
        return read_plain(file_path, max_lines=max_lines)
    else:
        return f"[!] Неподдерживаемый формат '{ext}'. Поддерживаются: DOCX, XLSX, PPTX, CSV, TXT, MD, JSON, HTML."


def main():
    parser = argparse.ArgumentParser(description="Универсальное чтение документов")
    parser.add_argument("file_path", help="Путь к файлу")
    parser.add_argument("--lines", "-n", type=int, default=150, help="Максимум строк")
    args = parser.parse_args()

    content = read_document(args.file_path, max_lines=args.lines)
    print(content)


if __name__ == "__main__":
    main()
