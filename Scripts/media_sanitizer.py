import os
import sys
import subprocess
from pathlib import Path

# Автоматический перезапуск через виртуальное окружение .venv при запуске из системного python
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENV_PYTHON = _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if _VENV_PYTHON.exists() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
    try:
        import dotenv
    except ImportError:
        res = subprocess.run([str(_VENV_PYTHON)] + sys.argv, cwd=str(_PROJECT_ROOT))
        sys.exit(res.returncode)

import time
import json
import shutil
import socket
import traceback
import argparse
import threading
import webbrowser
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from io import BytesIO
from PIL import Image, ImageFilter
from dotenv import load_dotenv
from google import genai
from google.genai import types
from Scripts.bot.utils.alert_manager import setup_crash_reporter, send_sync_critical

# Настройка UTF-8 для консоли Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

MODEL_POOL = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]

# Папки для батч-обработки и ручной проверки
REVIEW_BASE = PROJECT_ROOT / "Data" / "manual_review_media"
REVIEW_PENDING = REVIEW_BASE / "pending"
REVIEW_READY = REVIEW_BASE / "ready"
BATCH_BASE = PROJECT_ROOT / "Data" / "review_batches"
AUDIT_LOG_PATH = BATCH_BASE / "audit_log.json"
SERVER_ERROR_LOG_PATH = BATCH_BASE / "server_errors.log"
AGENT_QUEUE_PATH = BATCH_BASE / "agent_queue.json"

_AUDIT_LOCK = threading.Lock()
_QUEUE_LOCK = threading.Lock()
_JOB_STATE = {
    "running": False,
    "batch_idx": None,
    "current": 0,
    "total": 0,
    "current_file": "",
    "done": False,
    "error": None,
    "report_url": None
}

SYSTEM_PROMPT = """You are an expert construction document redaction and OPSEC inspection assistant.
Your task is to analyze the provided image (Revit interface screenshot, drawing sheet, or GIF frame) and detect bounding boxes of SENSITIVE CORPORATE AND PERSONAL DATA that MUST BE BLURRED.

### TARGET ANCHORS TO DETECT & BLUR:

1. TOP RIBBON TABS (Вкладки ленты Revit):
   - Check top menu tabs for company names ("Dogma", "DOGMA", "Dogma_KP", "Dogma_ЭОМ/СС", etc.).
   - Note: In Revit, ribbon tab headers are only about 22-24 pixels tall at the very top.

2. PROPERTIES PALETTE & TABLES (Палитра свойств, спецификации, таблицы параметров):
   - Parameter Names (left column / table cells):
     * CRITICAL SURGICAL PREFIX-ONLY RULE: If a parameter name contains a company brand prefix (e.g. "DOGMA_...", "!dogma_...", "dogma_..."), your bounding box MUST cover ONLY the brand prefix characters ("DOGMA_", "!dogma_").
     * The functional, descriptive part of the parameter name (e.g. "МаркаВложенногоИзделия", "Арм_Назначение", "ОтметкаВерхаТекст", "Ш_ИмяЛиста", "ШифрФайла", "СекцияНомер") MUST REMAIN FULLY VISIBLE, READABLE, AND UNBLURRED.
     * NEVER blur the entire parameter name or the entire table row! Calculate [xmin, xmax] tightly around the prefix letters only. The typical aspect ratio of a brand prefix like "DOGMA_" is about 2.5:1 to 3.5:1 (width to height). If width exceeds 4 times height, you are capturing non-sensitive parameter names, which is strictly prohibited.
     * Standard generic GOST role names ("Разработал", "Проверил", "Чертил", "Проектировщик", "Руководитель", "ГИП", "Н.контр", "Лист", "Стадия", "Масштаб") are GENERIC TEMPLATE WORDS. DO NOT BLUR THEM!
   - Parameter Values (right column / table cells):
     * CRITICAL SURGICAL VALUE-ONLY RULE: Blur if the value field contains real person usernames or logins (e.g. "maltseva_e2", "ivanov_ii", employee names), surnames, or specific project codes (e.g. "1011_Р_ЖД_...", "02_КР#1012_...").
     * Bounding box MUST cover ONLY the sensitive token itself (e.g. just the login "maltseva_e2"). DO NOT blur the parameter label ("Редактирует", "Рабочий набор") or adjacent cells.
     * Do NOT blur empty fields, standard numbers, or role titles.

3. WINDOW TITLE BAR, STATUS BAR & WORKSETS (Заголовки, рабочие наборы, статусная строка):
   - Usernames / logins / workstation owners (e.g. "maltseva_e2", "petrov_p", "author").
   - Project codes or server paths: e.g. "1011_Р_ЖД_...", "1001_ПУШ-2", "02_КР#1012_...", "P:\\...".

4. DRAWING TITLE BLOCKS / STAMPS (Штампы чертежей):
   - Corner tables containing designer surnames or company logos.

### STRICT PRESERVATION RULES (DO NOT BLUR):
- DO NOT blur standard Russian Autodesk BIM parameters starting with "ADSK_" (e.g. "ADSK_Комплект чертежей", "ADSK_Марка конструкции", "ADSK_Наименование", "ADSK_Обозначение", "ADSK_Материал"). "ADSK_" is a public open community standard, NOT confidential corporate data!
- DO NOT blur generic structural rebar and steel assortment engineering parameters (e.g. "Сорт_...", "Сорт_МаркаИзделия", "вертСтержень_...", "Побр_...", "Подр_...", "хомут_...", "шпилька_...", "арматура_...", "длина_...", "шаг_...", "отступ_..."). These are generic descriptive civil engineering terms for rebar shapes, assortment ("Сорт_"), and offsets, NOT company brands!
- DO NOT blur internal Revit drawing view names, standard sheet names ("Лист: ..."), or sheet references in schedules ("см. лист 13, 14...") in title bars, properties, or tables (e.g. "..._Фрагмент 3_Узел 1", "Разрез 1-1", "План на отм. +0.000", "Спецификация арматуры", "Вид \"Лист: 11 - ...\"", "см. лист ..."). Only blur commercial real estate project codes (like "1001_ПУШ-2", "1011_Р_ЖД_...", "02_КР#1012_...").
- DO NOT blur standard role titles without surnames ("Разработал", "Проверил", "Чертил", "Проектировщик", "Руководитель", "ГИП", "Н.контр").
- DO NOT blur structural element marks or drawing view titles (e.g. "Пилон П-1.1", "Балка Бм-1", "Плита Пм-1", "Колонна К-1", "Армирование").
- DO NOT blur standard Revit ribbon buttons ("Архитектура", "Конструкция", "Изменить", "Вырезать") or ModPlus/BimStarter tabs.
- DO NOT blur pure geometry, rebars, dimensions, grids, elevation tags.
- If the image contains ONLY generic CAD geometry, clean plugin interfaces, or standard tools -> return an empty array [].

### OUTPUT FORMAT:
Return ONLY a valid JSON array of objects:
[
  {
    "box_2d": [ymin, xmin, ymax, xmax],
    "category": "ribbon_tab" | "properties_branded_param" | "window_titlebar" | "drawing_stamp" | "credentials" | "username",
    "text_snippet": "detected sensitive text",
    "matched_anchor": "matched keyword",
    "reason": "why this must be blurred"
  }
]
Coordinates [ymin, xmin, ymax, xmax] must be normalized integers 0..1000.
"""

def get_gemini_client():
    return genai.Client()

def extract_vlm_frame(file_path: Path) -> Image.Image:
    """Извлекает 1-й кадр для анализа VLM"""
    with Image.open(file_path) as img:
        return img.convert("RGB")

