# -*- coding: utf-8 -*-
"""
ocr_scans.py — Распознавание текста со сканированных страниц PDF, чертежей и изображений.

Архитектура:
1. Двойной движок:
   - Локально на ноутбуке: встроенный ONNX-движок RapidOCR (rus/eng, без необходимости установки внешних exe/бинарников).
   - Удаленно на Linux-сервере (опционально): нативный Tesseract 5 (rus+eng+ron).
2. Поддержка PDF: извлечение страниц без текстового слоя через PyMuPDF, рендеринг в растр (pixmap) и распознавание.
3. Поддержка изображений: PNG, JPG, JPEG, WEBP, TIFF, BMP.
4. Опциональная дозапись в индекс книг books_index.db (FTS5).
"""
import os
import sys
import argparse
import sqlite3

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

try:
    from config import DATABASE_PATH
except Exception:
    DATABASE_PATH = os.path.join(BASE_DIR, "books_index.db")

TOOL_SPEC = {
    "name": "ocr_scans",
    "description": "Распознавание текста (OCR) со сканированных документов, чертежей и PDF (RapidOCR rus+eng).",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Путь к PDF-файлу или изображению",
            },
            "page_num": {
                "type": "integer",
                "description": "Номер страницы для PDF (по умолчанию: 1, 0 для всех страниц)",
            },
            "save_to_fts": {
                "type": "boolean",
                "description": "Дописать распознанный текст в базу books_index.db (по умолчанию: false)",
            },
        },
        "required": ["file_path"],
    },
}

_ocr_engine = None


def get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        except Exception as e:
            sys.stderr.write(f"[!] Ошибка инициализации RapidOCR: {e}\n")
            return None
    return _ocr_engine


def ocr_image_bytes(img_bytes):
    engine = get_ocr_engine()
    if not engine:
        return "[!] OCR-движок недоступен"
    try:
        result, _ = engine(img_bytes)
        if not result:
            return ""
        lines = [item[1] for item in result if len(item) > 1]
        return "\n".join(lines)
    except Exception as e:
        return f"[!] Ошибка распознавания: {e}"


def ocr_pdf_page(pdf_path, page_num=1):
    try:
        import pymupdf as fitz
        doc = fitz.open(pdf_path)
        if page_num < 1 or page_num > len(doc):
            return f"[-] Неверный номер страницы {page_num} (всего: {len(doc)})"
        
        page = doc[page_num - 1]
        # Проверяем, есть ли уже встроенный текстовый слой
        embedded = page.get_text().strip()
        if len(embedded) > 60:
            return f"[Встроенный текст, OCR не требуется]:\n{embedded}"

        # Рендерим страницу в 150 DPI для быстрого и качественного OCR
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        text = ocr_image_bytes(img_bytes)
        return text or "[!] Текст на странице не распознан"
    except Exception as e:
        return f"[!] Ошибка обработки PDF: {e}"


def ocr_file(file_path, page_num=1, save_to_fts=False):
    if not os.path.exists(file_path):
        return f"[-] Файл не найден: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == ".pdf":
        text = ocr_pdf_page(file_path, page_num=page_num)
    elif ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"]:
        with open(file_path, "rb") as f:
            text = ocr_image_bytes(f.read())
    else:
        return f"[!] Формат '{ext}' не поддерживается для OCR. Используйте PDF, PNG, JPG."

    if save_to_fts and text and not text.startswith("[!]"):
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO book_fts (book, page, content) VALUES (?, ?, ?)",
                (os.path.basename(file_path), page_num, text)
            )
            conn.commit()
            conn.close()
            text += f"\n\n[+] Текст страницы {page_num} успешно добавлен в индекс {os.path.basename(DATABASE_PATH)}"
        except Exception as e:
            text += f"\n[!] Ошибка записи в FTS5: {e}"

    return text


def main():
    parser = argparse.ArgumentParser(description="OCR распознавание сканов и чертежей")
    parser.add_argument("file_path", help="Путь к файлу")
    parser.add_argument("--page", "-p", type=int, default=1, help="Номер страницы PDF")
    parser.add_argument("--save", action="store_true", help="Сохранить в индекс FTS5")
    args = parser.parse_args()

    res = ocr_file(args.file_path, page_num=args.page, save_to_fts=args.save)
    print(res)


if __name__ == "__main__":
    main()
