# -*- coding: utf-8 -*-
r"""
pipeline_bom.py — Автономный конвейер Blueprint-to-BOM (Minimal CAD Takeoff).

Регламент:
Строго минималистичный подсчет: «№», «Наименование», «Количество шт./м», итоговая строка.
Без цен и сметных накруток.
Выгрузка: чистый Markdown в консоль + экспорт в CSV для CRM.
"""

import os
import sys
import re
import csv
import math
import argparse
from typing import List, Dict, Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

try:
    from tools.bom_optimizer import (
        BOMOptimizer,
        BRAND_CATALOG,
        classify_layer_name,
        detect_drawing_scale,
    )
except ImportError:
    from bom_optimizer import (
        BOMOptimizer,
        BRAND_CATALOG,
        classify_layer_name,
        detect_drawing_scale,
    )

# Каталог известных брендов и компонентов (KRAAB, Topmet, Neko, KLUS, Smart Light, Mean Well)
CATALOG_DB = {
    # Специфичные светодиодные ленты
    "led_rgbw_24v": {
        "pattern": r"(rgb|rgbw|цветн).*лент",
        "name": "Светодиодная лента RGBW 24V 18W/m",
        "unit": "м",
    },
    # Блоки питания (сначала 200W, затем 100W)
    "power_supply_200w": {
        "pattern": r"(блок\s*питан|драйвер|трансформатор|mean\s*well).*(150w|200w|240w|350w)",
        "name": "Блок питания Mean Well LRS-200-24 (200W 24V IP20)",
        "unit": "шт.",
    },
    "power_supply_100w": {
        "pattern": r"(блок\s*питан|драйвер|трансформатор|mean\s*well|источник).*(100w|100вт|120w|120вт)",
        "name": "Блок питания Mean Well LRS-100-24 (100W 24V IP20)",
        "unit": "шт.",
    },
    # Профили KRAAB Systems
    "profile_shadow_kraab_gipps": {
        "pattern": r"(теневой|шов|kraab).*(gipps|гкл|потолочн)",
        "name": "Профиль теневого шва KRAAB GIPPS (ГКЛ 12.5мм, L=2.0м)",
        "unit": "м",
    },
    "profile_baseboard_kraab_slk01": {
        "pattern": r"(теневой|шов|kraab).*(плинтус|slk|slk01)",
        "name": "Теневой плинтус скрытого монтажа KRAAB SLK01 (L=2.0м)",
        "unit": "м",
    },
    "profile_curtain_kraab_kn01": {
        "pattern": r"(скрыт|карниз|штор|kn01|kraab).*(карниз|kn01)",
        "name": "Скрытый потолочный карниз для штор KRAAB KN01 (L=2.0м)",
        "unit": "м",
    },
    "profile_shadow_kraab": {
        "pattern": r"(теневой|шов|kraab|gipps|плинтус|карниз)",
        "name": "Профиль теневого шва KRAAB GIPPS (ГКЛ 12.5мм)",
        "unit": "м",
    },
    "profile_klus": {
        "pattern": r"(klus|giza|pds|kozel)",
        "name": "Алюминиевый профиль KLUS с рассеивателем (L=2.0м)",
        "unit": "м",
    },
    "profile_topmet": {
        "pattern": r"(профиль|короб|врезн|накладн).*(topmet|linea|slim|декор|алюм)",
        "name": "Алюминиевый профиль Topmet с матовым рассеивателем (L=2.0м)",
        "unit": "м",
    },
    # Трековые системы Neko Moduline
    "track_48v": {
        "pattern": r"(трек|магнитн|neko|moduline|шинопровод)",
        "name": "Магнитный трековый профиль Neko Moduline 26 48V (L=2.0м)",
        "unit": "м",
    },
    # Светильники
    "downlight": {
        "pattern": r"(светильник|точечн|downlight|спот|встраиваем)",
        "name": "Встраиваемый точечный светильник Deep Anti-Glare 10W",
        "unit": "шт.",
    },
    # Общая светодиодная лента (в самом конце как fallback)
    "led_cob_24v": {
        "pattern": r"(лента|полоса|подсветк|cob|smd)",
        "name": "Светодиодная лента COB 24V 10W/m CRI>90 3000K",
        "unit": "м",
    },
}