def scan_ribbon_strip(img: Image.Image, client) -> list[dict]:
    """Специализированный сканер верхней полосы ленты Revit с идеальной высотой 23-24px и защитой от галлюцинаций"""
    w, h = img.size
    # Лента вкладок Revit присутствует только в полноразмерных окнах Revit
    if w < 700 or h < 350:
        return []

    # Кропим верхнюю полосу 36 пикселей для контекста меню
    strip_h = min(h, 36)
    ribbon = img.crop((0, 0, w, strip_h))

    buf = BytesIO()
    ribbon.save(buf, format="PNG")
    part = types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")

    prompt = f"""You are an automated CAD redaction inspector.
Examine this top strip (height {strip_h}px, width {w}px) of an application screenshot.
FIRST: Determine if this image actually shows Autodesk Revit ribbon tab headers (e.g. tabs like 'Файл', 'Архитектура', 'Конструкция', 'Вставить', 'Управление').
If this image is NOT the Autodesk Revit ribbon tab strip, or if it has NO company-branded tabs, return [].

ONLY if there is an actual visible tab labeled with the corporate brand name (e.g. "Dogma", "Dogma_KP", "Dogma_ЭОМ/СС"):
Return its exact horizontal pixel boundaries [xmin, xmax] relative to this strip (0..{w}):
[
  {{"tab_name": "exact visible label", "xmin": ..., "xmax": ...}}
]
DO NOT hallucinate or guess. If no visible brand tab is present, return [].
"""
    for model_name in MODEL_POOL:
        try:
            res = client.models.generate_content(
                model=model_name,
                contents=[part, prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = json.loads(res.text.strip())
            boxes = []
            for item in data:
                tab_name = item.get("tab_name", "").strip()
                # Строгая валидация: должно реально содержать бренд dogma
                if "dogma" not in tab_name.lower():
                    continue

                xmin = max(0, int(item.get("xmin", 0)) - 4)
                xmax = min(w, int(item.get("xmax", w)) + 6)
                tab_w = xmax - xmin
                # Вкладка ленты имеет адекватную ширину (от 40 до 350 пикселей)
                if tab_w < 35 or tab_w > 400:
                    continue

                boxes.append({
                    # Высота вкладки ленты Revit строго 24px
                    "box_2d": [0, int(xmin * 1000 / w), int(24 * 1000 / h), int(xmax * 1000 / w)],
                    "category": "ribbon_tab",
                    "text_snippet": tab_name,
                    "matched_anchor": "Dogma",
                    "reason": "Corporate brand name in ribbon tab"
                })
            return boxes
        except Exception:
            continue
    return []

def refine_box_by_local_profiling(img: Image.Image, box_px: list[int], text_snippet: str = "", category: str = "") -> list[int]:
    """
    Микро-сегментатор текста на основе профилирования яркости/градаций серого внутри ячейки.
    Схлопывает бокс строго до горизонтальных границ конфиденциального префикса ('DOGMA_', '!dogma_')
    и отсекает полезный суффикс строки (русскоязычные инженерные параметры).
    Также гарантирует нормальную рабочую высоту бокса не менее 18-22px (дефолт 20px).
    """
    import numpy as np
    x1, y1, x2, y2 = box_px
    w_box = x2 - x1
    h_box = y2 - y1
    if h_box <= 0 or w_box <= 0:
        return box_px

    img_w, img_h = img.size

    # Гарантия нормальной высоты бокса (не менее 18-22px, дефолт 20px)
    target_h = max(h_box, 20)
    if h_box < target_h and img_h >= target_h:
        diff = target_h - h_box
        y1 = max(0, y1 - diff // 2)
        y2 = min(img_h, y1 + target_h)
        h_box = y2 - y1

    # Проверяем, относится ли бокс к корпоративным префиксам параметров
    is_branded_param = (category == "properties_branded_param") or any(
        k in text_snippet.upper() for k in ["DOGMA_", "!DOGMA_", "DOGMA", "!DOGMA"]
    )

    if not is_branded_param:
        return [x1, y1, x2, y2]

    # Высота одной стандартной строки интерфейса Revit (18-22px)
    # Защита от многострочных боксов: если h_box захватил несколько строк,
    # расчет ширины префикса ведется строго от высоты ОДНОЙ строки (single_row_h)
    single_row_h = max(16, min(22, h_box))
    max_prefix_px = int(single_row_h * 3.0)  # максимум ~55-65px для 6 символов "DOGMA_"

    # Если бокс уже узкий (ширина не превышает максимальный размер префикса)
    if w_box <= max_prefix_px and not any('\u0400' <= char <= '\u04FF' for char in text_snippet):
        return [x1, y1, x2, y2]

    # Анализируем горизонтальную полосу строки ячейки
    crop_x1 = max(0, x1 - 5)
    crop_x2 = min(img_w, x2 + 5)
    crop = img.crop((crop_x1, y1, crop_x2, y2)).convert("L")
    arr = np.array(crop)
    ch, cw = arr.shape
    offset_x = crop_x1

    border_pixels = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
    bg_val = float(np.median(border_pixels))
    if bg_val > 128:
        ink = (arr < bg_val - 25).astype(int)
    else:
        ink = (arr > bg_val + 25).astype(int)

    # Подавляем изолированные тонкие разделительные линии таблицы (толщина 1-2px, плотность > 80%)
    row_dens = ink.sum(axis=1) / max(1, cw)
    for r in range(ch):
        if row_dens[r] > 0.80:
            is_isolated_grid_line = True
            if r > 0 and row_dens[r-1] > 0.40 and r < ch - 1 and row_dens[r+1] > 0.40:
                is_isolated_grid_line = False
            if is_isolated_grid_line:
                ink[r, :] = 0

    # Находим диапазон строк с текстом шрифта
    v_prof = ink.sum(axis=1)
    text_rows = np.where(v_prof > 2)[0]
    if len(text_rows) >= 3:
        r_start, r_end = text_rows[0], text_rows[-1] + 1
        text_strip = ink[r_start:r_end, :]
    else:
        text_strip = ink

    # Горизонтальный профиль чернил
    h_prof = text_strip.sum(axis=0)

    # Ищем начало первого видимого символа токена (буквы D / !)
    active = np.where(h_prof > 0)[0]
    if len(active) == 0:
        new_w = min(max_prefix_px, int(single_row_h * 2.7))
        return [x1, y1, min(img_w, x1 + new_w), y2]

    c_first = active[0]
    abs_start_x = offset_x + c_first

    # Ожидаемый диапазон окончания префикса DOGMA_ (длина 6 символов)
    # При высоте single_row_h ~20px, 6 символов шрифта Revit занимают ~42..62px
    search_min_x = c_first + int(single_row_h * 1.8)
    search_max_x = min(cw, c_first + max_prefix_px)

    best_cut = None
    min_ink = 999999

    for c in range(min(search_min_x, cw - 1), min(search_max_x, cw)):
        win = h_prof[max(0, c - 1): min(cw, c + 2)]
        val = win.sum()
        if val == 0:
            best_cut = c
            break
        elif val < min_ink:
            min_ink = val
            best_cut = c

    if best_cut is not None:
        new_x2 = offset_x + best_cut + 2
    else:
        new_x2 = abs_start_x + int(single_row_h * 2.7)

    # Строгий предохранитель: граница префикса никогда не может превышать c_first + max_prefix_px
    strict_max_x = abs_start_x + max_prefix_px
    new_x2 = min(x2, min(strict_max_x, max(x1 + int(single_row_h * 1.8), new_x2)))
    return [x1, y1, new_x2, y2]


def shrink_box_to_prefix(box_norm: list[int], text_snippet: str, category: str, img: Image.Image = None, img_w: int = 1000, img_h: int = 1000) -> list[int]:
    """
    Сжимает нормализованный бокс [ymin, xmin, ymax, xmax] до точных границ корпоративного префикса.
    При наличии img задействует точный микро-сегментатор профилирования яркости ячейки.
    При отсутствии img задействует математический аспектный ограничитель (~3.2:1).
    """
    ymin, xmin, ymax, xmax = box_norm
    px_x1 = int(xmin * img_w / 1000)
    px_y1 = int(ymin * img_h / 1000)
    px_x2 = int(xmax * img_w / 1000)
    px_y2 = int(ymax * img_h / 1000)

    if img is not None:
        rx1, ry1, rx2, ry2 = refine_box_by_local_profiling(img, [px_x1, px_y1, px_x2, px_y2], text_snippet=text_snippet, category=category)
    else:
        h_px = max(1, px_y2 - px_y1)
        w_px = max(1, px_x2 - px_x1)
        single_h = max(16, min(22, h_px))
        aspect = w_px / single_h
        if aspect > 2.8 and ("DOGMA" in text_snippet.upper() or category == "properties_branded_param"):
            rx2 = px_x1 + int(single_h * 2.7)
        else:
            rx2 = px_x2
        rx1, ry1, ry2 = px_x1, px_y1, px_y2

    norm_ymin = max(0, min(1000, int(ry1 * 1000 / img_h)))
    norm_xmin = max(0, min(1000, int(rx1 * 1000 / img_w)))
    norm_ymax = max(0, min(1000, int(ry2 * 1000 / img_h)))
    norm_xmax = max(0, min(1000, int(rx2 * 1000 / img_w)))

    return [norm_ymin, norm_xmin, norm_ymax, norm_xmax]


def merge_and_pad_boxes(boxes: list[dict], img_w: int, img_h: int) -> list[dict]:
    """
    Интеллектуальное объединение и расширение боксов:
    1. Запрет слияния соседних строк параметров (дельта Y в экранных пикселях > 10px никогда не объединяется).
    2. Гарантия нормальной высоты боксов не менее 18-22px (дефолт 20px) для защиты верхушек букв.
    """
    if not boxes:
        return []

    parsed = []
    for b in boxes:
        box = b.get("box_2d", [0, 0, 0, 0])
        category = b.get("category", "")
        ymin, xmin, ymax, xmax = box

        # Для вкладок ленты: высота строго 24 пикселя (не захватывая кнопки ленты!)
        if category == "ribbon_tab" or (ymin <= 15 and ymax <= 50):
            ymin = 0
            ymax = int(24 * 1000 / img_h)
            xmin = max(0, xmin - int(5 * 1000 / img_w))
            xmax = min(1000, xmax + int(8 * 1000 / img_w))

        # Гарантия нормальной высоты (не менее 20px в реальных пикселях)
        h_px = (ymax - ymin) * img_h / 1000.0
        if h_px < 20.0 and img_h >= 20:
            mid_y_px = (ymin + ymax) * img_h / 2000.0
            new_y1_px = max(0.0, mid_y_px - 10.0)
            new_y2_px = min(float(img_h), new_y1_px + 20.0)
            ymin = int(new_y1_px * 1000.0 / img_h)
            ymax = int(new_y2_px * 1000.0 / img_h)

        parsed.append({
            "ymin": ymin, "xmin": xmin, "ymax": ymax, "xmax": xmax,
            "category": category,
            "texts": [b.get("text_snippet", "")],
            "anchors": [b.get("matched_anchor", "")],
            "reasons": [b.get("reason", "")]
        })

    # Слияние смежных боксов внутри одной строки
    merged = True
    while merged:
        merged = False
        new_list = []
        skip_indices = set()

        for i in range(len(parsed)):
            if i in skip_indices:
                continue
            curr = parsed[i]
            for j in range(i + 1, len(parsed)):
                if j in skip_indices:
                    continue
                other = parsed[j]

                # Слияние только при реальном пересечении боксов (без искусственного раздувания)
                v_overlap = not (curr["ymax"] <= other["ymin"] or other["ymax"] <= curr["ymin"])
                h_overlap = not (curr["xmax"] <= other["xmin"] or other["xmax"] <= curr["xmin"])

                # Защита строк таблиц: дельта Y в реальных экранных пикселях > 10px никогда не объединяется
                curr_mid_y_px = (curr["ymin"] + curr["ymax"]) * img_h / 2000.0
                other_mid_y_px = (other["ymin"] + other["ymax"]) * img_h / 2000.0
                delta_y_px = abs(curr_mid_y_px - other_mid_y_px)
                different_rows = delta_y_px > 10.0

                if v_overlap and h_overlap and not different_rows:
                    curr["ymin"] = min(curr["ymin"], other["ymin"])
                    curr["xmin"] = min(curr["xmin"], other["xmin"])
                    curr["ymax"] = max(curr["ymax"], other["ymax"])
                    curr["xmax"] = max(curr["xmax"], other["xmax"])
                    curr["texts"].extend(other["texts"])
                    curr["anchors"].extend(other["anchors"])
                    skip_indices.add(j)
                    merged = True

            new_list.append(curr)
        parsed = new_list

    # Объединение префиксов параметров, идущих подряд в вертикальную колонку таблицы
    col_merged = True
    while col_merged:
        col_merged = False
        new_list = []
        skip_indices = set()

        for i in range(len(parsed)):
            if i in skip_indices:
                continue
            curr = parsed[i]
            px_x1_c = curr["xmin"] * img_w / 1000.0
            px_y1_c = curr["ymin"] * img_h / 1000.0
            px_x2_c = curr["xmax"] * img_w / 1000.0
            px_y2_c = curr["ymax"] * img_h / 1000.0

            is_prefix_c = (curr.get("category") == "properties_branded_param") or any(
                k in t.upper() for t in curr.get("texts", []) for k in ["DOGMA", "!DOGMA"]
            )

            for j in range(i + 1, len(parsed)):
                if j in skip_indices:
                    continue
                other = parsed[j]
                px_x1_o = other["xmin"] * img_w / 1000.0
                px_y1_o = other["ymin"] * img_h / 1000.0
                px_x2_o = other["xmax"] * img_w / 1000.0
                px_y2_o = other["ymax"] * img_h / 1000.0

                is_prefix_o = (other.get("category") == "properties_branded_param") or any(
                    k in t.upper() for t in other.get("texts", []) for k in ["DOGMA", "!DOGMA"]
                )

                if is_prefix_c and is_prefix_o:
                    dx1 = abs(px_x1_c - px_x1_o)
                    dx2 = abs(px_x2_c - px_x2_o)
                    v_gap = max(0.0, max(px_y1_o - px_y2_c, px_y1_c - px_y2_o))

                    # В одной колонке: выравнивание по X (dx1 <= 8px, dx2 <= 12px) и зазор по Y <= 12px
                    if dx1 <= 8.0 and dx2 <= 12.0 and v_gap <= 12.0:
                        curr["ymin"] = min(curr["ymin"], other["ymin"])
                        curr["xmin"] = min(curr["xmin"], other["xmin"])
                        curr["ymax"] = max(curr["ymax"], other["ymax"])
                        curr["xmax"] = max(curr["xmax"], other["xmax"])
                        curr["texts"].extend(other["texts"])
                        curr["anchors"].extend(other["anchors"])
                        skip_indices.add(j)
                        col_merged = True

            new_list.append(curr)
        parsed = new_list

    result = []
    for p in parsed:
        result.append({
            "box_2d": [p["ymin"], p["xmin"], p["ymax"], p["xmax"]],
            "category": p["category"],
            "text_snippet": "; ".join(filter(None, p["texts"][:3])),
            "matched_anchor": ", ".join(set(filter(None, p["anchors"]))),
            "reason": p["reasons"][0] if p["reasons"] else "Sensitive data"
        })
    return result

def call_vlm_detector(client, image: Image.Image) -> list[dict]:
    """Отправляет изображение в Gemini VLM и парсит JSON с боксами"""
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=85)
    img_part = types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")

    last_error = None
    for model_name in MODEL_POOL:
        try:
            res = client.models.generate_content(
                model=model_name,
                contents=[img_part, SYSTEM_PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            raw_text = res.text.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.strip("`")
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:].strip()
            data = json.loads(raw_text)
            raw_list = []
            if isinstance(data, list):
                raw_list = data
            elif isinstance(data, dict) and "detections" in data:
                raw_list = data["detections"]

            valid_list = []
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                b = item.get("box_2d")
                if isinstance(b, (list, tuple)) and len(b) == 4:
                    try:
                        y1, x1, y2, x2 = [max(0, min(1000, int(float(v)))) for v in b]
                        if y2 > y1 and x2 > x1:
                            item["box_2d"] = [y1, x1, y2, x2]
                            valid_list.append(item)
                    except Exception:
                        continue
            return valid_list
        except Exception as e:
            last_error = e
            continue
    print(f"  [VLM Warning] All models failed. Last error: {last_error}")
    try:
        err_str = str(last_error or "Unknown VLM error")
        title = "Gemini VLM Rate Limit (429)" if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str else "Отказ всех моделей Gemini VLM в media_sanitizer"
        send_sync_critical(
            title=title,
            details=(
                f"• <b>Пул моделей:</b> <code>{', '.join(MODEL_POOL)}</code>\n"
                f"• <b>Последняя ошибка:</b> <code>{err_str[:400]}</code>"
            )
        )
    except Exception:
        pass
    return []

def call_tiled_vlm_detector(client, image: Image.Image) -> list[dict]:
    """Сканирует изображение с горизонтальным тайлингом для скриншотов высокого разрешения (>1200px)"""
    w, h = image.size
    if w <= 1200:
        return call_vlm_detector(client, image)

    print(f"   [Tiled Scanner] High-res image ({w}x{h} px) -> scanning with 2 overlapping tiles...")
    all_detections = []

    crops = [
        ("left", image.crop((0, 0, int(w * 0.58), h)), 0, int(w * 0.58)),
        ("right", image.crop((int(w * 0.42), 0, w, h)), int(w * 0.42), w)
    ]

    for label, crop_img, x_offset, x_end in crops:
        cw, ch = crop_img.size
        print(f"   [Tiled Scanner] Scanning {label} tile ({cw}x{ch} px)...", flush=True)
        crop_dets = call_vlm_detector(client, crop_img)
        print(f"   [Tiled Scanner] {label} tile returned {len(crop_dets)} raw detections.", flush=True)
        for d in crop_dets:
            ymin, xmin, ymax, xmax = d.get("box_2d", [0, 0, 0, 0])
            abs_x1 = x_offset + int(xmin * cw / 1000)
            abs_x2 = x_offset + int(xmax * cw / 1000)
            abs_y1 = int(ymin * ch / 1000)
            abs_y2 = int(ymax * ch / 1000)

            norm_box = [
                int(abs_y1 * 1000 / h),
                int(abs_x1 * 1000 / w),
                int(abs_y2 * 1000 / h),
                int(abs_x2 * 1000 / w)
            ]
            d["box_2d"] = norm_box
            all_detections.append(d)

    return all_detections

def apply_blur_to_image(img_path: Path, detections: list[dict], out_path: Path, blur_radius: int = 22):
    """
    Накладывает точечный GaussianBlur на зоны Bounding Box.
    Для анимированных GIF размытие применяется покадрово на всю длительность анимации с сохранением fps и цикла.
    """
    ext = img_path.suffix.lower()
    with Image.open(img_path) as img:
        is_animated = getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1
        is_gif = (ext == ".gif") or is_animated

    if is_gif:
        timeline_rules = [
            {"start": 0, "end": 999999, "box_norm": det["box_2d"]}
            for det in detections
            if "box_2d" in det
        ]
        blur_gif_with_timeline(img_path, timeline_rules, out_path)
        return

    # Для статичных изображений (PNG, JPG)
    with Image.open(img_path) as img:
        img_rgb = img.convert("RGB")
        w, h = img.size

        for det in detections:
            ymin, xmin, ymax, xmax = det["box_2d"]
            x1 = max(0, int(xmin * w / 1000))
            y1 = max(0, int(ymin * h / 1000))
            x2 = min(w, int(xmax * w / 1000))
            y2 = min(h, int(ymax * h / 1000))

            box_h = y2 - y1
            if box_h < 20 and h >= 20:
                mid_y = (y1 + y2) // 2
                y1 = max(0, mid_y - 10)
                y2 = min(h, y1 + 20)

            if x2 <= x1 or y2 <= y1:
                continue

            crop_box = (x1, y1, x2, y2)
            cropped = img_rgb.crop(crop_box)
            blurred = cropped.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            img_rgb.paste(blurred, crop_box)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img_rgb.save(out_path)

def send_to_manual_review(file_path: Path, passport: dict):
    """Копирует сложный файл в карантинную папку для ручной доработки пользователем"""
    REVIEW_PENDING.mkdir(parents=True, exist_ok=True)
    REVIEW_READY.mkdir(parents=True, exist_ok=True)

    dest_file = REVIEW_PENDING / file_path.name
    shutil.copy2(file_path, dest_file)

    meta_file = REVIEW_PENDING / f"{file_path.stem}_meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(passport, f, ensure_ascii=False, indent=2)

    print(f"📦 [Manual Review Queue] Complex file routed to: {dest_file}")
    print(f"   Metadata saved to: {meta_file}")

def import_reviewed_files():
    """Импортирует готовые файлы из ready/ обратно в attachments"""
    if not REVIEW_READY.exists():
        print("Folder 'ready/' does not exist.")
        return

    ready_files = [f for f in REVIEW_READY.iterdir() if f.is_file() and not f.name.endswith(".json")]
    if not ready_files:
        print("No files found in 'Data/manual_review_media/ready/'.")
        return

    count = 0
    for rf in ready_files:
        meta_file = REVIEW_PENDING / f"{rf.stem}_meta.json"
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            original_rel = meta.get("file_path")
            if original_rel:
                orig_target = PROJECT_ROOT / original_rel
                shutil.copy2(rf, orig_target)
                print(f"✅ [Imported] {rf.name} -> {original_rel}")
                count += 1
        else:
            # Ищем совпадение по имени в attachments
            matches = list((PROJECT_ROOT / "Data" / "BIM" / "attachments").rglob(rf.name))
            if matches:
                shutil.copy2(rf, matches[0])
                print(f"✅ [Imported by Name] {rf.name} -> {matches[0].relative_to(PROJECT_ROOT)}")
                count += 1

    print(f"\nImported {count} manually reviewed files.")

def blur_gif_with_timeline(in_path: Path, timeline_rules: list, out_path: Path):
    """
    Покадрово блюрит анимированный GIF согласно хронометражным правилам.
    timeline_rules = [
        {"start": int, "end": int, "box_norm": [ymin, xmin, ymax, xmax]}
    ]
    """
    import numpy as np
    from PIL import ImageSequence
    with Image.open(in_path) as img:
        w, h = img.size
        n_frames = getattr(img, "n_frames", 1)
        print(f"[Timeline GIF Blur] {in_path.name} ({n_frames} frames, {w}x{h})")
        
        pixel_rules = []
        for r in timeline_rules:
            ymin, xmin, ymax, xmax = r["box_norm"]
            x1 = max(0, int(xmin * w / 1000) - 2)
            y1 = max(0, int(ymin * h / 1000) - 2)
            x2 = min(w, int(xmax * w / 1000) + 2)
            y2 = min(h, int(ymax * h / 1000) + 2)
            h_box = y2 - y1
            if h_box < 20 and h >= 20:
                mid_y = (y1 + y2) // 2
                y1 = max(0, mid_y - 10)
                y2 = min(h, y1 + 20)
            pixel_rules.append({
                "start": r.get("start", 0),
                "end": r.get("end", n_frames - 1),
                "box": (x1, y1, x2, y2)
            })
            
        frames = []
        durations = []
        loop = img.info.get("loop", 0)
        base_palette_img = None
        
        for idx, frame in enumerate(ImageSequence.Iterator(img)):
            f_rgb = frame.convert("RGB")
            for pr in pixel_rules:
                if pr["start"] <= idx <= pr["end"]:
                    bx = pr["box"]
                    if bx[2] > bx[0] and bx[3] > bx[1]:
                        crop = f_rgb.crop(bx)
                        blurred = crop.filter(ImageFilter.GaussianBlur(14))
                        f_rgb.paste(blurred, bx)
            if n_frames > 80:
                if base_palette_img is None:
                    base_palette_img = f_rgb.quantize(colors=256)
                    f_p = base_palette_img
                else:
                    f_p = f_rgb.quantize(palette=base_palette_img, dither=Image.Dither.NONE)
            else:
                f_p = f_rgb.convert("P", palette=Image.ADAPTIVE)
            # Очищаем кортежные атрибуты прозрачности/фона из RGB, которые ломают GifImagePlugin._write_local_header
            f_p.info.pop("transparency", None)
            f_p.info.pop("background", None)
            frames.append(f_p)
            durations.append(frame.info.get("duration", 80))
            
        out_path.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            out_path,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=loop,
            optimize=False
        )
        print(f"[Saved GIF] Preview with timeline blur saved to {out_path.name}")

def process_animated_media_smart(fpath: Path, previews_dir: Path, batch_dir: Path, client) -> dict:
    """Интеллектуальный токено-эффективный анализатор анимированных вложений с хронометражным отслеживанием"""
    import numpy as np
    from PIL import ImageSequence
    
    with Image.open(fpath) as img:
        w, h = img.size
        n_frames = getattr(img, 'n_frames', 1)
        orig_rel = os.path.relpath(fpath, batch_dir).replace('\\', '/')
        
    print(f"🎬 [Smart GIF Inspector] Analyzing timeline for {fpath.name} ({n_frames} frames)...")
    
    # 1. Анализируем начальный кадр 0 через стандартный VLM сканер
    frame0 = extract_vlm_frame(fpath)
    dets0 = call_tiled_vlm_detector(client, frame0)
    
    # Фильтруем детекции кадра 0
    clean_dets0 = []
    for d in dets0:
        snippet = d.get("text_snippet", "")
        cat = d.get("category", "")
        if cat == "ribbon_tab" or (d.get("box_2d", [100])[0] < 20 and d.get("box_2d", [0,0,100])[2] < 45):
            continue
        rebar_generic = ["СОРТ_", "ВЕРТСТЕРЖЕНЬ", "ПОБР", "ПОДР", "ХОМУТ", "ШПИЛЬКА", "АРМАТУРА", "ОТСТУП", "ДЛИНА", "ШАГ"]
        if any(p in snippet.upper() for p in rebar_generic) and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA"]):
            continue
        if snippet.startswith("ADSK_") and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA"]):
            continue
        if any(k in snippet.lower() for k in ["см. лист", "лист:"]) and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA", "1001_", "1011_", "1012_", "P:\\", "DP.1"]):
            continue
        clean_dets0.append(d)

    ribbon_boxes0 = scan_ribbon_strip(frame0, client)
    if ribbon_boxes0:
        clean_dets0.extend(ribbon_boxes0)
        
    # 2. Проверяем наличие изменения состояния сцены во времени (кадр 0 vs кадр середины)
    with Image.open(fpath) as img:
        img.seek(0)
        arr0 = np.array(img.convert('L'))
        mid_idx = n_frames // 2
        img.seek(mid_idx)
        arr_mid = np.array(img.convert('L'))
        
    # Считаем разницу между сценами
    diff_mid = np.mean(np.abs(arr0.astype(float) - arr_mid.astype(float)))
    
    clean_dets_mid = []
    # Если сцена заметно менялась (например, открывалось окно свойств типа / модалка)
    if diff_mid > 8.0:
        with Image.open(fpath) as img:
            img.seek(mid_idx)
            frame_mid = img.convert("RGB")
        dets_mid = call_tiled_vlm_detector(client, frame_mid)
        for d in dets_mid:
            snippet = d.get("text_snippet", "")
            cat = d.get("category", "")
            if cat == "ribbon_tab" or (d.get("box_2d", [100])[0] < 20 and d.get("box_2d", [0,0,100])[2] < 45):
                continue
            rebar_generic = ["СОРТ_", "ВЕРТСТЕРЖЕНЬ", "ПОБР", "ПОДР", "ХОМУТ", "ШПИЛЬКА", "АРМАТУРА", "ОТСТУП", "ДЛИНА", "ШАГ"]
            if any(p in snippet.upper() for p in rebar_generic) and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA"]):
                continue
            if snippet.startswith("ADSK_") and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA"]):
                continue
            if any(k in snippet.lower() for k in ["см. лист", "лист:"]) and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA", "1001_", "1011_", "1012_", "P:\\", "DP.1"]):
                continue
            clean_dets_mid.append(d)
            
    # Уточняем границы префиксов перед объединением
    refined_dets0 = [
        dict(d, box_2d=shrink_box_to_prefix(d.get("box_2d", [0,0,0,0]), d.get("text_snippet", ""), d.get("category", ""), frame0, w, h))
        for d in clean_dets0
    ]
    refined_dets_mid = [
        dict(d, box_2d=shrink_box_to_prefix(d.get("box_2d", [0,0,0,0]), d.get("text_snippet", ""), d.get("category", ""), frame_mid if 'frame_mid' in locals() else frame0, w, h))
        for d in clean_dets_mid
    ]

    # Объединяем смежные боксы до трекинга
    clean_dets0 = merge_and_pad_boxes(refined_dets0, w, h)
    clean_dets_mid = merge_and_pad_boxes(refined_dets_mid, w, h)

    # Собираем уникальные детекции
    all_found_dets = clean_dets0 + clean_dets_mid
    has_sensitive = len(all_found_dets) > 0
    
    if not has_sensitive:
        print(f"🛡️ [Smart GIF] File is 100% clean across all keyframes. Kept sharp.")
        return {
            "file_path": str(fpath.relative_to(PROJECT_ROOT)).replace('\\', '/'),
            "file_name": fpath.name,
            "status": "clean",
            "dimensions": {"width": w, "height": h, "frames": n_frames},
            "orig_rel_to_batch": orig_rel,
            "preview_rel_to_batch": None,
            "detections": []
        }
        
    # Если есть чувствительные данные: строим хронометражные правила через локальный diff пикселей за 1 проход
    timeline_rules = []
    unified_dets = []
    sample_step = max(1, n_frames // 50)
    sample_indices = sorted(set(list(range(0, n_frames, sample_step)) + [n_frames - 1]))

    # Предзагружаем опорные серые кадры за один проход
    sampled_frames = {}
    with Image.open(fpath) as img:
        for fi in sample_indices:
            img.seek(fi)
            sampled_frames[fi] = np.array(img.convert('L'))

    # Правило для кадра 0
    for d in clean_dets0:
        box = d.get("box_2d")
        if not box or len(box) != 4: continue
        unified_dets.append(d)
        ymin, xmin, ymax, xmax = box
        y1, y2 = int(ymin * h / 1000), int(ymax * h / 1000)
        x1, x2 = int(xmin * w / 1000), int(xmax * w / 1000)
        if y2 <= y1 or x2 <= x1: continue
        patch = arr0[y1:y2, x1:x2]
        
        active_f = [
            fi for fi, arr_f in sampled_frames.items()
            if np.mean(np.abs(arr_f[y1:y2, x1:x2].astype(float) - patch.astype(float))) < 10.0
        ]
        if active_f:
            max_gap = sample_step * 2
            seg_start = active_f[0]
            seg_end = active_f[0]
            for f in active_f[1:]:
                if f - seg_end <= max_gap:
                    seg_end = f
                else:
                    timeline_rules.append({
                        "start": max(0, seg_start - sample_step),
                        "end": min(n_frames - 1, seg_end + sample_step),
                        "box_norm": box
                    })
                    seg_start = f
                    seg_end = f
            timeline_rules.append({
                "start": max(0, seg_start - sample_step),
                "end": min(n_frames - 1, seg_end + sample_step),
                "box_norm": box
            })
                
    # Правило для кадра середины (если он отличался)
    for d in clean_dets_mid:
        box = d.get("box_2d")
        if not box or len(box) != 4: continue
        unified_dets.append(d)
        ymin, xmin, ymax, xmax = box
        y1, y2 = int(ymin * h / 1000), int(ymax * h / 1000)
        x1, x2 = int(xmin * w / 1000), int(xmax * w / 1000)
        if y2 <= y1 or x2 <= x1: continue
        patch = arr_mid[y1:y2, x1:x2]
        
        active_f = [
            fi for fi, arr_f in sampled_frames.items()
            if np.mean(np.abs(arr_f[y1:y2, x1:x2].astype(float) - patch.astype(float))) < 10.0
        ]
        if active_f:
            max_gap = sample_step * 2
            seg_start = active_f[0]
            seg_end = active_f[0]
            for f in active_f[1:]:
                if f - seg_end <= max_gap:
                    seg_end = f
                else:
                    timeline_rules.append({
                        "start": max(0, seg_start - sample_step),
                        "end": min(n_frames - 1, seg_end + sample_step),
                        "box_norm": box
                    })
                    seg_start = f
                    seg_end = f
            timeline_rules.append({
                "start": max(0, seg_start - sample_step),
                "end": min(n_frames - 1, seg_end + sample_step),
                "box_norm": box
            })
                
    if not timeline_rules:
        # Резерв: если локальный трекинг не сошелся, применяем на всю длину
        for d in all_found_dets:
            if d.get("box_2d"):
                timeline_rules.append({"start": 0, "end": n_frames - 1, "box_norm": d["box_2d"]})
                
    # Генерируем покадровое превью
    preview_name = fpath.name
    preview_path = previews_dir / preview_name
    blur_gif_with_timeline(fpath, timeline_rules, preview_path)
    
    return {
        "file_path": str(fpath.relative_to(PROJECT_ROOT)).replace('\\', '/'),
        "file_name": fpath.name,
        "status": "blurred",
        "dimensions": {"width": w, "height": h, "frames": n_frames},
        "orig_rel_to_batch": orig_rel,
        "preview_rel_to_batch": f"previews/{preview_name}",
        "detections": unified_dets
    }

def process_single_file(file_path: Path, preview: bool = True, client = None, route_complex_to_review: bool = True, force_quarantine: bool = False) -> dict:
    """Обрабатывает один файл: двухмасштабная VLM-детекция + наложение блюра"""
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        return {}

    if client is None:
        client = get_gemini_client()

    ext = file_path.suffix.lower()

    with Image.open(file_path) as img:
        w, h = img.size
        n_frames = getattr(img, 'n_frames', 1)
        actual_format = img.format or ("GIF" if ext == ".gif" else "PNG")
        is_animated = getattr(img, 'is_animated', False) or n_frames > 1

    is_gif = (ext == ".gif") or (actual_format == "GIF") or is_animated

    print(f"\n========================================================")
    print(f"🔍 [VLM Scan] Inspecting: {file_path.name}")
    print(f"   Resolution: {w}x{h} px | Internal Format: {actual_format} | Frames: {n_frames}")

    frame_for_vlm = extract_vlm_frame(file_path)

    # 1. Основной сканер (с поддержкой High-Res тайлинга для широких скриншотов)
    raw_detections = call_tiled_vlm_detector(client, frame_for_vlm)

    # Фильтрация детекций:
    # - Убираем неточные детекции ленты от общего сканера (их закроет высокоточный сканер полосы вкладок)
    # - Защищаем общеотраслевой стандарт ADSK_ (открытый шаблон BIM Автодеск) от ошибочного размытия
    clean_raw = []
    for d in raw_detections:
        snippet = d.get("text_snippet", "")
        cat = d.get("category", "")
        if cat == "ribbon_tab" or (d.get("box_2d", [100])[0] < 20 and d.get("box_2d", [0,0,100])[2] < 45):
            continue
        if snippet.startswith("ADSK_") and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA", "1012_", "1001_"]):
            continue

        # Защита конструкторских параметров армирования и сортамента (Сорт_)
        rebar_generic_prefixes = ["СОРТ_", "ВЕРТСТЕРЖЕНЬ", "ПОБР", "ПОДР", "ХОМУТ", "ШПИЛЬКА", "АРМАТУРА", "ОТСТУП", "ДЛИНА", "ШАГ"]
        if any(p in snippet.upper() for p in rebar_generic_prefixes) and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA"]):
            continue

        # Защита стандартных ссылок на листы ("см. лист") и имен листов ("Лист:") без коммерческих шифров
        if any(k in snippet.lower() for k in ["см. лист", "лист:"]) and not any(k in snippet.upper() for k in ["DOGMA", "MALTSEVA", "1001_", "1011_", "1012_", "P:\\", "DP.1"]):
            continue

        # Защита стандартных имен видов в заголовках окон (если это не коммерческий шифр проекта)
        if cat == "window_titlebar" and any(k in snippet.lower() for k in ["узел", "фрагмент", "разрез", "план", "спецификация", "ограждение", "лист"]) and not any(k in snippet for k in ["1001_", "1011_", "1012_", "P:\\", "dp.1"]):
            continue

        # Защита от ложных срабатываний брендовых параметров: если категория properties_branded_param,
        # но в тексте сниппета, анкора или причины нет реального бренда (DOGMA, !DOGMA, шифров, учеток)
        # либо это стандартные префиксы Revit (Фильтр_, Группа_, Тип_, Категория_, Комментарии) -> пропускаем
        if cat == "properties_branded_param":
            anchor = d.get("matched_anchor", "")
            reason = d.get("reason", "")
            full_text = f"{snippet} {anchor} {reason}".upper()
            is_real_brand = any(k in full_text for k in ["DOGMA", "!DOGMA", "MALTSEVA", "1001_", "1011_", "1012_", "P:\\", "DP.1"])
            generic_sys = any(p in snippet.upper() for p in ["ФИЛЬТР_", "ГРУППА_", "ТИП_", "КАТЕГОРИЯ_", "КОММЕНТАРИ"])
            if not is_real_brand or generic_sys:
                continue

        clean_raw.append(d)

    # 2. Высокоточный сканер ленты (строго 24px)
    ribbon_boxes = scan_ribbon_strip(frame_for_vlm, client)
    if ribbon_boxes:
        print(f"   [Ribbon Strip Scanner] Detected {len(ribbon_boxes)} exact tab boundaries (height: 24px)!")
        clean_raw.extend(ribbon_boxes)

    # Двухуровневый гибридный контур: уточнение границ префиксов через микро-сегментатор ячейки
    refined_raw = []
    for d in clean_raw:
        box_n = d.get("box_2d", [0, 0, 0, 0])
        snip = d.get("text_snippet", "")
        cat = d.get("category", "")
        shrunk_box = shrink_box_to_prefix(box_n, text_snippet=snip, category=cat, img=frame_for_vlm, img_w=w, img_h=h)
        d_copy = dict(d)
        d_copy["box_2d"] = shrunk_box
        refined_raw.append(d_copy)

    detections = merge_and_pad_boxes(refined_raw, w, h)
    has_sensitive = len(detections) > 0
    print(f"   Sensitive data detected: {'YES ⚠️' if has_sensitive else 'NO (Clean) ✅'} ({len(clean_raw)} raw -> {len(detections)} unified zones)")

    for idx, d in enumerate(detections, 1):
        ymin, xmin, ymax, xmax = d.get("box_2d", [0, 0, 0, 0])
        px_box = [int(xmin * w / 1000), int(ymin * h / 1000), int(xmax * w / 1000), int(ymax * h / 1000)]
        d["box_pixel"] = px_box
        print(f"   [{idx}] Category: {d.get('category')} | Anchor: '{d.get('matched_anchor')}'")
        print(f"       Box (norm): {d.get('box_2d')} -> Pixels: {px_box}")
        print(f"       Text: \"{d.get('text_snippet')}\"")
        print(f"       Reason: {d.get('reason')}")

    out_path = file_path.with_name(f"{file_path.stem}_preview{ext}") if preview else file_path

    # Определение сложных случаев (динамические скроллящиеся деревья/списки в GIF, длинные анимации)
    # По требованию пользователя: такие сложные случаи изолируются в карантинную папку Data/manual_review_media/pending/
    is_complex_case = is_gif and has_sensitive and (
        n_frames > 30 or len(detections) > 2 or any(
            cat in d.get("category", "") for d in detections for cat in ["project_browser", "tree", "properties", "scroll"]
        )
    )

    if (is_complex_case or force_quarantine) and route_complex_to_review:
        passport = {
            "file_path": str(file_path.relative_to(PROJECT_ROOT)).replace('\\', '/'),
            "file_name": file_path.name,
            "media_type": ext.upper().lstrip('.'),
            "dimensions": {"width": w, "height": h, "frames": n_frames},
            "has_sensitive_data": True,
            "reason_for_review": "Dynamic scrolling list / complex multi-zone animation in GIF",
            "detections": detections,
            "instructions_for_user": [
                "1. Откройте файл из папки pending/ в любом редакторе (Photoshop, ScreenToGif, GIMP и т.д.)",
                "2. Наложите блюр строго на чувствительные зоны (фамилии, бренд), сохраняя обучающий контекст плагинов и действий",
                "3. Сохраните готовый файл с ТЕМ ЖЕ ИМЕНЕМ в папку Data/manual_review_media/ready/",
                "4. Выполните команду: .venv\\Scripts\\python.exe Scripts/media_sanitizer.py --import-reviewed"
            ]
        }
        send_to_manual_review(file_path, passport)
        print(f"📌 [Карантин] Файл перенаправлен в Data/manual_review_media/pending/ для ручной доработки.")
        return passport

    if has_sensitive:
        print(f"🎨 [Blur Engine] Applying precision blur...")
        if not is_gif:
            apply_blur_to_image(file_path, detections, out_path)
        print(f"💾 [Saved] Result saved to: {out_path}")
    else:
        print(f"🛡️ [Skip] No sensitive data found. Original kept 100% sharp.")

    try:
        rel_file = str(file_path.resolve().relative_to(PROJECT_ROOT.resolve())).replace('\\', '/')
    except Exception:
        rel_file = str(file_path).replace('\\', '/')

    try:
        rel_out = str(out_path.resolve().relative_to(PROJECT_ROOT.resolve())).replace('\\', '/') if has_sensitive else None
    except Exception:
        rel_out = str(out_path).replace('\\', '/') if has_sensitive else None

    passport = {
        "file_path": rel_file,
        "file_name": file_path.name,
        "media_type": ext.upper().lstrip('.'),
        "dimensions": {"width": w, "height": h, "frames": n_frames},
        "has_sensitive_data": has_sensitive,
        "detections": detections,
        "output_path": rel_out
    }
    return passport

def get_ordered_media_list() -> list[Path]:
    """Возвращает стабильно отсортированный список всех медиавложений базы знаний"""
    attachments_dir = PROJECT_ROOT / "Data" / "BIM" / "attachments"
    files = []
    for p in attachments_dir.rglob("*"):
        if p.is_file() and "_preview" not in p.name and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".gif"]:
            files.append(p)
    files.sort(key=lambda x: str(x.relative_to(PROJECT_ROOT)).replace('\\', '/'))
    return files

def build_item_context(it: dict) -> str:
    """Генерирует краткое пояснение контекста изменений для человека"""
    status = it.get("status")
    if status == "clean":
        return "Чистый рабочий скриншот: стандартный интерфейс / чертеж без корпоративных брендов и персональных данных."
    elif status == "quarantine":
        n_frames = it.get("dimensions", {}).get("frames", 1)
        return f"Анимированный файл ({n_frames} кадров): маршрутизирован в 'pending/' для ручной покадровой модерации в ScreenToGif."
    elif status == "blurred":
        dets = it.get("detections", [])
        parts = []
        ribbon_tabs = [d.get("text_snippet") for d in dets if d.get("category") == "ribbon_tab"]
        params = [d.get("text_snippet") for d in dets if "param" in d.get("category", "")]
        creds = [d.get("text_snippet") for d in dets if d.get("category") in ["credentials", "username", "window_titlebar"]]

        if ribbon_tabs:
            clean_tabs = list(dict.fromkeys([t.split(';')[0].strip() for t in ribbon_tabs]))
            parts.append(f"лента Revit ({', '.join(clean_tabs[:2])})")
        if params:
            clean_params = list(dict.fromkeys([p.split(';')[0].strip() for p in params]))
            parts.append(f"параметры ({', '.join(clean_params[:2])})")
        if creds:
            clean_creds = list(dict.fromkeys([c.split(';')[0].strip() for c in creds]))
            parts.append(f"данные/шифр ({', '.join(clean_creds[:2])})")

        if parts:
            return f"Заблюрено: {'; '.join(parts)}. Обучающие панели и геометрия сохранены."
        return f"Заблюрено {len(dets)} зон с конфиденциальными корпоративными данными."
    return ""

def generate_batch_html_report(batch_idx: int, items: list[dict], out_html: Path):
    """Генерирует высокоэффективную интерактивную витрину инспекции с быстрым UI разметки в 1 клик и хоткеями"""
    batch_dir = out_html.parent
    total = len(items)
    blurred_count = sum(1 for it in items if it.get("status") == "blurred")
    clean_count = sum(1 for it in items if it.get("status") == "clean")
    quarantine_count = sum(1 for it in items if it.get("status") == "quarantine")

    avail_batches = get_available_batches()
    if batch_idx not in avail_batches:
        avail_batches = sorted(avail_batches + [batch_idx])
    audit_data = load_audit_log()
    applied_batches = set(audit_data.get("applied_batches", []))
    pills_html = []
    for b in avail_batches:
        b_str = f"{b:02d}"
        is_curr = (b == batch_idx)
        is_app = (b in applied_batches)
        cls = "batch-pill" + (" current" if is_curr else "") + (" applied" if is_app else "")
        check = "✓" if is_app else ""
        href = f"../batch_{b_str}/report.html"
        pills_html.append(f'<a href="{href}" class="{cls}" title="Batch {b_str}{" (применен к боевым)" if is_app else ""}">{b_str}{check}</a>')
    batch_pills_markup = "\n        ".join(pills_html)

    cards_html = []
    for idx, it in enumerate(items, 1):
        status = it.get("status")
        fname = it.get("file_name")
        orig_rel = it.get("orig_rel_to_batch")
        preview_rel = it.get("preview_rel_to_batch")
        dets = it.get("detections", [])
        dims = it.get("dimensions", {})
        context_desc = build_item_context(it)
        it["context_desc"] = context_desc
        dim_str = f"{dims.get('width', 0)}x{dims.get('height', 0)} px"
        if dims.get("frames", 1) > 1:
            dim_str += f" | {dims.get('frames')} кадров"

        status_badge = ""
        card_class = f"status-{status}"
        if status == "blurred":
            status_badge = f'<span class="badge badge-blurred">Blurred {len(dets)}</span>'
        elif status == "clean":
            status_badge = '<span class="badge badge-clean">Clean</span>'
        elif status == "quarantine":
            status_badge = f'<span class="badge badge-quarantine">Карантин {dims.get("frames")}</span>'

        # Считаем итерации ручных правок
        manual_edits = sum(1 for d in dets if d.get("category") == "manual_box" or "manual" in d.get("matched_anchor", "").lower())
        iter_badge = f'<span class="badge badge-iter" title="Количество итераций правок">v{manual_edits + 1}</span>' if manual_edits > 0 else ""

        dets_list_html = ""
        if dets:
            dets_items = []
            for z_idx, d in enumerate(dets, 1):
                cat = d.get("category", "")
                txt = d.get("text_snippet", "")
                reason = d.get("reason", "")
                dets_items.append(f'<li><strong>[{cat}]</strong> <code>{txt}</code> &mdash; <span class="reason">{reason}</span></li>')
            dets_list_html = f'<div class="detections-box"><h4>Обнаруженные конфиденциальные данные:</h4><ul>{"".join(dets_items)}</ul></div>'
        elif status == "quarantine":
            dets_list_html = f'<div class="detections-box quarantine-box"><p>Файл в папке <code>Data/manual_review_media/pending/{fname}</code> для ручной проверки в ScreenToGif/Photoshop.</p></div>'

        # Компактный блок быстрых решений в 1 клик
        quick_bar_html = f"""
        <div class="quick-review-bar" id="quick-bar-{idx}">
            <div class="quick-chips-group">
                <button type="button" class="chip-btn chip-approve" id="chip-app-{idx}" onclick="setQuickStatus({idx}, 'approved')" title="Утвердить результат (Хоткей: A)">
                    Принято [A]
                </button>
                <button type="button" class="chip-btn chip-reject" id="chip-fp-{idx}" onclick="setQuickStatus({idx}, 'fp')" title="Ложное срабатывание (Хоткей: F)">
                    Ложное [F]
                </button>
                <button type="button" class="chip-btn chip-reject" id="chip-offset-{idx}" onclick="setQuickStatus({idx}, 'offset')" title="Неверное положение / сдвиг блюра (Хоткей: W)">
                    Сдвиг [W]
                </button>
                <button type="button" class="chip-btn chip-reject" id="chip-missed-{idx}" onclick="setQuickStatus({idx}, 'missed')" title="Пропущен чувствительный параметр (Хоткей: M)">
                    Пропуск [M]
                </button>
                <button type="button" class="chip-btn chip-note" id="chip-note-toggle-{idx}" onclick="toggleQuickNote({idx})" title="Добавить текстовое пояснение">
                    Заметка
                </button>
            </div>
            <div class="quick-status-text" id="quick-status-text-{idx}"></div>
            <div class="quick-note-box" id="quick-note-box-{idx}" style="display: none;">
                <div class="note-input-row">
                    <input type="text" id="quick-note-input-{idx}" placeholder="Пояснение (например: стандартный параметр армирования)..." onkeydown="if(event.key==='Enter') saveQuickNote({idx})">
                    <button type="button" class="chip-btn chip-approve" onclick="saveQuickNote({idx})">Сохранить</button>
                </div>
            </div>
        </div>
        """

        # Визуальное представление на карточке с кликом для открытия модалки
        if status == "blurred":
            p_disk = batch_dir / preview_rel if preview_rel else None
            mtime = int(p_disk.stat().st_mtime) if p_disk and p_disk.exists() else int(time.time())
            preview_src = f"{preview_rel}?t={mtime}" if preview_rel else ""
            visual_html = f"""
            <div class="media-comparison">
                <div class="media-col">
                    <span class="col-label">Оригинал</span>
                    <img src="{orig_rel}" loading="lazy" alt="Original" onclick="openModal({idx-1})">
                </div>
                <div class="media-col">
                    <span class="col-label">Результат</span>
                    <img src="{preview_src}" loading="lazy" alt="Blurred" onclick="openModal({idx-1})">
                </div>
            </div>
            """
        elif status == "quarantine":
            visual_html = f"""
            <div class="media-single">
                <span class="col-label">Анимация GIF</span>
                <img src="{orig_rel}" loading="lazy" alt="GIF Animation" onclick="openModal({idx-1})">
            </div>
            """
        else: # clean
            visual_html = f"""
            <div class="media-single">
                <span class="col-label">Оригинал</span>
                <img src="{orig_rel}" loading="lazy" alt="Clean" onclick="openModal({idx-1})">
            </div>
            """

        cards_html.append(f"""
        <div class="card {card_class}" data-status="{status}" id="card-{idx}">
            <div class="card-processing-overlay" id="processing-overlay-{idx}">
                <div class="processing-spinner"></div>
                <span>В обработке агентом...</span>
            </div>
            <div class="card-header">
                <div class="card-title">
                    <span class="card-num">#{idx}</span>
                    <span class="card-name">{fname}</span>
                    <span class="card-dim">{dim_str}</span>
                    {status_badge}
                    {iter_badge}
                </div>
                <div class="card-badges">
                    <span class="human-badge" id="human-badge-{idx}" style="display: none;">Одобрено</span>
                </div>
            </div>
            <div class="card-body">
                <div class="card-context-desc">
                    <strong>Контекст:</strong> {context_desc}
                </div>
                {visual_html}
                {dets_list_html}
                {quick_bar_html}
            </div>
        </div>
        """)

    # Преобразуем items в JSON для JS с защитой от кэширования превью
    items_copy = []
    for it in items:
        c_it = dict(it)
        pr = c_it.get("preview_rel_to_batch")
        if pr:
            clean_pr = pr.split("?")[0]
            p_disk = batch_dir / clean_pr
            mtime = int(p_disk.stat().st_mtime) if p_disk.exists() else int(time.time())
            c_it["preview_rel_to_batch"] = f"{clean_pr}?t={mtime}"
        items_copy.append(c_it)
    items_json = json.dumps(items_copy, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Batch {batch_idx:02d} Review &mdash; AxonBot OPSEC Sanitizer</title>
<style>
:root {{
    --bg-primary: #0b1120;
    --bg-secondary: #1e293b;
    --bg-card: #141f32;
    --border-color: #334155;
    --text-primary: #f8fafc;
    --text-secondary: #94a3b8;
    --accent-blue: #38bdf8;
    --accent-green: #22c55e;
    --accent-orange: #f59e0b;
    --accent-red: #ef4444;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    padding: 24px;
    line-height: 1.5;
}}
header {{
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 14px 20px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
}}
.header-left {{
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
}}
.header-right {{
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
}}
.hotkey-tooltip-wrap {{
    position: relative;
    display: inline-block;
}}
.hotkey-badge {{
    background: rgba(56, 189, 248, 0.12);
    color: #38bdf8;
    border: 1px solid rgba(56, 189, 248, 0.35);
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 700;
    cursor: help;
    user-select: none;
    transition: all 0.15s ease;
}}
.hotkey-badge:hover {{
    background: rgba(56, 189, 248, 0.25);
    border-color: #38bdf8;
}}
.hotkey-popup {{
    display: none;
    position: absolute;
    top: calc(100% + 8px);
    left: 0;
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 12px 16px;
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.65);
    z-index: 2000;
    width: 320px;
}}
.hotkey-tooltip-wrap:hover .hotkey-popup {{
    display: flex;
    flex-direction: column;
    gap: 6px;
}}
.hotkey-popup-title {{
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    color: var(--accent-blue);
    margin-bottom: 4px;
}}
.hotkey-row {{
    font-size: 12px;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    gap: 8px;
}}
kbd {{
    background: #1e293b;
    border: 1px solid #475569;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 11px;
    font-family: Consolas, monospace;
    color: #38bdf8;
    font-weight: 700;
}}
h1 {{ font-size: 18px; font-weight: 700; color: var(--text-primary); }}

.controls-bar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 20px;
}}
.filters-bar {{
    display: flex;
    gap: 5px;
    align-items: center;
    flex-wrap: wrap;
}}
.filter-btn {{
    background: var(--bg-secondary);
    color: var(--text-secondary);
    border: 1px solid var(--border-color);
    padding: 4px 10px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 11px;
    font-weight: 600;
    transition: all 0.15s ease;
    white-space: nowrap;
}}
.filter-btn:hover, .filter-btn.active {{
    background: var(--accent-blue);
    color: #0f172a;
    border-color: var(--accent-blue);
}}

