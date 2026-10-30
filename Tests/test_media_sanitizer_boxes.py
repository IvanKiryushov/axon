import pytest
from pathlib import Path
from PIL import Image
import numpy as np

from Scripts.media_sanitizer import (
    refine_box_by_local_profiling,
    shrink_box_to_prefix,
    merge_and_pad_boxes
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_refine_box_height_guarantee():
    """Проверка гарантии минимальной рабочей высоты бокса (18-22px)."""
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    # Входной бокс высотой всего 8 пикселей
    thin_box = [10, 50, 60, 58]
    refined = refine_box_by_local_profiling(img, thin_box, text_snippet="test", category="generic")
    x1, y1, x2, y2 = refined
    h = y2 - y1
    assert h >= 20, f"Expected height >= 20px, got {h}px"


def test_refine_box_shrink_wide_prefix():
    """Проверка схлопывания раздутого бокса со свойством DOGMA_."""
    # Создаем изображение с префиксом DOGMA_ и суффиксом МаркаВложенногоИзделия
    w, h = 600, 100
    img = Image.new("RGB", (w, h), color=(255, 255, 255))
    arr = np.array(img)
    # Имитируем темный текст строки (высота 14px, строки 40..54)
    # DOGMA_ от x=50 до x=110 (длина 60px)
    arr[40:54, 50:110] = 30
    # Пробел между префиксом и суффиксом (x=110..116)
    # Суффикс от x=116 до x=320 (длина 204px)
    arr[40:54, 116:320] = 30
    synth_img = Image.fromarray(arr)

    # Имитируем грубую VLM-детекцию на всю строку (ширина 270px)
    wide_box = [50, 38, 320, 56]
    refined = refine_box_by_local_profiling(
        synth_img,
        wide_box,
        text_snippet="DOGMA_МаркаВложенногоИзделия",
        category="properties_branded_param"
    )
    rx1, ry1, rx2, ry2 = refined
    ref_w = rx2 - rx1
    ref_h = ry2 - ry1
    aspect = ref_w / (ref_h or 1)

    assert rx1 == 50
    # Бокс должен схлопнуться в зоне пробела перед суффиксом (x=110..118)
    assert 105 <= rx2 <= 122, f"Expected rx2 to cut near prefix boundary (~110-120), got {rx2}"
    assert aspect < 4.0, f"Expected aspect ratio < 4.0, got {aspect}"


def test_shrink_box_to_prefix_normalized():
    """Проверка нормализованной обертки shrink_box_to_prefix."""
    orig_path = PROJECT_ROOT / "Data" / "review_batches" / "batch_09" / "originals_backup" / "378154.png"
    if not orig_path.exists():
        pytest.skip("Test image 378154.png not found")

    img = Image.open(orig_path)
    w, h = img.size

    # Искусственно раздутый бокс (нормализованный)
    # xmin=499 (~540px), xmax=800 (~866px, захватив параметр)
    wide_norm_box = [712, 499, 748, 800]
    shrunk = shrink_box_to_prefix(
        box_norm=wide_norm_box,
        text_snippet="DOGMA_МаркаВложенногоИзделия",
        category="properties_branded_param",
        img=img,
        img_w=w,
        img_h=h
    )

    ymin, xmin, ymax, xmax = shrunk
    px_w = int((xmax - xmin) * w / 1000)
    px_h = int((ymax - ymin) * h / 1000)
    aspect = px_w / (px_h or 1)

    assert px_w < 85, f"Expected shrunk width < 85px, got {px_w}px"
    assert px_h >= 18, f"Expected height >= 18px, got {px_h}px"
    assert aspect <= 3.6, f"Expected aspect ratio <= 3.6, got {aspect}"


def test_merge_and_pad_boxes_row_isolation():
    """Проверка изоляции строк: строки с разрывом (> 15px) никогда не объединяются."""
    img_w, img_h = 1000, 1000

    # Две строки параметров с разрывом между ними (зазор 25px > 12px)
    # Строка 1: y=200..220
    # Строка 2: y=245..265
    box1 = {
        "box_2d": [200, 300, 220, 360],
        "category": "properties_branded_param",
        "text_snippet": "DOGMA_Param1"
    }
    box2 = {
        "box_2d": [245, 300, 265, 360],
        "category": "properties_branded_param",
        "text_snippet": "DOGMA_Param2"
    }

    merged = merge_and_pad_boxes([box1, box2], img_w, img_h)
    assert len(merged) == 2, f"Expected 2 separate rows, but got {len(merged)}"


def test_merge_and_pad_boxes_column_prefix_merge():
    """Проверка объединения префиксов, идущих подряд в колонку (зазор <= 12px)."""
    img_w, img_h = 1000, 1000

    # Две строки префиксов подряд в одной колонке: x=300..360 (w=60)
    # Строка 1: y=200..220
    # Строка 2: y=223..243 (зазор 3px)
    box1 = {
        "box_2d": [200, 300, 220, 360],
        "category": "properties_branded_param",
        "text_snippet": "DOGMA_"
    }
    box2 = {
        "box_2d": [223, 300, 243, 360],
        "category": "properties_branded_param",
        "text_snippet": "DOGMA_"
    }

    merged = merge_and_pad_boxes([box1, box2], img_w, img_h)
    assert len(merged) == 1, f"Expected 1 unified column box, got {len(merged)}"
    y1, x1, y2, x2 = merged[0]["box_2d"]
    assert y1 <= 200 and y2 >= 243
    assert x1 == 300 and x2 == 360


def test_merge_and_pad_boxes_same_row_merge():
    """Проверка слияния фрагментов внутри одной и той же строки таблицы."""
    img_w, img_h = 1000, 1000

    # Два фрагмента внутри одной строки: y=200..220, x1=100..150 и x2=145..200
    box1 = {
        "box_2d": [200, 100, 220, 150],
        "category": "username",
        "text_snippet": "maltseva"
    }
    box2 = {
        "box_2d": [200, 145, 220, 200],
        "category": "username",
        "text_snippet": "_e2"
    }

    merged = merge_and_pad_boxes([box1, box2], img_w, img_h)
    assert len(merged) == 1, f"Expected 1 merged box in single row, got {len(merged)}"
    res_box = merged[0]["box_2d"]
    assert res_box[1] <= 100 and res_box[3] >= 200


@pytest.mark.parametrize("batch_id,file_name,wide_box,snippet", [
    ("batch_09", "378154.png", [712, 499, 748, 800], "DOGMA_МаркаВложенногоИзделия"),
    ("batch_09", "378628.png", [770, 470, 844, 850], "DOGMA_ДиапазонПозиций"),
    ("batch_10", "3312998.png", [355, 487, 400, 600], "DOGMA_"),
    ("batch_10", "7444481.png", [242, 691, 272, 900], "DOGMA_КлассБетона"),
    ("batch_11", "378579.png", [338, 539, 361, 850], "DOGMA_Параметр1"),
])
def test_validation_on_problem_screenshots(batch_id, file_name, wide_box, snippet):
    """
    Валидация гибридного контура на 5 эталонных проблемных скриншотах:
    префиксы сжимаются до длины префикса, высота сохраняется >= 18px.
    """
    orig_path = PROJECT_ROOT / "Data" / "review_batches" / batch_id / "originals_backup" / file_name
    if not orig_path.exists():
        pytest.skip(f"Test image {file_name} not found")

    img = Image.open(orig_path)
    w, h = img.size

    shrunk = shrink_box_to_prefix(
        box_norm=wide_box,
        text_snippet=snippet,
        category="properties_branded_param",
        img=img,
        img_w=w,
        img_h=h
    )

    ymin, xmin, ymax, xmax = shrunk
    px_w = int((xmax - xmin) * w / 1000)
    px_h = int((ymax - ymin) * h / 1000)
    aspect = px_w / (px_h or 1)

    assert px_w < 110, f"File {file_name}: Expected prefix width < 110px, got {px_w}px"
    assert px_h >= 18, f"File {file_name}: Expected height >= 18px, got {px_h}px"
    assert aspect <= 3.8, f"File {file_name}: Expected aspect ratio <= 3.8, got {aspect:.2f}"