# Дополняем каталог позициями из BRAND_CATALOG
for _brand_info in BRAND_CATALOG.values():
    for _item_code, _bitem in _brand_info.get("items", {}).items():
        _code = _item_code.lower()
        if _code not in CATALOG_DB:
            CATALOG_DB[_code] = {
                "pattern": rf"({re.escape(_code)}|{re.escape(_bitem['name'].lower())})",
                "name": _bitem["name"],
                "unit": _bitem.get("unit", "шт."),
            }

TOOL_SPEC = {
    "name": "pipeline_bom",
    "description": "Автоматический конвейер Blueprint-to-BOM (Minimal CAD Takeoff): парсит чертежи PDF или спецификации, сопоставляет оборудование с каталогами (TOPMET, KRAAB, Neko) и формирует минималистичную спецификацию (№, Наименование, Кол-во).",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Путь к чертежу PDF или файлу спецификации (DOCX, XLSX, TXT, PDF)",
            },
            "reserve_percent": {
                "type": "number",
                "description": "Технологический запас на раскрой лент и профилей в % (по умолчанию 5%)",
            },
            "output_csv": {
                "type": "string",
                "description": "Путь для сохранения CSV (опционально)",
            },
        },
        "required": ["file_path"],
    },
}


def extract_raw_text(file_path: str) -> str:
    """Извлекает весь доступный текст из чертежа или документа."""
    clean_path = file_path.strip().strip("'\"")
    if not os.path.exists(clean_path):
        return ""

    ext = os.path.splitext(clean_path)[1].lower()

    if ext == ".pdf":
        try:
            import pymupdf as fitz
            doc = fitz.open(clean_path)
            pages_text = []
            for page in doc:
                t = page.get_text()
                if t and t.strip():
                    pages_text.append(t)
            doc.close()
            if pages_text:
                return "\n".join(pages_text)
        except Exception:
            pass

        # Если текстовый слой пуст или произошел сбой — пробуем OCR
        try:
            from tools.ocr_scans import ocr_file
            return ocr_file(clean_path)
        except Exception:
            pass

    elif ext in [".txt", ".csv", ".tsv"]:
        try:
            with open(clean_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            pass

    elif ext in [".docx", ".xlsx", ".pptx"]:
        try:
            from tools.doc_reader import read_document
            return read_document(clean_path)
        except Exception:
            pass

    elif ext in [".dxf", ".dwg"]:
        try:
            from tools.cad_extractor import extract_cad_entities
            res = extract_cad_entities(clean_path)
            if res and isinstance(res, dict):
                texts = res.get("texts", []) or []
                layers = [f"Слой: {k} (объектов: {v})" for k, v in (res.get("layers", {}) or {}).items()]
                return "\n".join(texts + layers)
        except Exception:
            pass
        try:
            import ezdxf
            doc = ezdxf.readfile(clean_path)
            msp = doc.modelspace()
            extracted = []
            scale = 1.0
            layer_lengths: Dict[str, float] = {}
            for e in msp:
                dxftype = e.dxftype()
                layer = getattr(e.dxf, "layer", "0")
                if dxftype in ("TEXT", "MTEXT"):
                    t = e.dxf.text if hasattr(e.dxf, "text") else getattr(e, "text", "")
                    if t:
                        extracted.append(t)
                        sc = detect_drawing_scale(t)
                        if sc:
                            scale = sc
                elif dxftype == "LINE":
                    start = e.dxf.start
                    end = e.dxf.end
                    length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
                    layer_lengths[layer] = layer_lengths.get(layer, 0.0) + length
                elif dxftype == "LWPOLYLINE":
                    points = e.get_points(format="xy")
                    plen = 0.0
                    for i in range(len(points) - 1):
                        p1, p2 = points[i], points[i + 1]
                        plen += ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
                    layer_lengths[layer] = layer_lengths.get(layer, 0.0) + plen

            for layer, raw_len in layer_lengths.items():
                cat = classify_layer_name(layer)
                if cat != "other" and raw_len > 0:
                    scaled_len = raw_len * scale
                    len_m = (scaled_len / 1000.0) if scaled_len > 50 else scaled_len
                    extracted.append(f"Слой {layer} ({cat}): {round(len_m, 2)}м")

            if extracted:
                return "\n".join(extracted)
        except Exception:
            pass

    return ""


def parse_items_from_text(raw_text: str, reserve_percent: float = 5.0) -> List[Dict[str, Any]]:
    """
    Интеллектуальный анализатор строк и выносок чертежа.
    Находит длины, штучные позиции и привязывает к каталожной базе.
    """
    items_map: Dict[str, Dict[str, Any]] = {}

    lines = raw_text.splitlines()
    for line in lines:
        line_clean = line.strip()
        if not line_clean or len(line_clean) < 3:
            continue

        # Ищем совпадения метража: "15.4 м", "15,4м", "15400 мм", "L=12.5m"
        length_match = re.search(r'(?:L\s*[:=]\s*|длина\s*[:=]\s*)?(\d+[.,]?\d*)\s*(?:м|m|метр|пог\.?\s*м)', line_clean, re.IGNORECASE)
        # Ищем совпадения штук: "12 шт", "4 x", "кол-во: 8"
        qty_match = re.search(r'(?:кол-во\s*[:=]\s*|x\s*|×\s*)?(\d+)\s*(?:шт|pcs|штук|точек)', line_clean, re.IGNORECASE)
        mm_match = re.search(r'(\d{3,5})\s*(?:мм|mm)', line_clean, re.IGNORECASE)

        val = 0.0
        unit = "шт."
        # Приоритет 1: Явные штуки (шт, pcs)
        if qty_match:
            try:
                val = float(qty_match.group(1))
                unit = "шт."
            except Exception:
                pass
        # Приоритет 2: Метраж (м, пог.м, L=...)
        elif length_match:
            try:
                val = float(length_match.group(1).replace(",", "."))
                unit = "м"
            except Exception:
                pass
        # Приоритет 3: Миллиметры (переводим мм в метры, только если это не штучное изделие)
        elif mm_match:
            try:
                val = float(mm_match.group(1)) / 1000.0
                unit = "м"
            except Exception:
                pass

        if val <= 0:
            continue

        # Сопоставляем категорию по каталогу
        matched_cat = None
        for cat_id, cat_info in CATALOG_DB.items():
            if re.search(cat_info["pattern"], line_clean, re.IGNORECASE):
                matched_cat = cat_info
                break

        if matched_cat:
            cat_name = matched_cat["name"]
            req_unit = matched_cat["unit"]
        else:
            # Общее наименование, если не попало в каталог
            cat_name = f"Оборудование чертежа: {line_clean[:45]}..."
            req_unit = unit

        if cat_name not in items_map:
            items_map[cat_name] = {
                "name": cat_name,
                "unit": req_unit,
                "raw_qty": 0.0,
            }
        items_map[cat_name]["raw_qty"] += val

    # Автоматический инженерный расчет фурнитуры (Lighting Estimator PRO)
    # 1. Трековые системы: заглушки и прямые коннекторы
    track_keys = [k for k in items_map if "треков" in k.lower() or "moduline" in k.lower()]
    for tk in track_keys:
        track_len = items_map[tk]["raw_qty"]
        if track_len > 0:
            track_opt = BOMOptimizer.calculate_track_bom([{"length_m": track_len}])
            end_cap_name = "Торцевая заглушка Neko Moduline 26"
            if end_cap_name not in items_map and track_opt["end_caps"] > 0:
                items_map[end_cap_name] = {"name": end_cap_name, "unit": "шт.", "raw_qty": float(track_opt["end_caps"])}
            conn_name = "Прямой токопроводящий коннектор Neko Moduline 26"
            if conn_name not in items_map and track_opt["straight_connectors"] > 0:
                items_map[conn_name] = {"name": conn_name, "unit": "шт.", "raw_qty": float(track_opt["straight_connectors"])}

    # 2. Профиль KRAAB GIPPS: стыковочные штифты
    kraab_keys = [k for k in items_map if "kraab gipps" in k.lower()]
    for kk in kraab_keys:
        kraab_len = items_map[kk]["raw_qty"]
        if kraab_len > 0:
            pins_name = "Стыковочные штифты KRAAB 2мм"
            if pins_name not in items_map:
                pins_qty = max(2, int(math.ceil(kraab_len / 2.0)) * 2)
                items_map[pins_name] = {"name": pins_name, "unit": "шт.", "raw_qty": float(pins_qty)}

    # Применяем технологический запас на раскрой лент и профилей
    result_list = []
    for item in items_map.values():
        qty = item["raw_qty"]
        if item["unit"] == "м" and reserve_percent > 0:
            qty = qty * (1.0 + reserve_percent / 100.0)
            qty = round(qty, 2)
        else:
            qty = int(round(qty))
        result_list.append({
            "name": item["name"],
            "unit": item["unit"],
            "qty": qty,
        })

    return result_list


def generate_bom_takeoff(file_path: str, reserve_percent: float = 5.0, output_csv: str = None) -> str:
    """
    Генерирует лаконичную спецификацию строго по регламенту Minimal CAD Takeoff.
    Формат: №, Наименование, Количество шт./м, итоговая строка.
    Поддерживает как пути к файлам (.dxf/.dwg/.pdf/.txt), так и текстовые описания/выноски.
    """
    clean_input = file_path.strip().strip("'\"")
    if os.path.exists(clean_input):
        raw_text = extract_raw_text(clean_input)
        title = os.path.basename(clean_input)
    else:
        raw_text = clean_input
        title = "Текстовое задание"

    if not raw_text:
        return "[-] Не удалось извлечь инженерные данные."

    items = parse_items_from_text(raw_text, reserve_percent=reserve_percent)
    if not items:
        return "[-] Не обнаружено специфицируемых позиций освещения."

    # Формируем таблицу строго по регламенту Minimal CAD Takeoff
    lines = [
        f"СПЕЦИФИКАЦИЯ ОБОРУДОВАНИЯ (BOM TAKEOFF) — {title}",
        f"Технологический запас на раскрой: {reserve_percent}%",
        "",
        "| № | Наименование | Количество шт./м |",
        "|---|---|---|"
    ]

    total_meters = 0.0
    total_pcs = 0

    csv_rows = [["№", "Наименование", "Количество", "Ед. изм."]]

    for idx, it in enumerate(items, 1):
        name = it["name"]
        qty = it["qty"]
        unit = it["unit"]
        lines.append(f"| {idx} | {name} | {qty} {unit} |")
        csv_rows.append([str(idx), name, str(qty), unit])

        if unit == "м":
            total_meters += qty
        else:
            total_pcs += qty

    lines.append("|---|---|---|")
    summary_parts = []
    if total_meters > 0:
        summary_parts.append(f"{round(total_meters, 2)} пог. м")
    if total_pcs > 0:
        summary_parts.append(f"{total_pcs} шт.")

    total_str = ", ".join(summary_parts) if summary_parts else "0"
    lines.append(f"| **ИТОГО** | **Суммарный объем спецификации** | **{total_str}** |")

    # Экспорт в CSV (если указан или сохраняем по умолчанию рядом)
    csv_target = output_csv if output_csv else (os.path.splitext(clean_input)[0] + "_BOM.csv" if os.path.exists(clean_input) else None)
    if csv_target:
        try:
            with open(csv_target, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerows(csv_rows)
            lines.append(f"\n[✓] Экспорт для CRM успешно сохранен: {os.path.basename(csv_target)}")
        except Exception as e:
            lines.append(f"\n[!] Предупреждение: не удалось сохранить CSV: {e}")

    return "\n".join(lines)


# Публичный алиас функции конвейера
run_bom_pipeline = generate_bom_takeoff


def main():
    parser = argparse.ArgumentParser(description="Автоматический конвейер Blueprint-to-BOM (Minimal CAD Takeoff)")
    parser.add_argument("pos_file", nargs="?", default=None, help="Путь к чертежу PDF или спецификации")
    parser.add_argument("--file", "-f", default=None, help="Путь к файлу чертежа")
    parser.add_argument("--reserve", "-r", type=float, default=5.0, help="Запас на раскрой лент/профилей в % (по умолчанию 5%)")
    parser.add_argument("--csv", "-c", default=None, help="Путь к CSV для CRM")
    args = parser.parse_args()

    target = args.file or args.pos_file
    if not target:
        print("[-] Укажите файл чертежа: python pipeline_bom.py <чертеж.pdf>")
        return

    report = generate_bom_takeoff(target, reserve_percent=args.reserve, output_csv=args.csv)
    print(report)


if __name__ == "__main__":
    main()
