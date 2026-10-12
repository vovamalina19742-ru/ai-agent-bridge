import os
import sys
import argparse
import pymupdf as fitz

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Отключаем внутренние ошибки MuPDF
try:
    fitz.TOOLS.mupdf_display_errors(False)
except Exception:
    pass

TOOL_SPEC = {
    "name": "cad_extractor",
    "description": "Извлекает текстовые слои, выноски, спецификации и геометрические примечания из инженерных чертежей PDF. Возвращает структурированный текст чертежа.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Путь к файлу чертежа (PDF)"
            }
        },
        "required": ["file_path"]
    }
}

def extract_blueprint_data(file_path):
    clean_path = file_path.strip().strip("'\"")
    if not os.path.exists(clean_path):
        print(f"[-] Файл чертежа не найден: {clean_path}")
        return

    try:
        doc = fitz.open(clean_path)
        print(f"[+] Чертеж открыт успешно: {os.path.basename(clean_path)} (страниц: {len(doc)})")
        print("=" * 60)
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            text = page.get_text()
            if text:
                print(f"--- Лист {page_idx + 1} ---")
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                print("\n".join(lines))
        doc.close()
    except Exception as e:
        print(f"[!] Ошибка парсинга чертежа: {e}")

def main():
    parser = argparse.ArgumentParser(description="Извлечение инженерных данных и текста из чертежей PDF")
    parser.add_argument("file_path", type=str, help="Путь к файлу чертежа PDF")
    args = parser.parse_args()

    extract_blueprint_data(args.file_path)

if __name__ == "__main__":
    main()
