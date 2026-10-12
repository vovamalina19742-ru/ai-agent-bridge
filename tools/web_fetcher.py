import os
import sys
import re
import json
import argparse
from datetime import datetime
import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOOL_SPEC = {
    "name": "web_fetcher",
    "description": "Загружает веб-страницу по URL, проверяет доступность сайта и извлекает чистый текст без HTML-мусора. Автоматически сохраняет ссылку в базу saved_links.json.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Полный URL веб-страницы (например: https://example.com или https://github.com/user/repo)"
            },
            "mode": {
                "type": "string",
                "description": "Режим: 'text' (извлечь текст статьи/страницы) или 'status' (только код ответа и заголовки). По умолчанию 'text'."
            }
        },
        "required": ["url"]
    }
}

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
SAVED_LINKS_FILE = os.path.join(PROJECT_ROOT, "saved_links.json")

def save_link_entry(url, title, summary):
    """Автоматически сохраняет ссылку с метаданными в saved_links.json (с защитой от дублей)."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    clean_summary = summary.strip()[:500] if summary else ""

    data = []
    if os.path.exists(SAVED_LINKS_FILE):
        try:
            with open(SAVED_LINKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []

    # Защита от дублей: если URL уже есть, обновляем таймстамп, заголовок и саммари
    updated = False
    for item in data:
        if item.get("url") == url:
            item["timestamp"] = now_str
            item["title"] = title
            item["summary"] = clean_summary
            updated = True
            break

    if not updated:
        data.append({
            "url": url,
            "timestamp": now_str,
            "title": title,
            "summary": clean_summary
        })

    try:
        with open(SAVED_LINKS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Предупреждение: Не удалось сохранить ссылку в saved_links.json: {e}")

def fetch_url(url, mode="text", max_chars=1800):
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    print(f"[*] Запрос к {url} (режим: {mode})...")

    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=12)
        status_code = resp.status_code

        if mode == "status":
            print(f"[+] HTTP Статус: {status_code} {resp.reason}")
            print(f"    Сервер: {resp.headers.get('Server', 'Не указан')}")
            print(f"    Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
            if status_code == 200:
                save_link_entry(url, "Статус-проверка", f"HTTP {status_code} {resp.reason}")
            return

        html_text = resp.text

        # Парсим через BeautifulSoup
        soup = BeautifulSoup(html_text, "html.parser")

        # Удаляем лишний шум
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else "Без заголовка"
        
        # Для GitHub и документаций: извлекаем README или основную статью
        content_container = (
            soup.find(id="readme") or 
            soup.find("article", class_=lambda c: c and "markdown-body" in c) or 
            soup.find("main") or 
            soup
        )
        text = content_container.get_text(separator=" ", strip=True)
        # Убираем множественные пробелы
        text = re.sub(r"\s+", " ", text)

        # Автоматическое сохранение метаданных при HTTP 200
        if status_code == 200:
            save_link_entry(url, title, text)

        print(f"\n[+] Сайт: {url} (HTTP {status_code})")
        print(f"📌 Заголовок: {title}")
        print("-" * 50)
        
        if text:
            snippet = text[:max_chars]
            print(snippet)
            if len(text) > max_chars:
                print(f"\n... [Текст сокращен, показано {max_chars} из {len(text)} символов]")
        else:
            print("[-] На странице не найдено текстового содержимого.")

    except requests.exceptions.SSLError:
        print(f"[!] Ошибка SSL-сертификата при обращении к {url}")
    except requests.exceptions.ConnectionError:
        print(f"[-] Не удалось подключиться к сайту {url} (хост недоступен или заблокирован).")
    except requests.exceptions.Timeout:
        print(f"[!] Таймаут подключения к {url} (более 12 сек).")
    except Exception as e:
        print(f"[!] Ошибка при запросе к {url}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Утилита для загрузки и очистки веб-страниц")
    parser.add_argument("url", type=str, help="URL сайта")
    parser.add_argument("mode", type=str, nargs="?", default="text", choices=["text", "status"], help="Режим работы")
    args = parser.parse_args()

    fetch_url(args.url, args.mode)