.actions-bar {{
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
}}
.btn-action {{
    padding: 8px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    border: 1px solid transparent;
    transition: all 0.2s;
    display: inline-flex;
    align-items: center;
    gap: 6px;
}}
.btn-action:disabled {{
    opacity: 0.55;
    cursor: not-allowed;
}}
.btn-apply-batch {{
    background: #16a34a;
    color: #ffffff;
    border-color: #22c55e;
    box-shadow: 0 0 12px rgba(22, 163, 74, 0.3);
}}
.btn-apply-batch:hover:not(:disabled) {{ background: #15803d; }}
.btn-apply-batch.is-pending {{
    background: #334155;
    color: #cbd5e1;
    border-color: #475569;
    box-shadow: none;
}}
.btn-apply-batch.is-pending:hover:not(:disabled) {{ background: #3e4f66; }}
.btn-apply-batch.is-warning {{
    background: #d97706;
    color: #ffffff;
    border-color: #f59e0b;
    box-shadow: 0 0 12px rgba(217, 119, 6, 0.3);
}}
.btn-apply-batch.is-warning:hover:not(:disabled) {{ background: #b45309; }}
.btn-apply-batch.is-ready {{
    background: #16a34a;
    color: #ffffff;
    border-color: #22c55e;
    box-shadow: 0 0 16px rgba(22, 163, 74, 0.4);
}}
.btn-apply-batch.is-ready:hover:not(:disabled) {{ background: #15803d; }}
.btn-next-batch {{
    background: #2563eb;
    color: #ffffff;
    border-color: #60a5fa;
    box-shadow: 0 0 12px rgba(37, 99, 235, 0.35);
}}
.btn-next-batch:hover:not(:disabled) {{ background: #1d4ed8; }}
.btn-export {{
    background: #0284c7;
    color: #ffffff;
}}
.btn-export:hover {{ background: #0369a1; }}
.btn-copy {{
    background: #334155;
    color: #f8fafc;
    border-color: #475569;
}}
.btn-copy:hover {{ background: #475569; }}
.btn-submit-agent {{
    background: #7c3aed;
    color: #ffffff;
    border-color: #8b5cf6;
    box-shadow: 0 0 12px rgba(124, 58, 237, 0.35);
}}
.btn-submit-agent:hover:not(:disabled) {{ background: #6d28d9; }}
.btn-submit-agent:disabled {{
    background: #334155;
    color: #94a3b8;
    border-color: #475569;
    box-shadow: none;
}}

.server-bar {{
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    padding: 12px 18px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
}}
.batch-nav-pills {{
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
}}
.batch-pill {{
    min-width: 32px;
    height: 28px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0 7px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 700;
    text-decoration: none;
    background: #1e293b;
    color: #cbd5e1;
    border: 1px solid #334155;
    transition: all 0.15s;
}}
.batch-pill:hover {{
    border-color: var(--accent-blue);
    color: #f8fafc;
}}
.batch-pill.current {{
    background: var(--accent-blue);
    color: #0f172a;
    border-color: var(--accent-blue);
}}
.batch-pill.applied {{
    border-color: rgba(34, 197, 94, 0.6);
    color: #4ade80;
}}
.live-sync-status {{
    font-size: 12px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 20px;
    background: rgba(34, 197, 94, 0.15);
    color: #4ade80;
    border: 1px solid rgba(34, 197, 94, 0.35);
}}
.live-sync-status.offline {{
    background: rgba(148, 163, 184, 0.15);
    color: #94a3b8;
    border-color: #475569;
}}
.job-progress-banner {{
    display: none;
    background: rgba(30, 58, 138, 0.45);
    border: 1px solid #3b82f6;
    border-radius: 10px;
    padding: 14px 20px;
    margin-bottom: 18px;
    flex-direction: column;
    gap: 8px;
}}
.job-progress-track {{
    width: 100%;
    height: 8px;
    background: #0f172a;
    border-radius: 4px;
    overflow: hidden;
}}
.job-progress-fill {{
    height: 100%;
    background: linear-gradient(90deg, #38bdf8, #22c55e);
    width: 0%;
    transition: width 0.3s ease;
}}
.toast-msg {{
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    background: #16a34a;
    color: #ffffff;
    padding: 10px 20px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
    z-index: 10000;
    display: none;
}}

.grid {{
    display: flex;
    flex-direction: column;
    gap: 20px;
}}
.card {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    overflow: hidden;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}}
.card.has-error-marked {{
    border-color: rgba(239, 68, 68, 0.6);
    box-shadow: 0 0 10px rgba(239, 68, 68, 0.12);
}}
.card.is-recheck {{
    border-color: rgba(129, 140, 248, 0.85);
    box-shadow: 0 0 14px rgba(99, 102, 241, 0.25);
}}
.badge-recheck {{
    background: rgba(79, 70, 229, 0.25) !important;
    border: 1px solid rgba(129, 140, 248, 0.7) !important;
    color: #c7d2fe !important;
}}
.quick-status-text.text-recheck {{
    color: #a5b4fc !important;
    font-weight: 600;
}}
.card.is-human-approved {{
    border-color: rgba(34, 197, 94, 0.45);
    box-shadow: 0 0 10px rgba(34, 197, 94, 0.1);
}}
.card.is-agent-processing {{
    position: relative;
    border-color: rgba(147, 51, 234, 0.7);
    box-shadow: 0 0 16px rgba(147, 51, 234, 0.25);
}}
.card.is-agent-processing .card-body {{
    filter: blur(4px);
    opacity: 0.55;
    pointer-events: none;
    user-select: none;
    transition: filter 0.3s ease, opacity 0.3s ease;
}}
.card-processing-overlay {{
    display: none;
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: rgba(15, 23, 42, 0.94);
    border: 1px solid #a855f7;
    border-radius: 8px;
    padding: 12px 20px;
    color: #f8fafc;
    font-size: 13px;
    font-weight: 700;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.8);
    z-index: 100;
    align-items: center;
    gap: 10px;
}}
.card.is-agent-processing .card-processing-overlay {{
    display: inline-flex;
}}
.processing-spinner {{
    width: 14px;
    height: 14px;
    border: 2px solid rgba(168, 85, 247, 0.3);
    border-top-color: #c084fc;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}}
@keyframes spin {{
    to {{ transform: rotate(360deg); }}
}}
.badge-iter {{
    background: rgba(147, 51, 234, 0.2);
    border: 1px solid rgba(168, 85, 247, 0.5);
    color: #c084fc;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
}}

.card-header {{
    padding: 14px 20px;
    background: rgba(30, 41, 59, 0.6);
    border-bottom: 1px solid var(--border-color);
    display: flex;
    justify-content: space-between;
    align-items: center;
}}
.card-title {{ display: flex; align-items: center; gap: 10px; font-size: 15px; font-weight: 600; }}
.card-num {{ color: var(--accent-blue); font-weight: 800; }}
.card-dim {{ color: var(--text-secondary); font-size: 12px; font-weight: 400; }}
.badge {{ padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; }}
.badge-blurred {{ background: #f59e0b; color: #1e293b; }}
.badge-clean {{ background: #22c55e; color: #0f172a; }}
.badge-quarantine {{ background: #ef4444; color: #ffffff; }}
.human-badge {{
    background: rgba(34, 197, 94, 0.2);
    border: 1px solid #22c55e;
    color: #4ade80;
    padding: 3px 8px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
}}

.card-body {{ padding: 20px; }}
.media-comparison {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-bottom: 16px;
}}
@media (max-width: 900px) {{
    .media-comparison {{ grid-template-columns: 1fr; }}
}}
.media-single {{ max-width: 800px; margin-bottom: 16px; }}
.media-col, .media-single {{ display: flex; flex-direction: column; gap: 8px; }}
.col-label {{ font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-secondary); }}
img {{
    width: 100%;
    max-height: 480px;
    object-fit: contain;
    background: #020617;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    cursor: zoom-in;
    transition: transform 0.15s ease, border-color 0.15s ease;
}}
img:hover {{ border-color: var(--accent-blue); }}

.card-context-desc {{
    background: rgba(15, 23, 42, 0.6);
    border-left: 3px solid var(--accent-blue);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 14px;
}}
.card-context-desc strong {{
    color: var(--accent-blue);
}}

.detections-box {{
    background: rgba(15, 23, 42, 0.8);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 14px;
}}
.detections-box h4 {{ font-size: 13px; font-weight: 700; color: var(--accent-orange); margin-bottom: 8px; text-transform: uppercase; }}
.detections-box ul {{ list-style: none; padding-left: 0; display: flex; flex-direction: column; gap: 6px; }}
.detections-box li {{ font-size: 13px; color: var(--text-primary); }}
.detections-box code {{ background: #334155; padding: 2px 6px; border-radius: 4px; color: #f8fafc; font-family: Consolas, monospace; }}
.detections-box .reason {{ color: var(--text-secondary); font-size: 12px; }}
.quarantine-box p {{ font-size: 13px; color: #fca5a5; }}

/* =====================================================================
   Компактный Quick-Action UI разметки (в 1 клик)
===================================================================== */
.quick-review-bar {{
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 10px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}}
.quick-chips-group {{
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}}
.chip-btn {{
    padding: 5px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    border: 1px solid transparent;
    transition: all 0.15s ease;
    user-select: none;
}}
.chip-approve {{
    background: rgba(34, 197, 94, 0.12);
    color: #4ade80;
    border-color: rgba(34, 197, 94, 0.35);
}}
.chip-approve:hover {{
    background: rgba(34, 197, 94, 0.25);
    border-color: #22c55e;
    color: #f8fafc;
}}
.chip-approve.active {{
    background: #22c55e !important;
    color: #0f172a !important;
    box-shadow: 0 0 12px rgba(34, 197, 94, 0.4);
}}
.chip-reject {{
    background: rgba(51, 65, 85, 0.6);
    color: #cbd5e1;
    border-color: #475569;
}}
.chip-reject:hover {{
    background: #334155;
    color: #f8fafc;
}}
.chip-reject.active {{
    background: #ef4444 !important;
    color: #ffffff !important;
    border-color: #ef4444 !important;
    box-shadow: 0 0 12px rgba(239, 68, 68, 0.4);
}}
.chip-note {{
    background: transparent;
    color: #94a3b8;
    border-color: #475569;
    padding: 5px 10px;
}}
.chip-note:hover {{ color: #f8fafc; border-color: #64748b; }}
.chip-note.active {{ background: #38bdf8; color: #0f172a; border-color: #38bdf8; }}
.chip-draw {{
    background: rgba(56, 189, 248, 0.15);
    color: #38bdf8;
    border-color: rgba(56, 189, 248, 0.4);
}}
.chip-draw:hover {{ background: rgba(56, 189, 248, 0.3); }}
.chip-draw.active {{
    background: #0284c7 !important;
    color: #ffffff !important;
    border-color: #38bdf8 !important;
    box-shadow: 0 0 12px rgba(56, 189, 248, 0.5);
}}

.quick-status-text {{
    font-size: 12px;
    font-weight: 600;
}}
.quick-status-text.text-approved {{ color: #4ade80; }}
.quick-status-text.text-rejected {{ color: #f87171; }}

.quick-note-box {{
    margin-top: 6px;
}}
.note-input-row {{
    display: flex;
    gap: 6px;
    align-items: center;
}}
.note-input-row input {{
    flex: 1;
    background: #020617;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px 10px;
    color: #f8fafc;
    font-size: 12px;
}}
.note-input-row input:focus {{
    outline: none;
    border-color: var(--accent-blue);
}}

/* =====================================================================
   Модальное окно Lightbox с чистой версткой и быстрыми кнопками
===================================================================== */
.modal-overlay {{
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(2, 6, 23, 0.94);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    z-index: 9999;
    flex-direction: column;
    touch-action: none !important;
    overscroll-behavior: none !important;
}}

/* Шапка модального окна */
.modal-header {{
    min-height: 68px;
    background: rgba(15, 23, 42, 0.95);
    border-bottom: 1px solid #334155;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 24px;
    gap: 16px;
}}
.modal-header-left {{
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
    flex: 1;
}}
.modal-title-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 16px;
    font-weight: 700;
}}
.modal-title-row .modal-num {{
    color: var(--accent-blue);
    font-weight: 800;
}}
.modal-title-row .modal-filename {{
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
.modal-detections-box {{
    font-size: 12px;
    max-height: 65px;
    overflow-y: auto;
    background: rgba(2, 6, 23, 0.6);
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 4px 10px;
    color: var(--text-secondary);
}}
.modal-detections-box ul {{
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}}
.modal-detections-box li {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: #f8fafc;
}}
.modal-detections-box code {{
    background: #334155;
    padding: 1px 6px;
    border-radius: 4px;
    color: #fbbf24;
    font-family: Consolas, monospace;
}}

.modal-header-center {{
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
}}
.btn-space-compare {{
    background: #f59e0b;
    color: #000000;
    border: 1px solid #d97706;
    padding: 7px 15px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    box-shadow: 0 0 14px rgba(245, 158, 11, 0.35);
    transition: all 0.15s ease;
    user-select: none;
}}
.btn-space-compare:hover {{
    background: #d97706;
    color: #ffffff;
}}
.btn-space-compare.active {{
    background: #16a34a !important;
    color: #ffffff !important;
    border-color: #4ade80 !important;
    box-shadow: 0 0 18px rgba(34, 197, 94, 0.5) !important;
}}

/* Быстрые кнопки в шапке модалки */
.modal-quick-actions {{
    display: flex;
    align-items: center;
    gap: 6px;
}}

.modal-header-right {{
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex-shrink: 0;
}}
.btn-modal-close {{
    background: #ef4444;
    color: #ffffff;
    border: none;
    padding: 8px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.15s;
}}
.btn-modal-close:hover {{ background: #dc2626; }}

.modal-qr-box {{
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 24px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 14px;
    max-width: 360px;
    text-align: center;
    box-shadow: 0 10px 30px rgba(0,0,0,0.6);
}}
.modal-qr-box img {{
    width: 220px;
    height: 220px;
    border-radius: 8px;
    border: 2px solid #38bdf8;
    background: #ffffff;
    padding: 6px;
}}
.modal-errors-box {{
    background: #0f172a;
    border: 1px solid #ef4444;
    border-radius: 12px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: 720px;
    width: 90%;
    box-shadow: 0 10px 30px rgba(0,0,0,0.6);
}}
.btn-qr-pill {{
    background: rgba(56, 189, 248, 0.12);
    color: #38bdf8;
    border: 1px solid rgba(56, 189, 248, 0.4);
    padding: 7px 14px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.15s ease;
    user-select: none;
}}
.btn-qr-pill:hover {{
    background: rgba(56, 189, 248, 0.25);
    border-color: #38bdf8;
}}

/* Основная область просмотра */
.modal-viewport {{
    flex: 1;
    position: relative;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #020617;
    cursor: grab;
    touch-action: none !important;
    overscroll-behavior: none !important;
    user-select: none;
    -webkit-user-select: none;
}}
.modal-viewport:active {{ cursor: grabbing; }}

.modal-img-container {{
    transform-origin: center center;
    transition: transform 0.05s ease-out;
    display: flex;
    align-items: center;
    justify-content: center;
    touch-action: none !important;
    user-select: none;
    -webkit-user-select: none;
}}
#lightboxImg {{
    max-width: 94vw;
    max-height: 82vh;
    object-fit: contain;
    border: none;
    background: transparent;
    cursor: grab;
    touch-action: none !important;
    user-select: none;
    -webkit-user-select: none;
    -webkit-touch-callout: none;
}}

/* Интерактивные зоны навигации (компактные стрелки по краям) */
.nav-zone {{
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    width: 60px;
    height: 120px;
    z-index: 50;
    display: flex;
    align-items: center;
    cursor: pointer;
    user-select: none;
}}
.nav-zone-left {{
    left: 12px;
    justify-content: flex-start;
}}
.nav-zone-right {{
    right: 12px;
    justify-content: flex-end;
}}
.nav-arrow-circle {{
    width: 52px;
    height: 52px;
    border-radius: 50%;
    background: rgba(15, 23, 42, 0.75);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(255, 255, 255, 0.25);
    color: #f8fafc;
    font-size: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transform: scale(0.92);
    transition: opacity 0.2s ease, transform 0.2s ease, background 0.2s ease, border-color 0.2s ease;
    pointer-events: none;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}}
.nav-zone:hover .nav-arrow-circle {{
    opacity: 0.85;
    transform: scale(1);
}}
.nav-zone:active .nav-arrow-circle {{
    opacity: 1;
    background: rgba(2, 132, 199, 0.85);
    border-color: #38bdf8;
    transform: scale(0.95);
}}

/* Плавающий мини-виджет масштабирования (Floating Zoom HUD) */
.floating-zoom-hud {{
    position: absolute;
    bottom: 20px;
    right: 24px;
    z-index: 60;
    display: flex;
    align-items: center;
    gap: 6px;
    background: rgba(15, 23, 42, 0.85);
    backdrop-filter: blur(10px);
    border: 1px solid #334155;
    padding: 6px 12px;
    border-radius: 24px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}}
.hud-btn {{
    background: #1e293b;
    color: #f8fafc;
    border: 1px solid #475569;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s ease;
}}
.hud-btn:hover {{
    background: #38bdf8;
    color: #0f172a;
    border-color: #38bdf8;
}}
.hud-scale {{
    font-size: 12px;
    color: #94a3b8;
    font-weight: 600;
    min-width: 44px;
    text-align: center;
}}

/* Подвал модального окна */
.modal-footer {{
    height: 46px;
    background: rgba(15, 23, 42, 0.95);
    border-top: 1px solid #334155;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 24px;
    font-size: 13px;
    color: #94a3b8;
}}
.key-hint {{
    background: #334155;
    padding: 2px 7px;
    border-radius: 4px;
    color: #f8fafc;
    font-family: Consolas, monospace;
    font-size: 12px;
}}
.keys-legend {{
    font-size: 12px;
    color: #64748b;
}}

footer {{
    margin-top: 30px;
    padding: 20px;
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    text-align: center;
    font-size: 13px;
    color: var(--text-secondary);
}}
footer code {{ color: var(--accent-blue); background: #0f172a; padding: 3px 8px; border-radius: 4px; }}
</style>
</head>
<body>

<header>
    <div class="header-left">
        <h1>AxonBot OPSEC Review &mdash; Batch {batch_idx:02d}</h1>
        <div class="hotkey-tooltip-wrap">
            <span class="hotkey-badge">? Хоткеи</span>
            <div class="hotkey-popup">
                <div class="hotkey-popup-title">Горячие клавиши (в модалке / карточке)</div>
                <div class="hotkey-row"><kbd>A</kbd><span>Одобрить (Approve)</span></div>
                <div class="hotkey-row"><kbd>F</kbd><span>Ложное срабатывание (FP)</span></div>
                <div class="hotkey-row"><kbd>W</kbd><span>Сдвиг / неточный блюр</span></div>
                <div class="hotkey-row"><kbd>M</kbd><span>Пропущенный шифр / имя</span></div>
                <div class="hotkey-row"><kbd>Shift+M</kbd><span>Нарисовать рамку мышью</span></div>
                <div class="hotkey-row"><kbd>Space</kbd><span>Удерживать: оригинал</span></div>
                <div class="hotkey-row"><kbd>&larr; / &rarr;</kbd><span>Предыдущее / следующее</span></div>
                <div class="hotkey-row"><kbd>Esc</kbd><span>Закрыть просмотр</span></div>
            </div>
        </div>
    </div>
    <div class="header-right">
        <span id="liveSyncStatus" class="live-sync-status offline">Связь с сервером...</span>
        <button class="btn-qr-pill" id="btnShowQr" onclick="openQrModal()" title="Открыть QR-код для смартфона">
            QR / Mobile
        </button>
        <button class="btn-qr-pill" id="btnShowErrors" onclick="openErrorsModal()" style="display: none; color: #f87171; border-color: rgba(239, 68, 68, 0.4); background: rgba(239, 68, 68, 0.15);">
            Ошибки (<span id="serverErrorsCount">0</span>)
        </button>
    </div>
</header>

<div class="server-bar">
    <div class="batch-nav-pills" id="batchNavPills">
        <span style="font-size: 12px; color: var(--text-secondary); margin-right: 4px;">Батчи:</span>
        {batch_pills_markup}
    </div>
    <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
        <button class="btn-action btn-apply-batch" id="btnApplyBatch" onclick="applyCurrentBatch()">
            Применить
        </button>
        <button class="btn-action btn-next-batch" id="btnNextBatch" onclick="runNextBatch()">
            Сканировать Batch {batch_idx + 1:02d}
        </button>
    </div>
</div>

<div class="job-progress-banner" id="jobProgressBanner">
    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 700;">
        <span id="jobProgressTitle">Сканирование следующего батча...</span>
        <span id="jobProgressCounter" style="color: var(--accent-blue);">0 / 20</span>
    </div>
    <div class="job-progress-track">
        <div class="job-progress-fill" id="jobProgressFill"></div>
    </div>
    <div id="jobProgressSub" style="font-size: 12px; color: var(--text-secondary);">Инициализация VLM-сканера...</div>
</div>

<div class="controls-bar">
    <div class="filters-bar">
        <span style="font-size: 11px; color: var(--text-secondary); margin-right: 2px;">Фильтр:</span>
        <button class="filter-btn active" onclick="filterCards('all')">Все ({total})</button>
        <button class="filter-btn" onclick="filterCards('recheck')" id="filterBtnRecheck" style="border-color: rgba(129, 140, 248, 0.4);">На проверку (<span id="recheckCount">0</span>)</button>
        <button class="filter-btn" onclick="filterCards('flagged')">Замечания (<span id="flaggedCount">0</span>)</button>
        <button class="filter-btn" onclick="filterCards('blurred')">Заблюрено ({blurred_count})</button>
        <button class="filter-btn" onclick="filterCards('clean')">Чистые ({clean_count})</button>
        <button class="filter-btn" onclick="filterCards('quarantine')">Карантин ({quarantine_count})</button>
        <button class="filter-btn" onclick="filterCards('unreviewed')" id="filterBtnUnreviewed">Не проверено (<span id="unreviewedCount">{total}</span>)</button>
        <button class="filter-btn" onclick="filterCards('approved')">Одобрено (<span id="approvedCount">0</span>)</button>
    </div>
    <div class="actions-bar">
        <button class="btn-action btn-submit-agent" id="btnSubmitAgent" onclick="submitToAgent()" disabled title="Поставить отмеченные файлы в очередь автономного исправления агентом">
            Отправить замечания агенту (<span id="submitAgentCount">0</span>)
        </button>
        <button class="btn-action btn-copy" onclick="copyFeedbackToClipboard()">Копировать замечания</button>
        <button class="btn-action btn-export" onclick="exportFeedbackJSON()">Экспорт JSON</button>
    </div>
</div>

<div class="toast-msg" id="toastMsg">Сохранено в audit_log.json</div>

<div class="grid" id="cardsGrid">
    {"".join(cards_html)}
</div>

<footer>
    <p>Все решения автоматически синхронизируются с <code>Data/review_batches/audit_log.json</code> при запущенном сервере <code>python Scripts/media_sanitizer.py --serve</code></p>
</footer>

<!-- Модальное окно QR-кода для мобильных устройств -->
<div class="modal-overlay" id="qrModal" style="display: none; justify-content: center; align-items: center; z-index: 2500;" onclick="closeQrModal(event)">
    <div class="modal-qr-box" onclick="event.stopPropagation()">
        <h3 style="margin: 0; color: #f8fafc; font-size: 16px;">Мобильный Dashboard</h3>
        <p style="margin: 0; color: #94a3b8; font-size: 12px;">Подключите смартфон к той же Wi-Fi сети и наведите камеру:</p>
        <img id="qrModalImg" src="../../mobile_qr.png" alt="Mobile QR Code" />
        <div style="font-size: 12px; color: #38bdf8; word-break: break-all; font-family: monospace;" id="qrModalUrlText"></div>
        <button class="btn-space-compare" style="box-shadow: none;" onclick="closeQrModal()">Закрыть</button>
    </div>
</div>

<!-- Модальное окно журнала ошибок сервера -->
<div class="modal-overlay" id="errorsModal" style="display: none; justify-content: center; align-items: center; z-index: 2500;" onclick="closeErrorsModal(event)">
    <div class="modal-errors-box" onclick="event.stopPropagation()">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <h3 style="margin: 0; color: #f87171; font-size: 16px;">Телеметрия ошибок бэкенда</h3>
            <button class="btn-modal-close" onclick="closeErrorsModal()">Закрыть</button>
        </div>
        <pre id="errorsLogText" style="margin: 0; max-height: 400px; overflow-y: auto; background: #020617; border: 1px solid #334155; border-radius: 6px; padding: 12px; color: #fca5a5; font-size: 12px; font-family: Consolas, monospace; white-space: pre-wrap;"></pre>
        <button class="btn-space-compare" style="box-shadow: none; align-self: flex-end;" onclick="closeErrorsModal()">Закрыть</button>
    </div>
</div>

<!-- Полноэкранное интерактивное модальное окно -->
<div class="modal-overlay" id="lightboxModal">
    <div class="modal-header">
        <div class="modal-header-left">
            <div class="modal-title-row">
                <span id="modalNum" class="modal-num">#1</span>
                <span id="modalFileName" class="modal-filename">file.png</span>
                <span id="modalViewBadge" class="badge" style="display: none;"></span>
                <span class="human-badge" id="modalHumanBadge" style="display: none;">Одобрено</span>
            </div>
            <div class="modal-detections-box" id="modalDetectionsBox"></div>
        </div>

        <div class="modal-header-center">
            <button class="btn-space-compare" id="btnHoldCompare"
                onmousedown="showOriginalInModal()" 
                onmouseup="showBlurredInModal()" 
                ontouchstart="showOriginalInModal()" 
                ontouchend="showBlurredInModal()">
                Пробел / Зажать: оригинал
            </button>
            <button type="button" class="chip-btn chip-draw" id="btnToggleDraw" onclick="toggleDrawBoxMode()" title="Включить выделение рамкой мышью (Shift+M)">
                Нарисовать рамку
            </button>
            <button type="button" class="chip-btn chip-reject" id="btnCancelDraw" onclick="toggleDrawBoxMode(false)" style="display: none;" title="Отменить режим рисования (Esc)">
                Отмена
            </button>
            <button type="button" class="chip-btn chip-note" id="btnUndoBlur" onclick="undoCustomBox()" style="display: none;" title="Отменить последний добавленный блюр">
                Отменить блюр (Undo)
            </button>
            
            <!-- Панель быстрых решений прямо в модалке -->
            <div class="modal-quick-actions">
                <button type="button" class="chip-btn chip-approve" id="modal-chip-app" onclick="setQuickStatusFromModal('approved')" title="Утвердить (Хоткей: A)">
                    Принято [A]
                </button>
                <button type="button" class="chip-btn chip-reject" id="modal-chip-fp" onclick="setQuickStatusFromModal('fp')" title="Ложное срабатывание (Хоткей: F)">
                    Ложное [F]
                </button>
                <button type="button" class="chip-btn chip-reject" id="modal-chip-offset" onclick="setQuickStatusFromModal('offset')" title="Сдвиг блюра (Хоткей: W)">
                    Сдвиг [W]
                </button>
                <button type="button" class="chip-btn chip-reject" id="modal-chip-missed" onclick="setQuickStatusFromModal('missed')" title="Пропущен параметр (Хоткей: M)">
                    Пропуск [M]
                </button>
            </div>
        </div>

        <div class="modal-header-right">
            <button class="btn-modal-close" onclick="closeModal()">Закрыть (Esc)</button>
        </div>
    </div>

    <div class="modal-viewport" id="modalViewport" 
        onwheel="onWheelZoom(event)" 
        onmousedown="onMouseDown(event)" 
        onmousemove="onMouseMove(event)" 
        onmouseup="onMouseUp(event)">
        
        <!-- Левая зона навигации (предыдущее) -->
        <div class="nav-zone nav-zone-left" id="navZoneLeft" onclick="prevCard()" title="Предыдущее изображение (←)">
            <div class="nav-arrow-circle" id="navCircleLeft">❮</div>
        </div>

        <!-- Контейнер масштабируемого изображения -->
        <div class="modal-img-container" id="modalImgContainer">
            <div id="modalImgWrapper" style="position: relative; display: inline-block; line-height: 0;">
                <img id="lightboxImg" src="" alt="Modal View">
                <svg id="selectionSvg" viewBox="0 0 1000 1000" preserveAspectRatio="none"
                     style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 45;">
                    <rect id="activeSelectionRect" x="0" y="0" width="0" height="0"
                          fill="rgba(239, 68, 68, 0.25)" stroke="#ef4444" stroke-width="2" stroke-dasharray="4 2" style="display: none;" />
                </svg>
            </div>
        </div>

        <!-- Правая зона навигации (следующее) -->
        <div class="nav-zone nav-zone-right" id="navZoneRight" onclick="nextCard()" title="Следующее изображение (→)">
            <div class="nav-arrow-circle" id="navCircleRight">❯</div>
        </div>

        <!-- Плавающий мини-виджет масштабирования (в углу просмотрщика) -->
        <div class="floating-zoom-hud">
            <button class="hud-btn" onclick="zoomIn()" title="Увеличить (+)">+</button>
            <button class="hud-btn" onclick="zoomOut()" title="Уменьшить (-)">-</button>
            <button class="hud-btn" onclick="resetZoom()" title="Центрировать и вернуть масштаб 100%">⛶ Центр</button>
            <span class="hud-scale" id="modalZoomLevel">100%</span>
        </div>
    </div>

    <div class="modal-footer">
        <div id="modalFooterHint">
            Зажмите и удерживайте <span class="key-hint">Пробел</span> (на ПК) или <span class="key-hint">палец на картинке</span> (на планшете), чтобы временно убрать блюр и увидеть оригинал.
        </div>
        <div class="keys-legend">
            Хоткеи: <span class="key-hint">A</span> Принять &nbsp;|&nbsp; 
            <span class="key-hint">F</span> Ложное &nbsp;|&nbsp; 
            <span class="key-hint">W</span> Сдвиг &nbsp;|&nbsp; 
            <span class="key-hint">M</span> Пропуск &nbsp;|&nbsp; 
            <span class="key-hint">Shift+M</span> Рамка &nbsp;|&nbsp; 
            Листание: <span class="key-hint">←</span> <span class="key-hint">→</span>
        </div>
    </div>
</div>

<script>
const BATCH_ITEMS = {items_json};
const BATCH_IDX = {batch_idx};
const API_BASE = window.location.protocol.startsWith('http') ? '' : 'http://127.0.0.1:8788';
let userFeedback = {{}};
let isServerConnected = false;
let jobPollTimer = null;
let noteDebounceTimer = null;

const REJECT_LABELS = {{
    'fp': 'Ложное срабатывание (чистый скриншот / стандартные элементы)',
    'offset': 'Неверное положение / смещение блюра',
    'missed': 'Пропущен чувствительный параметр / недозаблюрено'
}};

function showToast(msg, bg = '#16a34a') {{
    const el = document.getElementById('toastMsg');
    if (!el) return;
    el.textContent = msg;
    el.style.background = bg;
    el.style.display = 'block';
    clearTimeout(el._timer);
    el._timer = setTimeout(() => {{ el.style.display = 'none'; }}, 2600);
}}

// Загрузка сохраненного фидбека из localStorage + синхронизация с локальным сервером
async function loadFeedback() {{
    try {{
        const saved = localStorage.getItem('axonbot_feedback_batch_' + BATCH_IDX);
        if (saved) {{
            userFeedback = JSON.parse(saved);
            applyFeedbackToUI();
        }}
    }} catch(e) {{}}

    try {{
        const res = await fetch(API_BASE + '/api/state?batch=' + BATCH_IDX);
        if (res.ok) {{
            const data = await res.json();
            isServerConnected = true;
            const statusEl = document.getElementById('liveSyncStatus');
            if (statusEl) {{
                statusEl.textContent = 'Live Sync: audit_log.json';
                statusEl.classList.remove('offline');
            }}
            const serverItems = data.batch_items || {{}};
            const localCount = Object.keys(userFeedback).length;
            const serverCount = Object.keys(serverItems).length;
            const isBatchApplied = (data.applied_batches || []).includes(BATCH_IDX);

            if (serverCount > 0 && (isBatchApplied || serverCount >= localCount)) {{
                // Если батч уже обработан агентом/применен на сервере — приоритет у audit_log.json
                userFeedback = serverItems;
                saveFeedbackLocal();
                applyFeedbackToUI();
            }} else if (localCount > serverCount) {{
                await syncFullBatchToServer(true);
            }}
            renderServerState(data);
        }} else {{
            setOfflineBadge();
        }}
    }} catch(e) {{
        setOfflineBadge();
    }}
}}

function setOfflineBadge() {{
    isServerConnected = false;
    const statusEl = document.getElementById('liveSyncStatus');
    if (statusEl) {{
        if (window.location.protocol === 'file:') {{
            statusEl.innerHTML = 'Offline (file:// — <a href="http://127.0.0.1:8788/Data/review_batches/batch_' + String(BATCH_IDX).padStart(2, '0') + '/report.html" style="color: #60a5fa; text-decoration: underline;">открыть через Live Server</a>)';
        }} else {{
            statusEl.textContent = 'Offline (локальное хранилище)';
        }}
        statusEl.classList.add('offline');
    }}
}}

function renderServerState(data) {{
    const navEl = document.getElementById('batchNavPills');
    if (navEl && data.available_batches) {{
        const appliedSet = new Set(data.applied_batches || []);
        const isFileProto = window.location.protocol === 'file:';
        let html = '<span style="font-size: 12px; color: var(--text-secondary); margin-right: 4px;">Батчи:</span>';
        data.available_batches.forEach(b => {{
            const bStr = String(b).padStart(2, '0');
            const isCurr = (b === BATCH_IDX);
            const isApp = appliedSet.has(b);
            const cls = 'batch-pill' + (isCurr ? ' current' : '') + (isApp ? ' applied' : '');
            const check = isApp ? '✓' : '';
            const href = isFileProto ? `../batch_${{bStr}}/report.html` : `/Data/review_batches/batch_${{bStr}}/report.html`;
            html += `<a href="${{href}}" class="${{cls}}" title="Batch ${{bStr}}${{isApp ? ' (применен к боевым)' : ''}}">${{bStr}}${{check}}</a>`;
        }});
        navEl.innerHTML = html;
    }}

    const btnApply = document.getElementById('btnApplyBatch');
    if (btnApply && data.applied_batches && data.applied_batches.includes(BATCH_IDX)) {{
        btnApply.textContent = 'Применено';
        btnApply.className = 'btn-action btn-apply-batch is-ready';
        btnApply.title = `Batch ${{String(BATCH_IDX).padStart(2, '0')}} уже применен к боевым файлам`;
    }}

    const btnErr = document.getElementById('btnShowErrors');
    const cntErr = document.getElementById('serverErrorsCount');
    if (btnErr && data.server_errors_count > 0) {{
        btnErr.style.display = 'inline-flex';
        if (cntErr) cntErr.textContent = data.server_errors_count;
    }}

    if (data.job_status && data.job_status.running) {{
        startJobPolling();
    }}
}}

function openQrModal() {{
    const el = document.getElementById('qrModal');
    if (el) {{
        el.style.display = 'flex';
        const txt = document.getElementById('qrModalUrlText');
        const img = document.getElementById('qrModalImg');
        const currentUrl = window.location.href;
        if (txt) {{
            txt.textContent = currentUrl.startsWith('file:') ? 'Запустите: python Scripts/media_sanitizer.py --serve' : currentUrl;
        }}
        if (img) {{
            img.src = '/Data/review_batches/mobile_qr.png?t=' + Date.now();
        }}
    }}
}}

function closeQrModal(e) {{
    const el = document.getElementById('qrModal');
    if (el) el.style.display = 'none';
}}

async function openErrorsModal() {{
    const el = document.getElementById('errorsModal');
    const txt = document.getElementById('errorsLogText');
    if (el && txt) {{
        el.style.display = 'flex';
        txt.textContent = 'Загрузка логов ошибок...';
        try {{
            const res = await fetch(API_BASE + '/api/server-errors');
            if (res.ok) {{
                const data = await res.json();
                txt.textContent = data.errors || 'Ошибок в server_errors.log нет.';
            }} else {{
                txt.textContent = 'Не удалось загрузить лог ошибок с сервера.';
            }}
        }} catch(e) {{
            txt.textContent = 'Сервер недоступен.';
        }}
    }}
}}

function closeErrorsModal(e) {{
    const el = document.getElementById('errorsModal');
    if (el) el.style.display = 'none';
}}

function saveFeedbackLocal() {{
    try {{
        localStorage.setItem('axonbot_feedback_batch_' + BATCH_IDX, JSON.stringify(userFeedback));
    }} catch(e) {{}}
    updateFeedbackCounters();
}}

async function syncFullBatchToServer(showNotification = false) {{
    try {{
        const enriched = {{}};
        for (const [k, fb] of Object.entries(userFeedback)) {{
            const idx = parseInt(k, 10);
            const item = BATCH_ITEMS[idx - 1];
            enriched[k] = {{
                ...fb,
                item_idx: idx,
                file_name: fb.file_name || (item ? item.file_name : ''),
                file_path: fb.file_path || (item ? item.file_path : ''),
                detections: item ? (item.detections || []) : []
            }};
        }}
        const res = await fetch(API_BASE + '/api/sync-batch', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
                batch_idx: BATCH_IDX,
                items: enriched
            }})
        }});
        if (res.ok) {{
            isServerConnected = true;
            const statusEl = document.getElementById('liveSyncStatus');
            if (statusEl) {{
                statusEl.textContent = 'Live Sync: audit_log.json';
                statusEl.classList.remove('offline');
            }}
            if (showNotification) {{
                showToast(`Синхронизировано с audit_log.json (${{Object.keys(enriched).length}} оценок)`);
            }}
            return true;
        }}
    }} catch(e) {{
        console.warn('Batch sync failed:', e);
    }}
    return false;
}}

async function syncSingleFeedbackToServer(idx, fbObj, showNotification = true) {{
    try {{
        const item = BATCH_ITEMS[idx - 1];
        const res = await fetch(API_BASE + '/api/feedback', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
                batch_idx: BATCH_IDX,
                item_idx: idx,
                file_name: item ? item.file_name : '',
                file_path: item ? item.file_path : '',
                detections: item ? (item.detections || []) : [],
                feedback: fbObj || null
            }})
        }});
        if (res.ok) {{
            isServerConnected = true;
            const statusEl = document.getElementById('liveSyncStatus');
            if (statusEl) {{
                statusEl.textContent = 'Live Sync: audit_log.json';
                statusEl.classList.remove('offline');
            }}
            if (showNotification) {{
                showToast('Сохранено в audit_log.json');
            }}
        }}
    }} catch(e) {{
        console.warn('Live sync failed:', e);
    }}
}}

async function applyCurrentBatch() {{
    const btn = document.getElementById('btnApplyBatch');
    if (btn) btn.disabled = true;
    try {{
        // Сначала гарантированно синхронизируем все отметки из браузера в audit_log.json
        await syncFullBatchToServer(false);

        const rejectedItems = Object.values(userFeedback).filter(fb => fb.is_rejected);
        if (rejectedItems.length > 0) {{
            const proceed = confirm(
                `Внимание: в Batch ${{String(BATCH_IDX).padStart(2, '0')}} отмечено замечаний: ${{rejectedItems.length}} шт.\\n` +
                `Все они сохранены в audit_log.json для исправления.\\n\\n` +
                `Нажмите «Отмена», чтобы передать замечания в работу.\\n` +
                `Нажмите «ОК», если хотите применить остальные превью прямо сейчас (файлы с отметкой «Ложное» заблюрены не будут).`
            );
            if (!proceed) {{
                showToast(`${{rejectedItems.length}} замечаний сохранены в audit_log.json.`, '#2563eb');
                if (btn) btn.disabled = false;
                return;
            }}
        }}

        const res = await fetch(API_BASE + '/api/apply-batch', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ batch_idx: BATCH_IDX }})
        }});
        const data = await res.json();
        if (data.ok) {{
            showToast(`Применено ${{data.applied_count}} превью к боевым файлам`, '#16a34a');
            if (btn) btn.textContent = `Batch ${{String(BATCH_IDX).padStart(2, '0')}} применен (${{data.applied_count}} файлов)`;
            loadFeedback();
        }} else {{
            alert('Ошибка применения батча: ' + (data.error || 'Unknown'));
        }}
    }} catch(e) {{
        alert('Не удалось связаться с локальным сервером (http://127.0.0.1:8788). Запустите: python Scripts/media_sanitizer.py --serve');
    }} finally {{
        if (btn) btn.disabled = false;
    }}
}}

async function runNextBatch() {{
    const nextIdx = BATCH_IDX + 1;
    const btn = document.getElementById('btnNextBatch');
    if (btn) btn.disabled = true;
    try {{
        await syncFullBatchToServer(false);

        // Проверяем, существует ли уже следующий батч
        const stRes = await fetch(API_BASE + '/api/state?batch=' + BATCH_IDX);
        if (stRes.ok) {{
            const stData = await stRes.json();
            const avail = stData.available_batches || [];
            if (avail.includes(nextIdx)) {{
                const nextStr = String(nextIdx).padStart(2, '0');
                showToast(`Batch ${{nextStr}} уже существует. Переход...`, '#2563eb');
                setTimeout(() => {{
                    if (window.location.protocol === 'file:') {{
                        window.location.href = `../batch_${{nextStr}}/report.html`;
                    }} else {{
                        window.location.href = `/Data/review_batches/batch_${{nextStr}}/report.html`;
                    }}
                }}, 400);
                return;
            }}
        }}

        // Подтверждение перед запуском реально нового фонового сканирования
        const proceed = confirm(`Запустить фоновое сканирование нового батча (Batch ${{String(nextIdx).padStart(2, '0')}})?`);
        if (!proceed) {{
            if (btn) btn.disabled = false;
            return;
        }}

        const res = await fetch(API_BASE + '/api/run-batch', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ batch_idx: nextIdx, batch_size: 20 }})
        }});
        const data = await res.json();
        if (data.ok) {{
            const nextStr = String(data.batch_idx).padStart(2, '0');
            showToast(`Сканирование Batch ${{nextStr}} запущено! Переход на дешборд...`, '#2563eb');
            setTimeout(() => {{
                if (window.location.protocol === 'file:') {{
                    window.location.href = `../batch_${{nextStr}}/report.html`;
                }} else if (data.report_url) {{
                    window.location.href = data.report_url;
                }}
            }}, 500);
            return;
        }} else {{
            alert('Не удалось запустить батч: ' + (data.error || 'Уже выполняется'));
            if (btn) btn.disabled = false;
        }}
    }} catch(e) {{
        alert('Не удалось связаться с локальным сервером (http://127.0.0.1:8788). Запустите: python Scripts/media_sanitizer.py --serve');
        if (btn) btn.disabled = false;
    }}
}}

function renderCardItemHtml(it, idx) {{
    const status = it.status || 'clean';
    const fname = it.file_name || '';
    const origRel = it.orig_rel_to_batch || ('../../BIM/attachments/' + fname);
    const previewRel = it.preview_rel_to_batch || '';
    const dets = it.detections || [];
    const dims = it.dimensions || {{}};
    const contextDesc = it.context_desc || 'Стандартный интерфейс / чертеж без корпоративных брендов.';
    let dimStr = (dims.width || 0) + 'x' + (dims.height || 0) + ' px';
    if (dims.frames && dims.frames > 1) dimStr += ' | ' + dims.frames + ' кадров';

    let statusBadge = '';
    if (status === 'blurred') {{
        statusBadge = '<span class="badge badge-blurred">Blurred ' + dets.length + '</span>';
    }} else if (status === 'clean') {{
        statusBadge = '<span class="badge badge-clean">Clean</span>';
    }} else if (status === 'quarantine') {{
        statusBadge = '<span class="badge badge-quarantine">Карантин ' + (dims.frames || 1) + '</span>';
    }}

    let manualEdits = 0;
    dets.forEach(d => {{
        if (d.category === 'manual_box' || (d.matched_anchor && d.matched_anchor.toLowerCase().includes('manual'))) {{
            manualEdits++;
        }}
    }});
    const iterBadge = manualEdits > 0 ? '<span class="badge badge-iter" title="Количество итераций правок">v' + (manualEdits + 1) + '</span>' : '';

    let detsListHtml = '';
    if (dets && dets.length > 0) {{
        let itemsHtml = '';
        dets.forEach(d => {{
            itemsHtml += '<li><strong>[' + (d.category || '') + ']</strong> <code>' + (d.text_snippet || '') + '</code> &mdash; <span class="reason">' + (d.reason || '') + '</span></li>';
        }});
        detsListHtml = '<div class="detections-box"><h4>Обнаруженные конфиденциальные данные:</h4><ul>' + itemsHtml + '</ul></div>';
    }} else if (status === 'quarantine') {{
        detsListHtml = '<div class="detections-box quarantine-box"><p>Файл в папке <code>Data/manual_review_media/pending/' + fname + '</code> для ручной проверки в ScreenToGif/Photoshop.</p></div>';
    }}

    const quickBarHtml = '<div class="quick-review-bar" id="quick-bar-' + idx + '">' +
        '<div class="quick-chips-group">' +
            '<button type="button" class="chip-btn chip-approve" id="chip-app-' + idx + '" onclick="setQuickStatus(' + idx + ', \\'approved\\')" title="Утвердить результат (Хоткей: A)">Принято [A]</button>' +
            '<button type="button" class="chip-btn chip-reject" id="chip-fp-' + idx + '" onclick="setQuickStatus(' + idx + ', \\'fp\\')" title="Ложное срабатывание (Хоткей: F)">Ложное [F]</button>' +
            '<button type="button" class="chip-btn chip-reject" id="chip-offset-' + idx + '" onclick="setQuickStatus(' + idx + ', \\'offset\\')" title="Неверное положение / сдвиг блюра (Хоткей: W)">Сдвиг [W]</button>' +
            '<button type="button" class="chip-btn chip-reject" id="chip-missed-' + idx + '" onclick="setQuickStatus(' + idx + ', \\'missed\\')" title="Пропущен чувствительный параметр (Хоткей: M)">Пропуск [M]</button>' +
            '<button type="button" class="chip-btn chip-note" id="chip-note-toggle-' + idx + '" onclick="toggleQuickNote(' + idx + ')" title="Добавить текстовое пояснение">Заметка</button>' +
        '</div>' +
        '<div class="quick-status-text" id="quick-status-text-' + idx + '"></div>' +
        '<div class="quick-note-box" id="quick-note-box-' + idx + '" style="display: none;">' +
            '<div class="note-input-row">' +
                '<input type="text" id="quick-note-input-' + idx + '" placeholder="Пояснение..." onkeydown="if(event.key===\\'Enter\\') saveQuickNote(' + idx + ')">' +
                '<button type="button" class="chip-btn chip-approve" onclick="saveQuickNote(' + idx + ')">Сохранить</button>' +
            '</div>' +
        '</div>' +
    '</div>';

    let visualHtml = '';
    if (status === 'blurred') {{
        visualHtml = '<div class="media-comparison">' +
            '<div class="media-col"><span class="col-label">Оригинал</span><img src="' + origRel + '" loading="lazy" alt="Original" onclick="openModal(' + (idx - 1) + ')"></div>' +
            '<div class="media-col"><span class="col-label">Результат</span><img src="' + previewRel + '" loading="lazy" alt="Blurred" onclick="openModal(' + (idx - 1) + ')"></div>' +
        '</div>';
    }} else {{
        visualHtml = '<div class="media-single">' +
            '<span class="col-label">Оригинал</span><img src="' + origRel + '" loading="lazy" alt="Clean" onclick="openModal(' + (idx - 1) + ')">' +
        '</div>';
    }}

    return '<div class="card status-' + status + '" data-status="' + status + '" id="card-' + idx + '">' +
        '<div class="card-processing-overlay" id="processing-overlay-' + idx + '">' +
            '<div class="processing-spinner"></div>' +
            '<span>В обработке агентом...</span>' +
        '</div>' +
        '<div class="card-header">' +
            '<div class="card-title">' +
                '<span class="card-num">#' + idx + '</span>' +
                '<span class="card-name">' + fname + '</span>' +
                '<span class="card-dim">' + dimStr + '</span>' +
                statusBadge +
                iterBadge +
            '</div>' +
            '<div class="card-badges">' +
                '<span class="human-badge" id="human-badge-' + idx + '" style="display: none;">Одобрено</span>' +
            '</div>' +
        '</div>' +
        '<div class="card-body">' +
            '<div class="card-context-desc"><strong>Контекст:</strong> ' + contextDesc + '</div>' +
            visualHtml +
            detsListHtml +
            quickBarHtml +
        '</div>' +
    '</div>';
}}

let isSyncingCards = false;

async function syncNewCardsFromMeta() {{
    if (isSyncingCards) return;
    isSyncingCards = true;
    try {{
        const metaRes = await fetch('batch_meta.json?t=' + Date.now());
        if (!metaRes.ok) return;
        const meta = await metaRes.json();
        const serverResults = meta.results || [];
        if (serverResults.length > BATCH_ITEMS.length) {{
            const gridEl = document.querySelector('.grid');
            const startIndex = BATCH_ITEMS.length;
            const newItems = serverResults.slice(startIndex);
            newItems.forEach((it, i) => {{
                const cardIdx = startIndex + i + 1;
                BATCH_ITEMS.push(it);
                if (gridEl) {{
                    gridEl.insertAdjacentHTML('beforeend', renderCardItemHtml(it, cardIdx));
                }}
            }});
            updateFilterCounts();
            applyFeedbackToUI();
        }}
    }} catch(e) {{
    }} finally {{
        isSyncingCards = false;
    }}
}}

function updateFilterCounts() {{
    const total = BATCH_ITEMS.length;
    const blurredCount = BATCH_ITEMS.filter(it => it.status === 'blurred').length;
    const cleanCount = BATCH_ITEMS.filter(it => it.status === 'clean').length;
    const quarantineCount = BATCH_ITEMS.filter(it => it.status === 'quarantine').length;

    const btnAll = document.querySelector('.filter-btn:nth-child(2)');
    if (btnAll) btnAll.textContent = 'Все (' + total + ')';
    const btnBlurred = document.querySelector('.filter-btn:nth-child(3)');
    if (btnBlurred) btnBlurred.textContent = 'Заблюрено (' + blurredCount + ')';
    const btnClean = document.querySelector('.filter-btn:nth-child(4)');
    if (btnClean) btnClean.textContent = 'Чистые (' + cleanCount + ')';
    const btnQuarantine = document.querySelector('.filter-btn:nth-child(5)');
    if (btnQuarantine) btnQuarantine.textContent = 'Карантин (' + quarantineCount + ')';

    updateCountsAndButtons();
}}

function startJobPolling() {{
    const banner = document.getElementById('jobProgressBanner');
    if (banner) banner.style.display = 'flex';
    const btn = document.getElementById('btnNextBatch');
    if (btn) btn.disabled = true;

    if (jobPollTimer) clearInterval(jobPollTimer);
    jobPollTimer = setInterval(async () => {{
        try {{
            const res = await fetch(API_BASE + '/api/job-status');
            if (!res.ok) return;
            const st = await res.json();
            const titleEl = document.getElementById('jobProgressTitle');
            const countEl = document.getElementById('jobProgressCounter');
            const fillEl = document.getElementById('jobProgressFill');
            const subEl = document.getElementById('jobProgressSub');

            const bStr = String(st.batch_idx || (BATCH_IDX + 1)).padStart(2, '0');
            if (titleEl) titleEl.textContent = `Сканирование Batch ${{bStr}} (файлы выкладываются на лету)...`;
            if (countEl) countEl.textContent = `${{st.current || 0}} / ${{st.total || 20}}`;
            const pct = st.total > 0 ? Math.round((st.current / st.total) * 100) : 5;
            if (fillEl) fillEl.style.width = `${{pct}}%`;
            if (subEl) subEl.textContent = st.current_file ? `Обработка: ${{st.current_file}}` : 'Подготовка VLM...';

            // Если открыт именно этот батч и сервер выложил новые файлы — бесшовно добавляем в DOM без перезагрузки страницы
            if (st.running && st.batch_idx === BATCH_IDX) {{
                if (st.current > BATCH_ITEMS.length) {{
                    syncNewCardsFromMeta();
                }}
            }}

            if (!st.running && st.done) {{
                clearInterval(jobPollTimer);
                jobPollTimer = null;
                if (fillEl) fillEl.style.width = '100%';
                if (subEl) subEl.textContent = `Batch ${{bStr}} полностью готов.`;
                showToast(`Batch ${{bStr}} готов!`, '#16a34a');
                syncNewCardsFromMeta();
                if (st.batch_idx !== BATCH_IDX) {{
                    setTimeout(() => {{
                        if (window.location.protocol === 'file:') {{
                            window.location.href = `../batch_${{bStr}}/report.html`;
                        }} else if (st.report_url) {{
                            window.location.href = st.report_url;
                        }}
                    }}, 700);
                }}
            }} else if (!st.running && st.error) {{
                clearInterval(jobPollTimer);
                jobPollTimer = null;
                if (subEl) subEl.textContent = `Ошибка: ${{st.error}}`;
                if (btn) btn.disabled = false;
            }}
        }} catch(e) {{}}
    }}, 1500);
}}

// Быстрая установка статуса в 1 клик
function setQuickStatus(idx, type) {{
    const item = BATCH_ITEMS[idx - 1];
    const current = userFeedback[idx];

    // Повторный клик снимает статус
    if (current && current.type === type) {{
        delete userFeedback[idx];
    }} else {{
        userFeedback[idx] = {{
            file_name: item.file_name,
            file_path: item.file_path,
            type: type,
            is_approved: type === 'approved',
            is_rejected: ['fp', 'offset', 'missed'].includes(type),
            label: REJECT_LABELS[type] || '',
            note: (current && current.note) ? current.note : ''
        }};
    }}
    saveFeedbackLocal();
    applyFeedbackToUI();
    syncModalQuickBar();

    // Реактивное переключение превью при отметке FP (Ложное)
    const cardImg = document.querySelector(`#card-${{idx}} img`);
    const cardBadge = document.querySelector(`#card-${{idx}} .badge`);
    if (type === 'fp' && userFeedback[idx]) {{
        if (cardImg && item.orig_rel_to_batch) cardImg.src = item.orig_rel_to_batch;
        if (cardBadge) {{
            cardBadge.textContent = 'Оригинал (FP: Clean)';
            cardBadge.className = 'badge badge-clean';
        }}
    }} else if (current && current.type === 'fp' && !userFeedback[idx]) {{
        // Возврат исходного вида при снятии отметки
        if (cardImg && item.preview_rel_to_batch) cardImg.src = item.preview_rel_to_batch;
        if (cardBadge) {{
            cardBadge.textContent = 'Вид: С блюром';
            cardBadge.className = 'badge badge-blurred';
        }}
    }}

    syncSingleFeedbackToServer(idx, userFeedback[idx] || null, false);

    // Автономный реактивный запуск агента при установке замечания
    if (userFeedback[idx] && ['fp', 'offset', 'missed'].includes(type)) {{
        triggerAgentForItem(idx);
    }}
}}

function setQuickStatusFromModal(type) {{
    setQuickStatus(currentModalIdx + 1, type);
}}

function toggleQuickNote(idx) {{
    const box = document.getElementById('quick-note-box-' + idx);
    if (!box) return;
    box.style.display = box.style.display === 'none' ? 'block' : 'none';
    if (box.style.display === 'block') {{
        const input = document.getElementById('quick-note-input-' + idx);
        if (input) input.focus();
    }}
}}

function saveQuickNote(idx) {{
    const input = document.getElementById('quick-note-input-' + idx);
    if (!input) return;
    const noteText = input.value.trim();
    if (!noteText) {{
        showToast('Введите текст пояснения перед сохранением', '#f59e0b');
        return;
    }}
    const item = BATCH_ITEMS[idx - 1];
    if (!userFeedback[idx]) {{
        userFeedback[idx] = {{
            file_name: item.file_name,
            file_path: item.file_path,
            type: 'note',
            is_approved: false,
            is_rejected: true,
            label: 'Замечание пользователя',
            note: noteText
        }};
    }} else {{
        userFeedback[idx].note = noteText;
        userFeedback[idx].is_rejected = true;
        userFeedback[idx].is_approved = false;
        if (!userFeedback[idx].label) userFeedback[idx].label = 'Замечание пользователя';
    }}
    saveFeedbackLocal();
    applyFeedbackToUI();
    syncSingleFeedbackToServer(idx, userFeedback[idx], false);
    showToast(`Заметка сохранена для #${{idx}}`, '#16a34a');
    triggerAgentForItem(idx);
}}

let activeAgentPolling = {{}};

async function triggerAgentForItem(idx) {{
    const card = document.getElementById('card-' + idx);
    if (card) {{
        card.classList.add('is-agent-processing');
    }}
    const item = BATCH_ITEMS[idx - 1];
    const fb = userFeedback[idx] || {{}};

    const payload = {{
        batch_idx: BATCH_IDX,
        item_idx: idx,
        file_name: item ? item.file_name : (fb.file_name || ''),
        file_path: item ? item.file_path : (fb.file_path || ''),
        type: fb.type || 'flagged',
        label: fb.label || 'Замечание пользователя',
        note: fb.note || '',
        detections: item ? (item.detections || []) : []
    }};

    try {{
        const res = await fetch(API_BASE + '/api/queue-item', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify(payload)
        }});
        if (res.ok) {{
            const data = await res.json();
            showToast(`Файл #${{idx}} передан агенту в обработку`, '#7c3aed');
            pollItemAgentStatus(idx, data.task_id);
        }} else {{
            showToast('Не удалось поставить задачу агенту', '#ef4444');
            if (card) card.classList.remove('is-agent-processing');
        }}
    }} catch(e) {{
        showToast('Сервер недоступен для запуска агента', '#ef4444');
        if (card) card.classList.remove('is-agent-processing');
    }}
}}

function pollItemAgentStatus(idx, taskId) {{
    if (activeAgentPolling[idx]) clearInterval(activeAgentPolling[idx]);

    const pollStart = Date.now();
    activeAgentPolling[idx] = setInterval(async () => {{
        try {{
            const res = await fetch(API_BASE + '/api/agent-queue');
            if (!res.ok) return;
            const q = await res.json();
            const tasks = q.tasks || [];
            const myTask = tasks.find(t => t.id === taskId);

            if (myTask && myTask.status === 'resolved') {{
                clearInterval(activeAgentPolling[idx]);
                delete activeAgentPolling[idx];

                await refreshItemFromMeta(idx);
                const card = document.getElementById('card-' + idx);
                if (card) {{
                    card.classList.remove('is-agent-processing');
                }}
                showToast(`Агент успешно завершил обработку файла #${{idx}}!`, '#16a34a');
            }} else if (myTask && myTask.status === 'error') {{
                clearInterval(activeAgentPolling[idx]);
                delete activeAgentPolling[idx];

                const card = document.getElementById('card-' + idx);
                if (card) {{
                    card.classList.remove('is-agent-processing');
                }}
                showToast(`Ошибка обработки агентом: ${{myTask.error || myTask.error_details || 'Сбой калибровки'}}`, '#ef4444');
            }}

            // Таймаут опроса 15 минут
            if (Date.now() - pollStart > 900000) {{
                clearInterval(activeAgentPolling[idx]);
                delete activeAgentPolling[idx];
                const card = document.getElementById('card-' + idx);
                if (card) card.classList.remove('is-agent-processing');
            }}
        }} catch(e) {{}}
    }}, 2500);
}}

async function refreshItemFromMeta(idx) {{
    try {{
        const res = await fetch('batch_meta.json?t=' + Date.now());
        if (!res.ok) return;
        const meta = await res.json();
        const results = meta.results || [];
        const freshItem = results[idx - 1];
        if (freshItem) {{
            BATCH_ITEMS[idx - 1] = freshItem;
            const cardImg = document.querySelector(`#card-${{idx}} img:last-of-type`);
            const targetSrc = freshItem.preview_rel_to_batch 
                ? (freshItem.preview_rel_to_batch + '?t=' + Date.now()) 
                : freshItem.orig_rel_to_batch;
            if (cardImg) cardImg.src = targetSrc;

            const badge = document.querySelector(`#card-${{idx}} .badge`);
            if (badge) {{
                if (freshItem.status === 'blurred') {{
                    badge.className = 'badge badge-blurred';
                    badge.textContent = `Blurred ${{freshItem.detections ? freshItem.detections.length : 0}}`;
                }} else if (freshItem.status === 'clean') {{
                    badge.className = 'badge badge-clean';
                    badge.textContent = 'Clean';
                }}
            }}
        }}
    }} catch(e) {{}}
}}

function applyFeedbackToUI() {{
    let flaggedCount = 0;
    let approvedCount = 0;
    let recheckCount = 0;

    for (let idx = 1; idx <= BATCH_ITEMS.length; idx++) {{
        const fb = userFeedback[idx];
        const card = document.getElementById('card-' + idx);
        const humanBadge = document.getElementById('human-badge-' + idx);
        const statusText = document.getElementById('quick-status-text-' + idx);
        const noteInput = document.getElementById('quick-note-input-' + idx);

        const btnApp = document.getElementById('chip-app-' + idx);
        const btnFp = document.getElementById('chip-fp-' + idx);
        const btnOffset = document.getElementById('chip-offset-' + idx);
        const btnMissed = document.getElementById('chip-missed-' + idx);
        const btnNote = document.getElementById('chip-note-toggle-' + idx);

        // Сброс активных классов
        [btnApp, btnFp, btnOffset, btnMissed, btnNote].forEach(b => {{ if (b) b.classList.remove('active'); }});

        if (card) {{
            card.classList.remove('has-error-marked', 'is-human-approved', 'is-recheck');
        }}

        if (fb) {{
            if (noteInput && fb.note) noteInput.value = fb.note;

            if (fb.type === 'recheck' || fb.is_recheck) {{
                recheckCount++;
                if (card) card.classList.add('is-recheck');
                if (humanBadge) {{
                    humanBadge.textContent = 'На проверку';
                    humanBadge.className = 'badge-human badge-recheck';
                    humanBadge.style.display = 'inline-block';
                }}
                if (statusText) {{
                    statusText.textContent = fb.label || 'На проверку (исправлено агентом)';
                    statusText.className = 'quick-status-text text-recheck';
                }}
            }} else if (fb.is_approved) {{
                approvedCount++;
                if (card) card.classList.add('is-human-approved');
                if (humanBadge) {{
                    humanBadge.textContent = 'Одобрено';
                    humanBadge.className = 'badge-human';
                    humanBadge.style.display = 'inline-block';
                }}
                if (btnApp) btnApp.classList.add('active');
                if (statusText) {{
                    statusText.textContent = 'Утверждено';
                    statusText.className = 'quick-status-text text-approved';
                }}
            }} else if (fb.is_rejected) {{
                flaggedCount++;
                if (card) card.classList.add('has-error-marked');
                if (humanBadge) humanBadge.style.display = 'none';

                if (fb.type === 'fp' && btnFp) btnFp.classList.add('active');
                if (fb.type === 'offset' && btnOffset) btnOffset.classList.add('active');
                if (fb.type === 'missed' && btnMissed) btnMissed.classList.add('active');
                if (fb.note && btnNote) btnNote.classList.add('active');

                if (statusText) {{
                    statusText.textContent = fb.label || 'Отмечено замечание';
                    statusText.className = 'quick-status-text text-rejected';
                }}
            }}
        }} else {{
            if (humanBadge) humanBadge.style.display = 'none';
            if (statusText) {{
                statusText.textContent = '';
                statusText.className = 'quick-status-text';
            }}
        }}
    }}

    const rEl = document.getElementById('recheckCount');
    if (rEl) rEl.textContent = recheckCount;
    const fEl = document.getElementById('flaggedCount');
    if (fEl) fEl.textContent = flaggedCount;
    const aEl = document.getElementById('approvedCount');
    if (aEl) aEl.textContent = approvedCount;

    const total = BATCH_ITEMS.length;
    const unreviewed = Math.max(0, total - approvedCount - flaggedCount - recheckCount);
    const uEl = document.getElementById('unreviewedCount');
    if (uEl) uEl.textContent = unreviewed;

    const btnApply = document.getElementById('btnApplyBatch');
    if (btnApply) {{
        if (flaggedCount > 0) {{
            btnApply.textContent = `Замечаний: ${{flaggedCount}}`;
            btnApply.className = 'btn-action btn-apply-batch is-warning';
            btnApply.title = `В батче отмечено ${{flaggedCount}} замечаний. Исправьте их перед применением.`;
        }} else if (recheckCount > 0) {{
            btnApply.textContent = `На проверку: ${{recheckCount}}`;
            btnApply.className = 'btn-action btn-apply-batch is-warning';
            btnApply.title = `Осталось перепроверить ${{recheckCount}} файлов после исправлений агента.`;
        }} else if (unreviewed > 0) {{
            btnApply.textContent = `Проверено ${{approvedCount}}/${{total}}`;
            btnApply.className = 'btn-action btn-apply-batch is-pending';
            btnApply.title = `Осталось проверить ${{unreviewed}} файлов.`;
        }} else {{
            btnApply.textContent = `Применить`;
            btnApply.className = 'btn-action btn-apply-batch is-ready';
            btnApply.title = `Все ${{total}} файлов проверены и готовы к применению.`;
        }}
    }}

    const subBtn = document.getElementById('btnSubmitAgent');
    const subCnt = document.getElementById('submitAgentCount');
    if (subBtn) {{
        subBtn.disabled = flaggedCount === 0;
    }}
    if (subCnt) {{
        subCnt.textContent = flaggedCount;
    }}
}}

function syncModalQuickBar() {{
    const fb = userFeedback[currentModalIdx + 1];
    const mApp = document.getElementById('modal-chip-app');
    const mFp = document.getElementById('modal-chip-fp');
    const mOffset = document.getElementById('modal-chip-offset');
    const mMissed = document.getElementById('modal-chip-missed');
    const mHumanBadge = document.getElementById('modalHumanBadge');

    [mApp, mFp, mOffset, mMissed].forEach(b => {{ if (b) b.classList.remove('active'); }});
    if (mHumanBadge) mHumanBadge.style.display = 'none';

    if (fb) {{
        if (fb.type === 'recheck' || fb.is_recheck) {{
            if (mHumanBadge) {{
                mHumanBadge.textContent = 'На проверку';
                mHumanBadge.title = fb.label || 'Исправлено агентом';
                mHumanBadge.className = 'badge-human badge-recheck';
                mHumanBadge.style.display = 'inline-block';
            }}
        }} else if (fb.is_approved) {{
            if (mApp) mApp.classList.add('active');
            if (mHumanBadge) {{
                mHumanBadge.textContent = 'Одобрено';
                mHumanBadge.className = 'badge-human';
                mHumanBadge.style.display = 'inline-block';
            }}
        }} else if (fb.is_rejected) {{
            if (fb.type === 'fp' && mFp) mFp.classList.add('active');
            if (fb.type === 'offset' && mOffset) mOffset.classList.add('active');
            if (fb.type === 'missed' && mMissed) mMissed.classList.add('active');
        }}
    }}
}}

function updateFeedbackCounters() {{
    applyFeedbackToUI();
}}

function exportFeedbackJSON() {{
    const blob = new Blob([JSON.stringify({{ batch: BATCH_IDX, feedback: userFeedback }}, null, 2)], {{ type: 'application/json' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'batch_' + String(BATCH_IDX).padStart(2, '0') + '_feedback.json';
    a.click();
}}

function copyFeedbackToClipboard() {{
    const rejectedItems = Object.values(userFeedback).filter(fb => fb.is_rejected);
    const approvedItems = Object.values(userFeedback).filter(fb => fb.is_approved);

    if (rejectedItems.length === 0 && approvedItems.length === 0) {{
        alert('Вы еще не отметили ни одного файла.');
        return;
    }}

    let summaryText = '### Замечания по Batch ' + BATCH_IDX + ':\\n';
    if (rejectedItems.length > 0) {{
        rejectedItems.forEach(fb => {{
            summaryText += '* **Файл**: `' + fb.file_name + '`\\n';
            summaryText += '  - Замечание: ' + fb.label + '\\n';
            if (fb.note) {{
                summaryText += '  - Пояснение: ' + fb.note + '\\n';
            }}
        }});
    }} else {{
        summaryText += 'Замечаний нет (все проверенные файлы приняты).\\n';
    }}

    if (approvedItems.length > 0) {{
        summaryText += '\\n- Одобрено вручную: ' + approvedItems.length + ' файлов\\n';
    }}

    navigator.clipboard.writeText(summaryText).then(() => {{
        alert('Замечания скопированы в буфер обмена! Просто вставьте их в чат агенту.');
    }});
}}

async function submitToAgent() {{
    const rejectedEntries = Object.entries(userFeedback).filter(([k, v]) => v.is_rejected);
    if (rejectedEntries.length === 0) {{
        alert('Нет отмеченных замечаний для отправки агенту.');
        return;
    }}

    const payloadItems = rejectedEntries.map(([k, v]) => {{
        const idx = parseInt(k, 10);
        const item = BATCH_ITEMS[idx - 1];
        return {{
            item_idx: idx,
            file_name: v.file_name || (item ? item.file_name : ''),
            file_path: v.file_path || (item ? item.file_path : ''),
            type: v.type,
            label: v.label,
            note: v.note || '',
            detections: item ? (item.detections || []) : []
        }};
    }});

    const btn = document.getElementById('btnSubmitAgent');
    if (btn) btn.disabled = true;
    try {{
        await syncFullBatchToServer(false);
        const res = await fetch(API_BASE + '/api/submit-to-agent', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
                batch_idx: BATCH_IDX,
                items: payloadItems
            }})
        }});
        if (res.ok) {{
            const data = await res.json();
            showToast(`Замечания (${{payloadItems.length}} шт.) переданы агенту в очередь`, '#7c3aed');
            alert(`Замечания (${{payloadItems.length}} шт.) успешно переданы агенту в очередь!\nАгент в терминале/IDE приступает к автономной обработке замечаний.`);
        }} else {{
            alert('Не удалось отправить замечания в очередь агента.');
        }}
    }} catch(e) {{
        alert('Сервер недоступен.');
    }} finally {{
        if (btn) btn.disabled = false;
    }}
}}

// =====================================================================
// Логика ручного выделения рамкой (Manual Box Selector)
// =====================================================================
let isDrawBoxMode = false;
let isDrawingBox = false;
let drawStartX = 0;
let drawStartY = 0;

function toggleDrawBoxMode(force) {{
    isDrawBoxMode = (typeof force === 'boolean') ? force : !isDrawBoxMode;
    const btn = document.getElementById('btnToggleDraw');
    const cancelBtn = document.getElementById('btnCancelDraw');
    const svg = document.getElementById('selectionSvg');
    const rect = document.getElementById('activeSelectionRect');
    const navLeft = document.getElementById('navZoneLeft');
    const navRight = document.getElementById('navZoneRight');

    if (btn) {{
        btn.classList.toggle('active', isDrawBoxMode);
        btn.textContent = isDrawBoxMode ? 'Рамка активна' : 'Нарисовать рамку';
    }}
    if (cancelBtn) {{
        cancelBtn.style.display = isDrawBoxMode ? 'inline-flex' : 'none';
    }}
    if (svg) {{
        svg.style.pointerEvents = isDrawBoxMode ? 'all' : 'none';
        svg.style.cursor = isDrawBoxMode ? 'crosshair' : 'default';
        svg.style.zIndex = isDrawBoxMode ? '100' : '45';
    }}
    if (rect) rect.style.display = 'none';

    // Скрываем стрелки навигации на время рисования, чтобы не перехватывали клики!
    if (navLeft) navLeft.style.display = isDrawBoxMode ? 'none' : (currentModalIdx > 0 ? 'flex' : 'none');
    if (navRight) navRight.style.display = isDrawBoxMode ? 'none' : (currentModalIdx < (BATCH_ITEMS.length - 1) ? 'flex' : 'none');

    if (isDrawBoxMode) {{
        showToast('Режим рамки активен: протяните прямоугольник мышью поверх пропущенного шифра', '#0284c7');
    }}
}}

async function undoCustomBox() {{
    const item = BATCH_ITEMS[currentModalIdx];
    if (!item.detections || item.detections.length === 0) {{
        alert('Нет добавленных детекций для отмены.');
        return;
    }}
    showToast('Отмена последнего блюра...', '#2563eb');
    try {{
        const res = await fetch(API_BASE + '/api/undo-custom-box', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
                batch_idx: BATCH_IDX,
                item_idx: currentModalIdx + 1,
                file_name: item.file_name
            }})
        }});
        if (res.ok) {{
            const data = await res.json();
            item.detections = data.detections || [];
            item.status = data.status || 'clean';
            item.preview_rel_to_batch = data.preview_rel || null;

            const targetSrc = data.preview_rel ? (data.preview_rel + '?t=' + Date.now()) : item.orig_rel_to_batch;
            document.getElementById('lightboxImg').src = targetSrc;

            const cardImg = document.querySelector(`#card-${{currentModalIdx + 1}} img:last-of-type`);
            if (cardImg) cardImg.src = targetSrc;

            updateModalImage();
            saveFeedbackLocal();
            applyFeedbackToUI();
            showToast('Последний блюр успешно отменен', '#16a34a');
        }} else {{
            showToast('Ошибка отмены блюра на сервере', '#ef4444');
        }}
    }} catch(err) {{
        showToast('Сервер недоступен', '#ef4444');
    }}
}}

function setupSvgBoxSelector() {{
    const selSvg = document.getElementById('selectionSvg');
    const selRect = document.getElementById('activeSelectionRect');
    if (!selSvg || !selRect) return;

    // Планшетный режим: предотвращаем системный скролл/зум при рисовании стилусом
    selSvg.style.touchAction = 'none';

    selSvg.addEventListener('pointerdown', (e) => {{
        if (!isDrawBoxMode || (e.button !== 0 && e.pointerType === 'mouse')) return;
        e.preventDefault();
        e.stopPropagation();
        try {{ selSvg.setPointerCapture(e.pointerId); }} catch(_) {{}}
        isDrawingBox = true;
        const bounds = selSvg.getBoundingClientRect();
        drawStartX = Math.max(0, Math.min(1000, ((e.clientX - bounds.left) / bounds.width) * 1000));
        drawStartY = Math.max(0, Math.min(1000, ((e.clientY - bounds.top) / bounds.height) * 1000));
        selRect.setAttribute('x', drawStartX);
        selRect.setAttribute('y', drawStartY);
        selRect.setAttribute('width', '0');
        selRect.setAttribute('height', '0');
        selRect.style.display = 'block';
    }});

    window.addEventListener('pointermove', (e) => {{
        if (!isDrawingBox) return;
        e.preventDefault();
        const bounds = selSvg.getBoundingClientRect();
        const curX = Math.max(0, Math.min(1000, ((e.clientX - bounds.left) / bounds.width) * 1000));
        const curY = Math.max(0, Math.min(1000, ((e.clientY - bounds.top) / bounds.height) * 1000));
        const x = Math.min(drawStartX, curX);
        const y = Math.min(drawStartY, curY);
        const w = Math.abs(curX - drawStartX);
        const h = Math.abs(curY - drawStartY);
        selRect.setAttribute('x', x);
        selRect.setAttribute('y', y);
        selRect.setAttribute('width', w);
        selRect.setAttribute('height', h);
    }});

    const finishPointerDraw = async (e) => {{
        if (!isDrawingBox) return;
        isDrawingBox = false;
        try {{ selSvg.releasePointerCapture(e.pointerId); }} catch(_) {{}}
        const bounds = selSvg.getBoundingClientRect();
        const curX = Math.max(0, Math.min(1000, ((e.clientX - bounds.left) / bounds.width) * 1000));
        const curY = Math.max(0, Math.min(1000, ((e.clientY - bounds.top) / bounds.height) * 1000));

        const x1 = Math.min(drawStartX, curX);
        const x2 = Math.max(drawStartX, curX);
        const y1 = Math.min(drawStartY, curY);
        const y2 = Math.max(drawStartY, curY);

        const w = x2 - x1;
        const h = y2 - y1;

        selRect.style.display = 'none';

        if (w < 8 || h < 8) return;

        const item = BATCH_ITEMS[currentModalIdx];
        const normBox = [Math.round(y1), Math.round(x1), Math.round(y2), Math.round(x2)];

        showToast('Наложение блюра на превью...', '#2563eb');

        // Автоповтор (Retry) при нестабильной мобильной сети
        let attempts = 0;
        let success = false;
        while (attempts < 3 && !success) {{
            attempts++;
            try {{
                const res = await fetch(API_BASE + '/api/apply-custom-box', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        batch_idx: BATCH_IDX,
                        item_idx: currentModalIdx + 1,
                        file_name: item.file_name,
                        box_2d: normBox,
                        category: 'manual_box',
                        reason: 'Пользовательское выделение'
                    }})
                }});
                if (res.ok) {{
                    const data = await res.json();
                    item.detections = data.detections || [];
                    item.status = 'blurred';
                    item.preview_rel_to_batch = data.preview_rel;

                    const freshUrl = data.preview_rel + '?t=' + Date.now();
                    document.getElementById('lightboxImg').src = freshUrl;

                    const cardImg = document.querySelector(`#card-${{currentModalIdx + 1}} img:last-of-type`);
                    if (cardImg) cardImg.src = freshUrl;

                    userFeedback[currentModalIdx + 1] = {{
                        file_name: item.file_name,
                        file_path: item.file_path,
                        type: 'approved',
                        is_approved: true,
                        is_rejected: false,
                        label: 'Ручное выделение области',
                        note: ''
                    }};
                    saveFeedbackLocal();
                    applyFeedbackToUI();
                    updateModalImage();
                    showToast('Область успешно заблюрена и сохранена', '#16a34a');
                    toggleDrawBoxMode(false);
                    success = true;
                    return;
                }} else {{
                    showToast('Ошибка наложения блюра на сервере', '#ef4444');
                    break;
                }}
            }} catch(err) {{
                if (attempts < 3) {{
                    showToast(`Сбой связи. Повтор (${{attempts}}/3)...`, '#f59e0b');
                    await new Promise(r => setTimeout(r, 1200));
                }} else {{
                    showToast('Сервер недоступен (проверьте интернет)', '#ef4444');
                }}
            }}
        }}
    }};

    window.addEventListener('pointerup', finishPointerDraw);
    window.addEventListener('pointercancel', finishPointerDraw);
}}

