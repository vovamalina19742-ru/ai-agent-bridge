# -*- coding: utf-8 -*-
"""
bom_optimizer.py — Промышленный модуль оптимизации раскроя и каталогов (Lighting Estimator PRO -> Pipeline BOM).

Инженерные расчеты:
1. Раскрой хлыстов треков (2.0м и 3.0м) с минимизацией отходов.
2. Автоматический расчет фурнитуры: заглушки (2 * N линий), стыковочные коннекторы, углы 90°.
3. Расчет бобин LED ленты (кратность 5м) и подбор блоков питания с запасом мощности 35%.
4. Раскрой профилей теневого шва KRAAB GIPPS, SLK01, KN01 и Topmet/KLUS.
5. Классификация слоев чертежей (CAD/PDF) и автодетект масштаба.
"""

import math
import re
from typing import Dict, List, Any, Tuple


STANDARD_WASTE_FACTORS = {
    "profile_straight": 1.05,       # 5% отход при прямых резах
    "profile_miter_corners": 1.10,  # 10% отход при запилах под 45°
    "led_tape": 1.08,               # 8% технологический запас на пайку и кратность реза
    "cable_runs": 1.15,             # 15% запас на провисы и спуски
    "driver_safety_margin": 1.35,   # 35% запас мощности блоков питания
}

LIGHTING_KEYWORDS = ["electro", "electrica", "lighting", "lumini", "iluminat", "lamp", "sviet", "track", "свет", "электр", "освещен"]
SOCKET_KEYWORDS = ["prize", "socket", "rozet", "розет"]
ARCH_KEYWORDS = ["stena", "peregorodka", "wall", "dveri", "door", "beam", "xref", "mob", "mebel", "стен", "двер"]
NOISE_KEYWORDS = ["strih", "hatch", "штрих"]
DIM_KEYWORDS = ["razmer", "dim", "cota", "размер"]


def classify_layer_name(name: str) -> str:
    """Классифицирует имя слоя CAD/PDF по семантической роли."""
    n = name.lower()
    if any(k in n for k in LIGHTING_KEYWORDS):
        return "lighting"
    if any(k in n for k in SOCKET_KEYWORDS):
        return "socket"
    if any(k in n for k in NOISE_KEYWORDS):
        return "noise"
    if any(k in n for k in DIM_KEYWORDS):
        return "dimension"
    if any(k in n for k in ARCH_KEYWORDS):
        return "architecture"
    return "other"


def detect_drawing_scale(text: str) -> Tuple[float, str]:
    """
    Считывание масштаба чертежа из штампа/аннотаций (1:20, 1:25, 1:50, 1:75, 1:100, 1:200).
    Возвращает (pts_per_meter, scale_str).
    """
    match = re.search(r'(?:масштаб|scale|scara|ech|м|m)[\s.:]*1\s*[:/]\s*(\d+)', text, re.IGNORECASE)
    if not match:
        match = re.search(r'\b1\s*[:/]\s*(20|25|50|75|100|200)\b', text)

    if match:
        scale_denom = int(match.group(1))
        if scale_denom > 0:
            pts_per_m = (1000.0 / scale_denom) * (72.0 / 25.4)
            return round(pts_per_m, 2), f"1:{scale_denom}"

    return 56.69, "1:50 (по умолчанию)"