// =====================================================================
// Логика Модального окна Lightbox
// =====================================================================
let currentModalIdx = 0;
let currentZoom = 1;
let panX = 0;
let panY = 0;
let isPanning = false;
let isSpaceHeld = false;
let startX = 0;
let startY = 0;

let savedScrollYBeforeModal = 0;

function openModal(itemIdx) {{
    savedScrollYBeforeModal = window.scrollY || window.pageYOffset || document.documentElement.scrollTop;
    document.body.style.overflow = 'hidden';
    document.body.style.touchAction = 'none';
    document.documentElement.style.overflow = 'hidden';
    document.documentElement.style.touchAction = 'none';
    currentModalIdx = itemIdx;
    currentZoom = 1;
    panX = 0;
    panY = 0;
    isSpaceHeld = false;
    toggleDrawBoxMode(false);
    updateModalImage();
    syncModalQuickBar();
    document.getElementById('lightboxModal').style.display = 'flex';
}}

function closeModal() {{
    toggleDrawBoxMode(false);
    showBlurredInModal();
    document.getElementById('lightboxModal').style.display = 'none';
    document.body.style.overflow = '';
    document.body.style.touchAction = '';
    document.documentElement.style.overflow = '';
    document.documentElement.style.touchAction = '';
    isSpaceHeld = false;
    if (typeof savedScrollYBeforeModal === 'number') {{
        window.scrollTo(0, savedScrollYBeforeModal);
    }}
}}

function updateModalImage() {{
    const item = BATCH_ITEMS[currentModalIdx];
    const imgEl = document.getElementById('lightboxImg');
    const badgeEl = document.getElementById('modalViewBadge');
    const numEl = document.getElementById('modalNum');
    const nameEl = document.getElementById('modalFileName');
    const detsBox = document.getElementById('modalDetectionsBox');
    const btnCompare = document.getElementById('btnHoldCompare');
    const hintEl = document.getElementById('modalFooterHint');
    const navZoneLeft = document.getElementById('navZoneLeft');
    const navZoneRight = document.getElementById('navZoneRight');

    numEl.textContent = '#' + (currentModalIdx + 1);
    nameEl.textContent = item.file_name;

    // Дублирование блока «Обнаруженные конфиденциальные данные:»
    if (item.detections && item.detections.length > 0) {{
        let dHtml = '<ul>';
        item.detections.forEach((d, zIdx) => {{
            const cat = d.category || 'PII';
            const txt = d.text_snippet || '';
            const reason = d.reason || '';
            dHtml += `<li><strong>[${{cat}}]</strong> <code>${{txt}}</code> &mdash; <span style="color: #94a3b8;">${{reason}}</span></li>`;
        }});
        dHtml += '</ul>';
        detsBox.innerHTML = dHtml;
        detsBox.style.display = 'block';
    }} else if (item.status === 'clean') {{
        detsBox.innerHTML = '<span style="color: #4ade80;">Конфиденциальные данные не обнаружены (оригинал чистый, без изменений).</span>';
        detsBox.style.display = 'block';
    }} else {{
        detsBox.innerHTML = '<span style="color: #f87171;">Анимированный GIF в карантине (Data/manual_review_media/pending/) для покадровой модерации.</span>';
        detsBox.style.display = 'block';
    }}

    // Управление видимостью навигационных стрелок (слева и справа)
    if (navZoneLeft) {{
        navZoneLeft.style.display = isDrawBoxMode ? 'none' : (currentModalIdx > 0 ? 'flex' : 'none');
    }}
    if (navZoneRight) {{
        navZoneRight.style.display = isDrawBoxMode ? 'none' : (currentModalIdx < (BATCH_ITEMS.length - 1) ? 'flex' : 'none');
    }}

    const undoBtn = document.getElementById('btnUndoBlur');
    if (undoBtn) {{
        undoBtn.style.display = (item.detections && item.detections.length > 0) ? 'inline-flex' : 'none';
    }}

    // Режим сравнения «Hold Space to Reveal»
    if (item.status === 'blurred' && item.preview_rel_to_batch) {{
        imgEl.src = item.preview_rel_to_batch;
        if (badgeEl) badgeEl.style.display = 'none';
        btnCompare.style.display = 'inline-flex';
        btnCompare.classList.remove('active');
        btnCompare.textContent = 'Пробел: оригинал';
        hintEl.innerHTML = 'Зажмите <span class="key-hint">Пробел</span>, чтобы временно показать оригинал без блюра.';
    }} else {{
        imgEl.src = item.orig_rel_to_batch;
        btnCompare.style.display = 'none';
        if (badgeEl) {{
            if (item.status === 'clean') {{
                badgeEl.textContent = 'Чистый';
                badgeEl.className = 'badge badge-clean';
                badgeEl.style.display = 'inline-block';
                hintEl.innerHTML = 'Конфиденциальных данных нет (оригинал без изменений). Сравнение отключено.';
            }} else {{
                badgeEl.textContent = 'Карантин';
                badgeEl.className = 'badge badge-quarantine';
                badgeEl.style.display = 'inline-block';
                hintEl.innerHTML = 'Анимированный файл в карантине для ручной проверки в ScreenToGif.';
            }}
        }}
    }}
    updateTransform();
    syncModalQuickBar();
}}