class BOMOptimizer:
    """Инженерный калькулятор раскроя трековых систем, профилей и комплектующих."""

    @staticmethod
    def calculate_track_bom(track_lines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Рассчитывает оптимальную комбинацию хлыстов (2м и 3м), токовводов,
        торцевых заглушек и стыковочных коннекторов.
        """
        profiles_2m = 0
        profiles_3m = 0
        power_feeds = 0
        end_caps = 0
        straight_connectors = 0
        corner_connectors = 0
        total_net_length = sum(line_item.get("length_m", 0.0) for line_item in track_lines)

        for line in track_lines:
            L = float(line.get("length_m", 0.0))
            if L <= 0:
                continue

            power_feeds += 1
            end_caps += 2

            if line.get("has_corner_90", False):
                corner_connectors += 1

            if L <= 2.0:
                profiles_2m += 1
            elif L <= 3.0:
                profiles_3m += 1
            elif L <= 4.0:
                profiles_2m += 2
                straight_connectors += 1
            elif L <= 5.0:
                profiles_3m += 1
                profiles_2m += 1
                straight_connectors += 1
            elif L <= 6.0:
                profiles_2m += 3
                straight_connectors += 2
            else:
                n3 = int(L // 3.0)
                rem = L % 3.0
                profiles_3m += n3
                if rem > 0:
                    if rem <= 2.0:
                        profiles_2m += 1
                    else:
                        profiles_3m += 1
                straight_connectors += (n3 + (1 if rem > 0 else 0) - 1)

        base_spots = math.ceil(total_net_length * 1.6)

        return {
            "total_net_m": round(total_net_length, 2),
            "profiles_2000mm": profiles_2m,
            "profiles_3000mm": profiles_3m,
            "total_purchase_m": (profiles_2m * 2.0) + (profiles_3m * 3.0),
            "power_feeds": power_feeds,
            "end_caps": end_caps,
            "straight_connectors": straight_connectors,
            "corner_connectors_90": corner_connectors,
            "recommended_spots_15w": base_spots,
        }

    @staticmethod
    def calculate_led_tape_and_profiles_bom(
        runs: List[Dict[str, Any]],
        waste_factor: float = 1.08
    ) -> Dict[str, Any]:
        """
        Расчет бобин светодиодной ленты (кратность 5м) и блоков питания (запас мощности 35%).
        """
        total_net_m = sum(r.get("length_m", 0.0) for r in runs)
        total_rolls_5m = 0
        total_profile_sticks_2m = 0
        driver_items = []

        standard_driver_ratings = [60, 100, 150, 200, 240, 320, 350]

        for r in runs:
            L = float(r.get("length_m", 0.0))
            if L <= 0:
                continue

            gross_l = L * waste_factor
            rolls = math.ceil(gross_l / 5.0)
            total_rolls_5m += rolls

            sticks = math.ceil(gross_l / 2.0)
            total_profile_sticks_2m += sticks

            w_per_m = float(r.get("power_w_per_m", 14.4))
            p_load = L * w_per_m
            p_driver_min = p_load * STANDARD_WASTE_FACTORS["driver_safety_margin"]

            driver_w = next((d for d in standard_driver_ratings if d >= p_driver_min), math.ceil(p_driver_min / 50) * 50)
            driver_items.append({
                "length_m": L,
                "load_w": round(p_load, 1),
                "driver_w": driver_w,
            })

        return {
            "total_net_m": round(total_net_m, 2),
            "total_rolls_5m": total_rolls_5m,
            "total_purchase_tape_m": total_rolls_5m * 5.0,
            "profile_sticks_2000mm": total_profile_sticks_2m,
            "profile_total_m": total_profile_sticks_2m * 2.0,
            "drivers": driver_items,
        }


BRAND_CATALOG = {
    "kraab": {
        "name": "KRAAB Systems",
        "items": {
            "GIPPS": {
                "name": "Профиль теневого шва KRAAB GIPPS L=2.0м (ГКЛ 12.5мм)",
                "unit": "шт.",
                "accessories": ["демпферная лента", "стыковочные штифты 2мм"]
            },
            "SLK01": {
                "name": "Теневой плинтус KRAAB SLK01 L=2.0м с пазом под LED",
                "unit": "шт.",
                "accessories": ["вставка плинтуса", "уголок внутренний/внешний"]
            },
            "KN01": {
                "name": "Скрытый карниз для штор KRAAB KN01 L=2.0м (паз под LED)",
                "unit": "шт.",
                "accessories": ["крючки для штор", "торцевые заглушки"]
            }
        }
    },
    "neko": {
        "name": "Neko Lighting",
        "items": {
            "ML26_R2000": {
                "name": "Магнитный трековый профиль Neko Moduline 26 L=2000мм 48V",
                "unit": "шт."
            },
            "ML26_R3000": {
                "name": "Магнитный трековый профиль Neko Moduline 26 L=3000мм 48V",
                "unit": "шт."
            },
            "ML26_ENDCAP": {
                "name": "Торцевая заглушка Neko Moduline 26 (пара)",
                "unit": "компл."
            },
            "ML26_RLC": {
                "name": "Прямой токопроводящий коннектор Neko Moduline 26",
                "unit": "шт."
            },
            "ML26_TACM": {
                "name": "Угловой коннектор Neko Moduline 26 90°",
                "unit": "шт."
            }
        }
    }
}