function showOriginalInModal() {{
    const item = BATCH_ITEMS[currentModalIdx];
    if (item.status !== 'blurred') return;
    const imgEl = document.getElementById('lightboxImg');
    const btnCompare = document.getElementById('btnHoldCompare');
    imgEl.src = item.orig_rel_to_batch;
    if (btnCompare) {{
        btnCompare.classList.add('active');
        btnCompare.textContent = 'Оригинал (отпустите Пробел)';
    }}
}}

function showBlurredInModal() {{
    const item = BATCH_ITEMS[currentModalIdx];
    if (item.status !== 'blurred') return;
    const imgEl = document.getElementById('lightboxImg');
    const btnCompare = document.getElementById('btnHoldCompare');
    if (item.preview_rel_to_batch) {{
        imgEl.src = item.preview_rel_to_batch;
    }}
    if (btnCompare) {{
        btnCompare.classList.remove('active');
        btnCompare.textContent = 'Пробел: оригинал';
    }}
}}

function updateTransform() {{
    const container = document.getElementById('modalImgContainer');
    container.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{currentZoom}})`;
    document.getElementById('modalZoomLevel').textContent = `${{Math.round(currentZoom * 100)}}%`;
}}

function zoomIn() {{
    currentZoom = Math.min(currentZoom * 1.25, 6);
    updateTransform();
}}
function zoomOut() {{
    currentZoom = Math.max(currentZoom / 1.25, 0.3);
    updateTransform();
}}
function resetZoom() {{
    currentZoom = 1;
    panX = 0;
    panY = 0;
    updateTransform();
}}

function nextCard() {{
    if (currentModalIdx < BATCH_ITEMS.length - 1) {{
        currentModalIdx++;
        currentZoom = 1;
        panX = 0;
        panY = 0;
        isSpaceHeld = false;
        toggleDrawBoxMode(false);
        updateModalImage();
    }}
}}
function prevCard() {{
    if (currentModalIdx > 0) {{
        currentModalIdx--;
        currentZoom = 1;
        panX = 0;
        panY = 0;
        isSpaceHeld = false;
        toggleDrawBoxMode(false);
        updateModalImage();
    }}
}}

// Зум колесиком
function onWheelZoom(e) {{
    e.preventDefault();
    if (e.deltaY < 0) {{
        zoomIn();
    }} else {{
        zoomOut();
    }}
}}

// Панорамирование мышью
function onMouseDown(e) {{
    if (e.button !== 0 || isDrawBoxMode) return;
    if (e.target.closest('.nav-zone') || e.target.closest('.floating-zoom-hud') || e.target.closest('button') || e.target.closest('input')) return;
    isPanning = true;
    startX = e.clientX - panX;
    startY = e.clientY - panY;
}}
function onMouseMove(e) {{
    if (!isPanning) return;
    panX = e.clientX - startX;
    panY = e.clientY - startY;
    updateTransform();
}}
function onMouseUp(e) {{
    isPanning = false;
}}

// =====================================================================
// Планшетные жесты: Зажатие на картинку = оригинал, Pinch-to-zoom, Pan
// =====================================================================
let initialPinchDist = 0;
let initialPinchZoom = 1;
let touchDownX = 0;
let touchDownY = 0;
let startPanX = 0;
let startPanY = 0;
let isTouchHoldingCompare = false;
let lastTapTime = 0;

function setupTouchGestures() {{
    const vp = document.getElementById('modalViewport');
    if (!vp) return;

    vp.addEventListener('touchstart', (e) => {{
        if (isDrawBoxMode) return;
        if (e.target.closest('.nav-zone') || e.target.closest('.floating-zoom-hud') || e.target.closest('button')) return;

        // Двойной тап по картинке/экрану: сброс масштаба и центрирование
        const now = Date.now();
        if (e.touches.length === 1 && (now - lastTapTime) < 320) {{
            e.preventDefault();
            resetZoom();
            if (isTouchHoldingCompare) {{
                isTouchHoldingCompare = false;
                showBlurredInModal();
            }}
            lastTapTime = 0;
            return;
        }}
        lastTapTime = now;

        if (e.touches.length === 1) {{
            const t = e.touches[0];
            touchDownX = t.clientX;
            touchDownY = t.clientY;
            startPanX = panX;
            startPanY = panY;
            isPanning = true;

            // Если касание произошло по самой картинке - зажатие сразу показывает оригинал (как Пробел!)
            const targetIsImg = e.target.closest('#modalImgWrapper') || e.target.id === 'lightboxImg';
            if (targetIsImg) {{
                isTouchHoldingCompare = true;
                showOriginalInModal();
            }}
        }} else if (e.touches.length === 2) {{
            // Два пальца: Pinch-to-Zoom
            e.preventDefault();
            isPanning = false;
            if (isTouchHoldingCompare) {{
                isTouchHoldingCompare = false;
                showBlurredInModal();
            }}
            const t1 = e.touches[0];
            const t2 = e.touches[1];
            initialPinchDist = Math.hypot(t1.clientX - t2.clientX, t1.clientY - t2.clientY);
            initialPinchZoom = currentZoom;
            startPanX = panX;
            startPanY = panY;
            startX = (t1.clientX + t2.clientX) / 2;
            startY = (t1.clientY + t2.clientY) / 2;
        }}
    }}, {{ passive: false }});

    vp.addEventListener('touchmove', (e) => {{
        if (isDrawBoxMode) return;
        if (e.target.closest('.nav-zone') || e.target.closest('.floating-zoom-hud') || e.target.closest('button')) return;
        e.preventDefault(); // Запрещаем скролл body и улет страницы!

        if (e.touches.length === 1 && isPanning) {{
            const t = e.touches[0];
            const dx = t.clientX - touchDownX;
            const dy = t.clientY - touchDownY;

            // Если палец сдвинулся больше чем на 12px - это панорамирование, возвращаем блюр
            if (isTouchHoldingCompare && Math.hypot(dx, dy) > 12) {{
                isTouchHoldingCompare = false;
                showBlurredInModal();
            }}

            panX = startPanX + dx;
            panY = startPanY + dy;
            updateTransform();
        }} else if (e.touches.length === 2 && initialPinchDist > 0) {{
            const t1 = e.touches[0];
            const t2 = e.touches[1];
            const curDist = Math.hypot(t1.clientX - t2.clientX, t1.clientY - t2.clientY);
            const factor = curDist / initialPinchDist;
            currentZoom = Math.min(6.0, Math.max(0.4, initialPinchZoom * factor));

            const midX = (t1.clientX + t2.clientX) / 2;
            const midY = (t1.clientY + t2.clientY) / 2;
            panX = startPanX + (midX - startX);
            panY = startPanY + (midY - startY);
            updateTransform();
        }}
    }}, {{ passive: false }});

    const onTouchEnd = (e) => {{
        if (isTouchHoldingCompare) {{
            isTouchHoldingCompare = false;
            showBlurredInModal();
        }}
        if (e.touches.length === 0) {{
            isPanning = false;
            initialPinchDist = 0;
        }} else if (e.touches.length === 1) {{
            const t = e.touches[0];
            touchDownX = t.clientX;
            touchDownY = t.clientY;
            startPanX = panX;
            startPanY = panY;
            isPanning = true;
            initialPinchDist = 0;
        }}
    }};

    vp.addEventListener('touchend', onTouchEnd, {{ passive: false }});
    vp.addEventListener('touchcancel', onTouchEnd, {{ passive: false }});
}}

// Отслеживание карточки под курсором для работы хоткеев A/F/W/M прямо в ленте
let hoveredCardIdx = null;
document.addEventListener('mouseover', (e) => {{
    const card = e.target.closest('.card');
    if (card && card.id && card.id.startsWith('card-')) {{
        hoveredCardIdx = parseInt(card.id.replace('card-', ''), 10);
    }}
}});

// Обработка клавиатуры (Rapid Review Hotkeys)
window.addEventListener('keydown', (e) => {{
    const modal = document.getElementById('lightboxModal');
    const isModalOpen = modal.style.display === 'flex';

    // Если фокус в текстовом поле, не перехватывать хоткеи
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    if (isModalOpen) {{
        // Полная блокировка нативного скролла клавишей Пробел при открытой модалке
        if (e.code === 'Space' || e.key === ' ' || e.keyCode === 32) {{
            e.preventDefault();
            e.stopPropagation();
            const item = BATCH_ITEMS[currentModalIdx];
            if (item && item.status === 'blurred' && !e.repeat) {{
                isSpaceHeld = true;
                showOriginalInModal();
            }}
            return;
        }} else if (e.key === 'Escape') {{
            if (isDrawBoxMode) {{
                toggleDrawBoxMode(false);
            }} else {{
                closeModal();
            }}
        }} else if (e.key === 'ArrowRight') {{
            nextCard();
        }} else if (e.key === 'ArrowLeft') {{
            prevCard();
        }} else if (e.key === 'KeyA' || e.key === 'a' || e.key === 'ф' || e.key === 'Ф') {{
            setQuickStatusFromModal('approved');
        }} else if (e.key === 'KeyF' || e.key === 'f' || e.key === 'а' || e.key === 'А') {{
            setQuickStatusFromModal('fp');
        }} else if (e.key === 'KeyW' || e.key === 'w' || e.key === 'ц' || e.key === 'Ц') {{
            setQuickStatusFromModal('offset');
        }} else if ((e.key === 'KeyM' || e.key === 'm' || e.key === 'ь' || e.key === 'Ь') && e.shiftKey) {{
            e.preventDefault();
            toggleDrawBoxMode();
        }} else if (e.key === 'KeyM' || e.key === 'm' || e.key === 'ь' || e.key === 'Ь') {{
            setQuickStatusFromModal('missed');
        }} else if (e.key === '+' || e.key === '=') {{
            zoomIn();
        }} else if (e.key === '-' || e.key === '_') {{
            zoomOut();
        }} else if (e.key === '0') {{
            resetZoom();
        }}
    }} else if (hoveredCardIdx !== null) {{
        if (e.key === 'KeyA' || e.key === 'a' || e.key === 'ф' || e.key === 'Ф') {{
            setQuickStatus(hoveredCardIdx, 'approved');
        }} else if (e.key === 'KeyF' || e.key === 'f' || e.key === 'а' || e.key === 'А') {{
            setQuickStatus(hoveredCardIdx, 'fp');
        }} else if (e.key === 'KeyW' || e.key === 'w' || e.key === 'ц' || e.key === 'Ц') {{
            setQuickStatus(hoveredCardIdx, 'offset');
        }} else if (e.key === 'KeyM' || e.key === 'm' || e.key === 'ь' || e.key === 'Ь') {{
            setQuickStatus(hoveredCardIdx, 'missed');
        }}
    }}
}});

window.addEventListener('keyup', (e) => {{
    const modal = document.getElementById('lightboxModal');
    if (!modal || modal.style.display !== 'flex') return;

    if (e.code === 'Space' || e.key === ' ' || e.keyCode === 32) {{
        e.preventDefault();
        e.stopPropagation();
        isSpaceHeld = false;
        showBlurredInModal();
    }}
}});

window.addEventListener('mouseup', () => {{
    if (!isSpaceHeld) {{
        showBlurredInModal();
    }}
}});

function filterCards(type) {{
    document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
    if (window.event && window.event.target) window.event.target.classList.add('active');
    document.querySelectorAll('.card').forEach(card => {{
        if (type === 'all') {{
            card.style.display = 'block';
        }} else if (type === 'recheck') {{
            card.style.display = card.classList.contains('is-recheck') ? 'block' : 'none';
        }} else if (type === 'flagged') {{
            card.style.display = card.classList.contains('has-error-marked') ? 'block' : 'none';
        }} else if (type === 'approved') {{
            card.style.display = card.classList.contains('is-human-approved') ? 'block' : 'none';
        }} else if (type === 'unreviewed') {{
            const isApp = card.classList.contains('is-human-approved');
            const isErr = card.classList.contains('has-error-marked');
            const isRec = card.classList.contains('is-recheck');
            card.style.display = (!isApp && !isErr && !isRec) ? 'block' : 'none';
        }} else {{
            card.style.display = card.getAttribute('data-status') === type ? 'block' : 'none';
        }}
    }});
}}

window.addEventListener('DOMContentLoaded', () => {{
    loadFeedback();
    setupSvgBoxSelector();
    setupTouchGestures();
    // Фоновый опрос состояния и синхронизация каждые 3 секунды
    setInterval(() => {{
        loadFeedback();
    }}, 3000);
}});
</script>

</body>
</html>
"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[HTML Gallery] Interactive report with Rapid Review UI generated at: {out_html}")


def get_available_batches() -> list[int]:
    """Возвращает отсортированный список существующих номеров батчей"""
    if not BATCH_BASE.exists():
        return []
    batches = []
    for d in BATCH_BASE.iterdir():
        if d.is_dir() and d.name.startswith("batch_"):
            try:
                idx = int(d.name.split("_")[1])
                if (d / "report.html").exists() or (d / "batch_meta.json").exists():
                    batches.append(idx)
            except ValueError:
                pass
    batches.sort()
    return batches


def load_audit_log() -> dict:
    """Читает журнал аудита Data/review_batches/audit_log.json (или инициализирует его)"""
    with _AUDIT_LOCK:
        if AUDIT_LOG_PATH.exists():
            try:
                with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "applied_batches" not in data:
                    data["applied_batches"] = [1, 2, 3]
                if "batches" not in data:
                    data["batches"] = {}
                return data
            except Exception:
                pass
        default_log = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "applied_batches": [1, 2, 3],
            "batches": {}
        }
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(default_log, f, ensure_ascii=False, indent=2)
        return default_log


def log_server_error(context: str, err: Exception):
    """Фиксирует ошибку в stderr, audit_log.json и server_errors.log"""
    if isinstance(err, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
        return

    tb_str = traceback.format_exc()
    ts = datetime.now().isoformat(timespec="seconds")
    err_msg = f"[{ts}] [{context}] {type(err).__name__}: {err}\n{tb_str}\n"
    sys.stderr.write(f"\n❌ [AUTONOMOUS ERROR TELEMETRY]\n{err_msg}\n")
    sys.stderr.flush()

    try:
        SERVER_ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SERVER_ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(err_msg)
    except Exception:
        pass

    try:
        audit_data = load_audit_log()
        err_list = audit_data.setdefault("server_errors", [])
        err_list.append({
            "timestamp": ts,
            "context": context,
            "error_type": type(err).__name__,
            "error_message": str(err),
            "traceback": tb_str.strip().split("\n")[-3:]
        })
        audit_data["server_errors"] = err_list[-20:]
        save_audit_log(audit_data)
    except Exception:
        pass


def get_local_lan_ip() -> str:
    """Возвращает IPv4 адрес машины в локальной сети"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def print_ascii_qr(data: str):
    """Выводит ASCII QR-код в консоль терминала"""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass


def generate_qr_image(data: str, out_path: Path):
    """Генерирует PNG QR-код для раздачи мобильным устройствам"""
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
    except Exception:
        pass


def save_audit_log(data: dict):
    """Потокобезопасно сохраняет Data/review_batches/audit_log.json"""
    with _AUDIT_LOCK:
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def update_audit_feedback(batch_idx: int, item_idx: int, file_name: str, file_path: str, detections: list, fb_obj: dict | None):
    """Обновляет статус конкретного файла в audit_log.json"""
    data = load_audit_log()
    b_key = str(batch_idx)
    if b_key not in data["batches"]:
        data["batches"][b_key] = {
            "batch_idx": batch_idx,
            "applied": batch_idx in data.get("applied_batches", []),
            "items": {}
        }
    items_map = data["batches"][b_key].setdefault("items", {})
    i_key = str(item_idx)
    if fb_obj is None:
        items_map.pop(i_key, None)
    else:
        items_map[i_key] = {
            "item_idx": item_idx,
            "file_name": fb_obj.get("file_name") or file_name,
            "file_path": fb_obj.get("file_path") or file_path,
            "type": fb_obj.get("type", ""),
            "is_approved": bool(fb_obj.get("is_approved", False)),
            "is_rejected": bool(fb_obj.get("is_rejected", False)),
            "label": fb_obj.get("label", ""),
            "note": fb_obj.get("note", ""),
            "detections": detections,
            "updated_at": datetime.now().isoformat(timespec="seconds")
        }
    save_audit_log(data)


def load_agent_queue() -> dict:
    """Читает очередь задач агента Data/review_batches/agent_queue.json"""
    with _QUEUE_LOCK:
        if AGENT_QUEUE_PATH.exists():
            try:
                with open(AGENT_QUEUE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"tasks": []}


def save_agent_queue(data: dict):
    """Потокобезопасно сохраняет очередь задач агента"""
    with _QUEUE_LOCK:
        AGENT_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def enqueue_agent_task(batch_idx: int, items: list[dict]) -> dict:
    """Ставит задачу в очередь агента Data/review_batches/agent_queue.json"""
    q = load_agent_queue()
    ts = datetime.now().isoformat(timespec="seconds")
    task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    new_task = {
        "id": task_id,
        "batch_idx": batch_idx,
        "created_at": ts,
        "status": "pending",
        "items_count": len(items),
        "items": items
    }
    q.setdefault("tasks", []).append(new_task)
    save_agent_queue(q)
    return new_task


def apply_custom_box_to_item(batch_idx: int, item_idx: int, file_name: str, box_2d: list, category: str = "manual_box", reason: str = "Ручное выделение области") -> dict:
    """Мгновенно накладывает блюр на превью по пользовательскому Bounding Box и обновляет метаданные"""
    batch_dir = BATCH_BASE / f"batch_{batch_idx:02d}"
    meta_path = batch_dir / "batch_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"batch_meta.json not found for batch {batch_idx}")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta_data = json.load(f)

    target_item = None
    for it in meta_data.get("results", []):
        if it.get("file_name") == file_name:
            target_item = it
            break

    if not target_item:
        raise ValueError(f"File {file_name} not found in batch {batch_idx}")

    # Исходный файл: бэкап (если есть) или attachments
    orig_path = PROJECT_ROOT / target_item["file_path"]
    backup_path = batch_dir / "originals_backup" / file_name
    src_img = backup_path if backup_path.exists() else orig_path

    # Стабилизация высоты бокса от тряски руки/стилуса на планшете (гарантия высоты не менее 18-20px)
    try:
        with Image.open(src_img) as im_chk:
            im_w, im_h = im_chk.size
            ymin, xmin, ymax, xmax = box_2d
            h_px = (ymax - ymin) * im_h / 1000.0
            if h_px < 18.0 and im_h >= 20:
                mid_y = (ymin + ymax) / 2.0
                delta_norm = (10.0 / im_h) * 1000.0
                ymin = max(0, int(mid_y - delta_norm))
                ymax = min(1000, int(mid_y + delta_norm))
                box_2d = [ymin, xmin, ymax, xmax]
    except Exception:
        pass

    dets = target_item.setdefault("detections", [])
    new_det = {
        "box_2d": box_2d,
        "category": category or "manual_box",
        "text_snippet": "Manual redaction",
        "matched_anchor": "Manual",
        "reason": reason or "Пользовательское выделение"
    }

    previews_dir = batch_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    out_preview = previews_dir / file_name

    apply_blur_to_image(src_img, dets, out_preview, blur_radius=22)
    target_item["preview_rel_to_batch"] = f"previews/{file_name}"

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    # Обновляем audit_log.json
    audit_data = load_audit_log()
    b_key = str(batch_idx)
    if b_key not in audit_data["batches"]:
        audit_data["batches"][b_key] = {"batch_idx": batch_idx, "applied": False, "items": {}}
    items_map = audit_data["batches"][b_key].setdefault("items", {})
    i_key = str(item_idx)
    items_map[i_key] = {
        "item_idx": item_idx,
        "file_name": file_name,
        "file_path": target_item.get("file_path", ""),
        "type": "approved",
        "is_approved": True,
        "is_rejected": False,
        "label": "Ручное исправление области",
        "note": "",
        "detections": dets,
        "updated_at": datetime.now().isoformat(timespec="seconds")
    }
    save_audit_log(audit_data)

    return target_item


def undo_custom_box_item(batch_idx: int, item_idx: int, file_name: str) -> dict:
    """Удаляет последнюю детекцию блюра и пересчитывает превью (или откатывает к clean)"""
    batch_dir = BATCH_BASE / f"batch_{batch_idx:02d}"
    meta_path = batch_dir / "batch_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"batch_meta.json not found for batch {batch_idx}")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta_data = json.load(f)

    target_item = None
    for it in meta_data.get("results", []):
        if it.get("file_name") == file_name:
            target_item = it
            break

    if not target_item:
        raise ValueError(f"File {file_name} not found in batch {batch_idx}")

    dets = target_item.get("detections", [])
    if dets:
        dets.pop()

    previews_dir = batch_dir / "previews"
    out_preview = previews_dir / file_name

    if not dets:
        target_item["status"] = "clean"
        target_item["preview_rel_to_batch"] = None
        target_item["detections"] = []
        if out_preview.exists():
            try:
                out_preview.unlink()
            except Exception:
                pass
    else:
        orig_path = PROJECT_ROOT / target_item["file_path"]
        backup_path = batch_dir / "originals_backup" / file_name
        src_img = backup_path if backup_path.exists() else orig_path
        apply_blur_to_image(src_img, dets, out_preview, blur_radius=22)
        target_item["status"] = "blurred"
        target_item["preview_rel_to_batch"] = f"previews/{file_name}"
        target_item["detections"] = dets

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    # Обновляем audit_log.json
    audit_data = load_audit_log()
    b_key = str(batch_idx)
    if b_key in audit_data["batches"]:
        items_map = audit_data["batches"][b_key].setdefault("items", {})
        i_key = str(item_idx)
        if not dets:
            items_map[i_key] = {
                "item_idx": item_idx,
                "file_name": file_name,
                "file_path": target_item.get("file_path", ""),
                "type": "approved",
                "is_approved": True,
                "is_rejected": False,
                "label": "Откат к чистому (Undo)",
                "note": "",
                "detections": [],
                "updated_at": datetime.now().isoformat(timespec="seconds")
            }
        else:
            items_map[i_key]["detections"] = dets
            items_map[i_key]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_audit_log(audit_data)

    return target_item


def print_audit_summary():
    """Выводит сводку audit_log.json в консоль для быстрого чтения агентом одной командой"""
    data = load_audit_log()
    applied = data.get("applied_batches", [])
    avail = get_available_batches()
    print("========================================================")
    print(f"[LIVE AUDIT LOG SUMMARY] ({AUDIT_LOG_PATH.relative_to(PROJECT_ROOT)})")
    print(f"   Updated at:        {data.get('updated_at')}")
    print(f"   Available batches: {avail}")
    print(f"   Applied batches:   {applied}")
    print("========================================================")

    any_flagged = False
    for b_key, b_val in sorted(data.get("batches", {}).items(), key=lambda x: int(x[0])):
        items = b_val.get("items", {})
        rejected = [v for v in items.values() if v.get("is_rejected")]
        approved = [v for v in items.values() if v.get("is_approved")]
        is_app = int(b_key) in applied
        print(f"\nBatch {int(b_key):02d} [{'APPLIED' if is_app else 'PENDING REVIEW'}]: "
              f"Approved={len(approved)}, Flagged={len(rejected)}")
        for r in sorted(rejected, key=lambda x: x.get("item_idx", 0)):
            any_flagged = True
            print(f"   [#{r.get('item_idx')}] {r.get('file_name')} ({r.get('file_path')})")
            print(f"      Тип: {r.get('type')} — {r.get('label')}")
            if r.get("note"):
                print(f"      Комментарий: \"{r.get('note')}\"")
            if r.get("detections"):
                print(f"      Детекции: {json.dumps(r.get('detections'), ensure_ascii=False)}")

    if not any_flagged:
        print("\nСпорных замечаний (Flagged items) в журнале нет.")

    errors = data.get("server_errors", [])
    if errors:
        print(f"\nСЕРВЕРНЫЕ ОШИБКИ И СБОИ ({len(errors)} шт.):")
        for err in errors[-5:]:
            print(f"   [{err.get('timestamp')}] {err.get('context')}: {err.get('error_type')} - {err.get('error_message')}")
            for line in err.get('traceback', []):
                print(f"      {line}")


def regenerate_all_reports():
    """Перегенерирует HTML-отчеты для всех уже существующих батчей с новым UI Web Dashboard"""
    for b_idx in get_available_batches():
        b_dir = BATCH_BASE / f"batch_{b_idx:02d}"
        meta_path = b_dir / "batch_meta.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                mdata = json.load(f)
            results = mdata.get("results", [])
            generate_batch_html_report(b_idx, results, b_dir / "report.html")


def run_batch(batch_idx: int, batch_size: int = 20) -> Path | None:
    """Выполняет пакетную обработку N файлов с генерацией HTML-отчета"""
    all_media = get_ordered_media_list()
    total_media = len(all_media)
    start_idx = (batch_idx - 1) * batch_size
    end_idx = min(start_idx + batch_size, total_media)

    if start_idx >= total_media:
        print(f"Error: Batch {batch_idx} is out of bounds (total items: {total_media}, requested start: {start_idx}).")
        return None

    batch_slice = all_media[start_idx:end_idx]
    print(f"\n========================================================")
    print(f"🚀 Running Batch {batch_idx:02d} ({len(batch_slice)} items, index {start_idx}..{end_idx-1} of {total_media})")
    print(f"========================================================")

    _JOB_STATE["batch_idx"] = batch_idx
    _JOB_STATE["total"] = len(batch_slice)
    _JOB_STATE["current"] = 0

    batch_dir = BATCH_BASE / f"batch_{batch_idx:02d}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    previews_dir = batch_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    meta_path = batch_dir / "batch_meta.json"
    html_report_path = batch_dir / "report.html"

    # Сразу создаем первичный отчет для мгновенного отображения на дешборде
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "batch_idx": batch_idx,
            "batch_size": batch_size,
            "total_items": 0,
            "results": [],
            "in_progress": True
        }, f, ensure_ascii=False, indent=2)
    generate_batch_html_report(batch_idx, [], html_report_path)

    client = get_gemini_client()
    batch_results = []

    for idx, fpath in enumerate(batch_slice, 1):
        _JOB_STATE["current"] = idx
        _JOB_STATE["current_file"] = fpath.name
        print(f"\n[{idx}/{len(batch_slice)}] Processing: {fpath.name}", flush=True)
        time.sleep(2.0)  # Вежливая пауза для соблюдения лимитов API (15 RPM)
        ext = fpath.suffix.lower()

        try:
            with Image.open(fpath) as img:
                w, h = img.size
                n_frames = getattr(img, 'n_frames', 1)
                actual_format = img.format or ("GIF" if ext == ".gif" else "PNG")
                is_animated = getattr(img, 'is_animated', False) or n_frames > 1

            is_gif = (ext == ".gif") or (actual_format == "GIF") or is_animated

            # Относительный путь от batch_dir к оригиналу для report.html
            orig_rel = os.path.relpath(fpath, batch_dir).replace('\\', '/')

            # Интеллектуальная обработка GIF с отслеживанием хронометража без перерасхода токенов
            if is_gif:
                gif_res = process_animated_media_smart(fpath, previews_dir, batch_dir, client)
                batch_results.append(gif_res)
                # Инкрементальное обновление отчета
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "batch_idx": batch_idx,
                        "batch_size": batch_size,
                        "total_items": len(batch_results),
                        "results": batch_results,
                        "in_progress": True
                    }, f, ensure_ascii=False, indent=2)
                generate_batch_html_report(batch_idx, batch_results, html_report_path)
                continue

            # Для статичных картинок запускаем полный VLM-пайплайн
            passport = process_single_file(fpath, preview=True, client=client, route_complex_to_review=False)
            detections = passport.get("detections", [])
            has_sensitive = passport.get("has_sensitive_data", False)

            if has_sensitive:
                # Сохраняем превью в папку батча
                batch_preview_path = previews_dir / fpath.name
                apply_blur_to_image(fpath, detections, batch_preview_path)
                preview_rel = os.path.relpath(batch_preview_path, batch_dir).replace('\\', '/')

                batch_results.append({
                    "file_path": str(fpath.relative_to(PROJECT_ROOT)).replace('\\', '/'),
                    "file_name": fpath.name,
                    "status": "blurred",
                    "dimensions": {"width": w, "height": h, "frames": n_frames},
                    "orig_rel_to_batch": orig_rel,
                    "preview_rel_to_batch": preview_rel,
                    "detections": detections
                })
            else:
                batch_results.append({
                    "file_path": str(fpath.relative_to(PROJECT_ROOT)).replace('\\', '/'),
                    "file_name": fpath.name,
                    "status": "clean",
                    "dimensions": {"width": w, "height": h, "frames": n_frames},
                    "orig_rel_to_batch": orig_rel,
                    "preview_rel_to_batch": None,
                    "detections": []
                })
        except Exception as item_err:
            log_server_error(f"Batch {batch_idx:02d} - Item {idx} ({fpath.name})", item_err)
            batch_results.append({
                "file_path": str(fpath.relative_to(PROJECT_ROOT)).replace('\\', '/'),
                "file_name": fpath.name,
                "status": "clean",
                "dimensions": {"width": 0, "height": 0, "frames": 1},
                "orig_rel_to_batch": os.path.relpath(fpath, batch_dir).replace('\\', '/'),
                "preview_rel_to_batch": None,
                "detections": [],
                "error_note": str(item_err)
            })

        # Инкрементальное обновление отчета на дешборде в реальном времени после каждого файла
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "batch_idx": batch_idx,
                "batch_size": batch_size,
                "total_items": len(batch_results),
                "results": batch_results,
                "in_progress": True
            }, f, ensure_ascii=False, indent=2)
        generate_batch_html_report(batch_idx, batch_results, html_report_path)

    # Финальное сохранение завершенного батча
    meta_path = batch_dir / "batch_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "batch_idx": batch_idx,
            "batch_size": batch_size,
            "total_items": len(batch_results),
            "results": batch_results,
            "in_progress": False
        }, f, ensure_ascii=False, indent=2)

    # Генерируем финальный HTML-отчет
    html_report_path = batch_dir / "report.html"
    generate_batch_html_report(batch_idx, batch_results, html_report_path)

    # Инициализируем запись батча в audit_log.json
    audit_data = load_audit_log()
    b_key = str(batch_idx)
    if b_key not in audit_data["batches"]:
        audit_data["batches"][b_key] = {
            "batch_idx": batch_idx,
            "applied": batch_idx in audit_data.get("applied_batches", []),
            "items": {}
        }
        save_audit_log(audit_data)

    print(f"\n========================================================")
    print(f"🎉 Batch {batch_idx:02d} Complete!")
    print(f"   Report: file:///{str(html_report_path).replace('\\', '/')}")
    print(f"========================================================", flush=True)
    return html_report_path


def apply_batch(batch_idx: int) -> int:
    """Применяет утвержденный батч превью к оригинальным файлам вложений с учетом audit_log.json и защитой бэкапа"""
    batch_dir = BATCH_BASE / f"batch_{batch_idx:02d}"
    meta_path = batch_dir / "batch_meta.json"

    if not meta_path.exists():
        print(f"Error: Batch {batch_idx} metadata not found at {meta_path}")
        return 0

    with open(meta_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    audit_data = load_audit_log()
    batch_audit = audit_data.get("batches", {}).get(str(batch_idx), {}).get("items", {})

    applied_count = 0
    backup_dir = BATCH_BASE / f"batch_{batch_idx:02d}" / "originals_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for it in data.get("results", []):
        fname = it.get("file_name")
        orig_path = PROJECT_ROOT / it.get("file_path")
        backup_path = backup_dir / it.get("file_name")

        # Проверяем решения человека из audit_log.json
        human_fb = next((v for v in batch_audit.values() if v.get("file_name") == fname), None)
        if human_fb:
            if human_fb.get("type") == "fp":
                # Если файл ранее был ошибочно заблюрен, а в бэкапе есть чистый оригинал — откатываем к оригиналу!
                if backup_path.exists() and orig_path.exists():
                    shutil.copy2(backup_path, orig_path)
                    print(f"[Rollback Clean] {fname} восстановлен из бэкапа как чистый!")
                else:
                    print(f"[Skip FP] {fname} отмечен как ложное срабатывание — превью не накатывается!")
                continue
            if human_fb.get("is_rejected"):
                print(f"[Skip Rejected] {fname} отмечен замечанием ({human_fb.get('label')}) — ожидает исправления агентом!")
                continue

        if it.get("status") == "clean":
            if backup_path.exists() and orig_path.exists():
                shutil.copy2(backup_path, orig_path)
                print(f"[Rollback Clean] {fname} восстановлен из бэкапа!")
            continue

        if it.get("status") == "blurred":
            preview_path = batch_dir / "previews" / it.get("file_name")
            if preview_path.exists() and orig_path.exists():
                if not backup_path.exists():
                    shutil.copy2(orig_path, backup_path)
                shutil.copy2(preview_path, orig_path)
                print(f"[Applied] {it.get('file_name')} -> {it.get('file_path')}")
                applied_count += 1

    # Фиксируем применение батча в audit_log.json
    audit_data = load_audit_log()
    if batch_idx not in audit_data["applied_batches"]:
        audit_data["applied_batches"].append(batch_idx)
        audit_data["applied_batches"].sort()
    b_key = str(batch_idx)
    if b_key not in audit_data["batches"]:
        audit_data["batches"][b_key] = {"batch_idx": batch_idx, "applied": True, "items": {}}
    else:
        audit_data["batches"][b_key]["applied"] = True
    save_audit_log(audit_data)

    print(f"\nSuccessfully applied {applied_count} sanitized images to attachments.")
    return applied_count


class ReviewDashboardHandler(SimpleHTTPRequestHandler):
    """Микро-бэкенд для раздачи отчетов, оригиналов/превью и синхронизации с audit_log.json"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def log_message(self, format, *args):
        # Тихий логгинг только для API-мутаций
        if self.path.startswith("/api/") and self.command == "POST":
            sys.stdout.write(f"[Dashboard API] {self.command} {self.path}\n")
            sys.stdout.flush()

    def _send_json(self, payload: dict, status: int = 200):
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ["/", "/dashboard"]:
            avail = get_available_batches()
            latest = avail[-1] if avail else 1
            target_url = f"/Data/review_batches/batch_{latest:02d}/report.html"
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()
            return

        if parsed.path == "/api/state":
            qs = parse_qs(parsed.query)
            b_idx = int(qs.get("batch", ["1"])[0])
            audit_data = load_audit_log()
            b_info = audit_data.get("batches", {}).get(str(b_idx), {})
            self._send_json({
                "ok": True,
                "batch_idx": b_idx,
                "batch_items": b_info.get("items", {}),
                "applied_batches": audit_data.get("applied_batches", []),
                "available_batches": get_available_batches(),
                "job_status": _JOB_STATE,
                "server_errors_count": len(audit_data.get("server_errors", []))
            })
            return

        if parsed.path == "/api/job-status":
            self._send_json(_JOB_STATE)
            return

        if parsed.path == "/api/server-errors":
            err_content = ""
            if SERVER_ERROR_LOG_PATH.exists():
                try:
                    with open(SERVER_ERROR_LOG_PATH, "r", encoding="utf-8") as f:
                        err_content = f.read()[-8000:]
                except Exception:
                    pass
            self._send_json({"ok": True, "errors": err_content})
            return

        if parsed.path == "/api/agent-queue":
            self._send_json(load_agent_queue())
            return

        try:
            return super().do_GET()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            body = json.loads(raw_body)
        except Exception:
            body = {}

        if parsed.path == "/api/submit-to-agent":
            b_idx = int(body.get("batch_idx", 1))
            items = body.get("items", [])
            task = enqueue_agent_task(b_idx, items)
            self._send_json({"ok": True, "task": task})
            return

        if parsed.path == "/api/queue-item":
            b_idx = int(body.get("batch_idx", 1))
            i_idx = int(body.get("item_idx", 1))
            fname = body.get("file_name", "")
            fpath = body.get("file_path", "")
            fb_type = body.get("type", "flagged")
            label = body.get("label", "Замечание пользователя")
            note = body.get("note", "")
            dets = body.get("detections", [])

            fb_obj = {
                "item_idx": i_idx,
                "file_name": fname,
                "file_path": fpath,
                "type": fb_type,
                "is_approved": False,
                "is_rejected": True,
                "label": label,
                "note": note,
                "detections": dets
            }
            update_audit_feedback(b_idx, i_idx, fname, fpath, dets, fb_obj)

            task_item = {
                "item_idx": i_idx,
                "file_name": fname,
                "file_path": fpath,
                "type": fb_type,
                "label": label,
                "note": note,
                "detections": dets
            }
            task = enqueue_agent_task(b_idx, [task_item])
            self._send_json({"ok": True, "task_id": task["id"]})
            return

        if parsed.path == "/api/apply-custom-box":
            b_idx = int(body.get("batch_idx", 1))
            i_idx = int(body.get("item_idx", 1))
            fname = body.get("file_name", "")
            box = body.get("box_2d", [0, 0, 0, 0])
            cat = body.get("category", "manual_box")
            reason = body.get("reason", "Пользовательское выделение")
            try:
                updated_item = apply_custom_box_to_item(b_idx, i_idx, fname, box, cat, reason)
            except Exception as e:
                log_server_error(f"apply_custom_box for {fname}", e)
                self._send_json({"ok": False, "error": str(e)}, status=500)
                return

            self._send_json({
                "ok": True,
                "preview_rel": updated_item.get("preview_rel_to_batch"),
                "detections": updated_item.get("detections", [])
            })
            return

        if parsed.path == "/api/undo-custom-box":
            b_idx = int(body.get("batch_idx", 1))
            i_idx = int(body.get("item_idx", 1))
            fname = body.get("file_name", "")
            try:
                updated_item = undo_custom_box_item(b_idx, i_idx, fname)
            except Exception as e:
                log_server_error(f"undo_custom_box for {fname}", e)
                self._send_json({"ok": False, "error": str(e)}, status=500)
                return

            self._send_json({
                "ok": True,
                "status": updated_item.get("status"),
                "preview_rel": updated_item.get("preview_rel_to_batch"),
                "detections": updated_item.get("detections", [])
            })
            return

        if parsed.path == "/api/sync-batch":
            b_idx = int(body.get("batch_idx", 1))
            items_dict = body.get("items", {})
            data = load_audit_log()
            b_key = str(b_idx)
            if b_key not in data["batches"]:
                data["batches"][b_key] = {
                    "batch_idx": b_idx,
                    "applied": b_idx in data.get("applied_batches", []),
                    "items": {}
                }
            data["batches"][b_key]["items"] = items_dict
            save_audit_log(data)
            self._send_json({"ok": True, "synced_count": len(items_dict)})
            return

        if parsed.path == "/api/feedback":
            b_idx = int(body.get("batch_idx", 1))
            i_idx = int(body.get("item_idx", 1))
            fname = body.get("file_name", "")
            fpath = body.get("file_path", "")
            dets = body.get("detections", [])
            fb = body.get("feedback", None)
            update_audit_feedback(b_idx, i_idx, fname, fpath, dets, fb)

            # Реактивное самоисцеление при ложном срабатывании (FP)
            if fb and fb.get("type") == "fp" and fname:
                try:
                    batch_dir = BATCH_BASE / f"batch_{b_idx:02d}"
                    meta_path = batch_dir / "batch_meta.json"
                    if meta_path.exists():
                        with open(meta_path, "r", encoding="utf-8") as f:
                            m_data = json.load(f)
                        for r in m_data.get("results", []):
                            if r.get("file_name") == fname:
                                r["status"] = "clean"
                                r["detections"] = []
                                r["preview_rel_to_batch"] = None
                        with open(meta_path, "w", encoding="utf-8") as f:
                            json.dump(m_data, f, ensure_ascii=False, indent=2)

                    # Удаляем превью с диска
                    prev_p = batch_dir / "previews" / fname
                    if prev_p.exists():
                        prev_p.unlink()

                    # Если боевой файл уже был заменен, откатываем чистый оригинал из бэкапа
                    backup_p = batch_dir / "originals_backup" / fname
                    orig_p = PROJECT_ROOT / fpath if fpath else None
                    if backup_p.exists() and orig_p and orig_p.exists():
                        shutil.copy2(backup_p, orig_p)
                except Exception as e:
                    log_server_error(f"Reactive FP fix for {fname}", e)

            self._send_json({"ok": True})
            return

        if parsed.path == "/api/apply-batch":
            b_idx = int(body.get("batch_idx", 1))
            count = apply_batch(b_idx)
            self._send_json({"ok": True, "batch_idx": b_idx, "applied_count": count})
            return

        if parsed.path == "/api/run-batch":
            if _JOB_STATE.get("running"):
                self._send_json({"ok": False, "error": f"Batch {_JOB_STATE.get('batch_idx')} is already running"}, status=409)
                return

            avail = get_available_batches()
            target_batch = int(body.get("batch_idx", 0))

            # Защита от случайного повторного сканирования уже существующего батча
            if target_batch in avail:
                self._send_json({
                    "ok": True,
                    "already_exists": True,
                    "batch_idx": target_batch,
                    "report_url": f"/Data/review_batches/batch_{target_batch:02d}/report.html"
                })
                return

            default_next = (avail[-1] + 1) if avail else 1
            if target_batch <= 0:
                target_batch = default_next
            batch_size = int(body.get("batch_size", 20))

            _JOB_STATE.update({
                "running": True,
                "batch_idx": target_batch,
                "current": 0,
                "total": batch_size,
                "current_file": "Инициализация...",
                "done": False,
                "error": None,
                "report_url": f"/Data/review_batches/batch_{target_batch:02d}/report.html"
            })

            def _bg_worker():
                try:
                    run_batch(target_batch, batch_size=batch_size)
                    _JOB_STATE["done"] = True
                except Exception as e:
                    log_server_error(f"Batch Worker {target_batch}", e)
                    _JOB_STATE["error"] = str(e)
                finally:
                    _JOB_STATE["running"] = False

            t = threading.Thread(target=_bg_worker, daemon=True)
            t.start()
            self._send_json({
                "ok": True,
                "batch_idx": target_batch,
                "report_url": f"/Data/review_batches/batch_{target_batch:02d}/report.html"
            })
            return

        self._send_json({"ok": False, "error": "Unknown endpoint"}, status=404)


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Запрещает перехват уже занятого порта другим процессом в Windows (SO_REUSEADDR=False)"""
    allow_reuse_address = False


def kill_process_on_port(port: int = 8788):
    """Если порт занят старым зомби-процессом, завершаем его перед стартом сервера"""
    if sys.platform != "win32":
        return
    try:
        cmd = f'(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess'
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
        pids = [int(p.strip()) for p in res.stdout.strip().splitlines() if p.strip().isdigit()]
        for pid in pids:
            if pid > 0 and pid != os.getpid():
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.5)
    except Exception:
        pass


def start_review_server(port: int = 8788, open_browser: bool = True, use_tunnel: bool = False):
    """Запускает интерактивный сервер ревью батчей с опциональным защищенным Cloudflare-туннелем и QR-кодом"""
    load_audit_log()
    regenerate_all_reports()
    avail = get_available_batches()
    latest = avail[-1] if avail else 1
    
    local_ip = get_local_lan_ip()
    local_url = f"http://127.0.0.1:{port}/Data/review_batches/batch_{latest:02d}/report.html"
    mobile_url = f"http://{local_ip}:{port}/Data/review_batches/batch_{latest:02d}/report.html"
    tunnel_url = None
    tunnel_proc = None

    if use_tunnel:
        cf_bin = Path("C:/Program Files (x86)/cloudflared/cloudflared.exe")
        if not cf_bin.exists():
            cf_bin = Path("C:/Program Files/cloudflared/cloudflared.exe")
        if cf_bin.exists():
            print("\n[Tunnel] Initializing secure Cloudflare tunnel for remote mobile access...")
            tunnel_log_path = BATCH_BASE / "tunnel.log"
            try:
                if tunnel_log_path.exists():
                    tunnel_log_path.unlink()
            except Exception:
                pass
            try:
                tunnel_proc = subprocess.Popen(
                    [str(cf_bin), "tunnel", "--protocol", "http2", "--url", f"http://127.0.0.1:{port}", "--logfile", str(tunnel_log_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                start_t = time.time()
                while time.time() - start_t < 20:
                    if tunnel_log_path.exists():
                        try:
                            log_txt = tunnel_log_path.read_text(encoding="utf-8", errors="replace")
                            import re
                            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", log_txt)
                            if m:
                                tunnel_base = m.group(0)
                                tunnel_url = f"{tunnel_base}/Data/review_batches/batch_{latest:02d}/report.html"
                                break
                        except Exception:
                            pass
                    time.sleep(0.5)
            except Exception as e:
                print(f"[Tunnel Warning] Failed to start cloudflared: {e}")

    effective_mobile_url = tunnel_url or mobile_url

    # Генерация QR-кода на мобильный URL
    qr_img_path = BATCH_BASE / "mobile_qr.png"
    generate_qr_image(effective_mobile_url, qr_img_path)

    # Автономный запуск воркера очереди замечаний в процессе сервера
    try:
        try:
            from Scripts.agent_review_worker import start_worker_thread
        except ImportError:
            from agent_review_worker import start_worker_thread
        start_worker_thread()
        worker_info = "Integrated background worker thread active"
    except Exception as worker_err:
        worker_info = f"Warning: could not start worker ({worker_err})"

    try:
        server = ExclusiveThreadingHTTPServer(("0.0.0.0", port), ReviewDashboardHandler)
    except OSError:
        kill_process_on_port(port)
        server = ExclusiveThreadingHTTPServer(("0.0.0.0", port), ReviewDashboardHandler)
    print("========================================================")
    print(f"[Review Dashboard] AxonBot OPSEC Live Review Dashboard running on 0.0.0.0:{port}!")
    print(f"   Local PC:    {local_url}")
    if tunnel_url:
        print(f"   Cloudflare:  {tunnel_url}  (HTTPS Remote / Mobile)")
    else:
        print(f"   Mobile LAN:  {mobile_url}")
    print(f"   Worker:      {worker_info}")
    print(f"   Audit Log:   {AUDIT_LOG_PATH}")
    print(f"   Errors Log:  {SERVER_ERROR_LOG_PATH}")
    print("========================================================")
    print(f"QR-код для быстрого открытия со смартфона/планшета (наведите камеру):")
    print_ascii_qr(effective_mobile_url)
    print("========================================================", flush=True)

    if open_browser:
        try:
            webbrowser.open(local_url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[STOP] Stopping Review Dashboard server.")
        if tunnel_proc:
            try:
                tunnel_proc.terminate()
            except Exception:
                pass
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="AxonBot Media Sanitizer & Precision Blur Engine")
    parser.add_argument("--file", type=str, help="Path to a single image or GIF to process")
    parser.add_argument("--detect-and-blur", action="store_true", help="Run VLM detection and apply blur")
    parser.add_argument("--preview", action="store_true", default=True, help="Save as _preview without overwriting original")
    parser.add_argument("--apply", action="store_true", help="Overwrite original file directly")
    parser.add_argument("--quarantine", action="store_true", help="Directly route file to Data/manual_review_media/pending/ for manual editing")
    parser.add_argument("--import-reviewed", action="store_true", help="Import manually reviewed files from ready/ into attachments")
    parser.add_argument("--run-batch", type=int, help="Run batch processing for a specific batch index (e.g. 1)")
    parser.add_argument("--size", type=int, default=20, help="Batch size (default: 20)")
    parser.add_argument("--apply-batch", type=int, help="Apply reviewed previews for a specific batch index")
    parser.add_argument("--serve", action="store_true", help="Start local interactive Web Dashboard server")
    parser.add_argument("--port", type=int, default=8788, help="Port for --serve (default: 8788)")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open browser when starting --serve")
    parser.add_argument("--read-audit", action="store_true", help="Print live audit_log.json summary for agent")
    parser.add_argument("--regen-reports", action="store_true", help="Regenerate HTML reports for all existing batches")
    parser.add_argument("--tunnel", action="store_true", help="Start secure Cloudflare tunnel for remote mobile access")
    args = parser.parse_args()

    if args.read_audit:
        print_audit_summary()
        return

    if args.regen_reports:
        regenerate_all_reports()
        return

    if args.serve:
        start_review_server(port=args.port, open_browser=not args.no_browser, use_tunnel=args.tunnel)
        return

    if args.import_reviewed:
        import_reviewed_files()
        return

    if args.run_batch is not None:
        run_batch(args.run_batch, batch_size=args.size)
        return

    if args.apply_batch is not None:
        apply_batch(args.apply_batch)
        return

    if args.file:
        target_path = Path(args.file)
        if not target_path.is_absolute():
            target_path = PROJECT_ROOT / target_path
        preview_mode = not args.apply
        process_single_file(target_path, preview=preview_mode, force_quarantine=args.quarantine)
    else:
        parser.print_help()

if __name__ == "__main__":
    setup_crash_reporter("Media_Sanitizer")
    main()
