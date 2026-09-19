import re
import sys
import time
import asyncio
import aiohttp
import pytest
from dataclasses import dataclass, field
from typing import Optional
from Scripts.bot.config import N8N_RAG_URL

# Гарантируем UTF-8 вывод в Windows PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

@dataclass(frozen=True)
class RAGTestCase:
    id: str
    name: str
    question: str
    expected_keywords: list[str]
    expected_media: list[str] = field(default_factory=list)
    expected_source_pattern: Optional[str] = None
    max_latency_sec: float = 20.0

@dataclass
class RAGTestResult:
    case_id: str
    passed: bool
    status_code: int
    latency_sec: float
    response_text: str
    matched_keywords: list[str]
    missing_keywords: list[str]
    found_media: list[str]
    missing_media: list[str]
    source_found: bool
    error_message: Optional[str] = None

TEST_SUITE_KR = [
    RAGTestCase(
        id="TC-KR-01",
        name="Скрытие заходящих разрезов (Типовые этажи)",
        question="У меня на плане типового этажа откуда-то вылез разрез с другого этажа. Если я его удалю, он снесется на остальных листах. Как его по стандарту спрятать только на этом плане?",
        expected_keywords=["Скрыть", "Элемент"],
        expected_media=["7438994.png"],
        expected_source_pattern=r"atlassian\.net|Виды на типовых этажах",
    ),
    RAGTestCase(
        id="TC-KR-02",
        name="Параметр видимости типовых этажей",
        question="Сделал типовой этаж, но на плане пусто — ссылочные виды не подтягиваются. Что я забыл заполнить в свойствах исходного вида?",
        expected_keywords=["ADSK_Примечание", "Типовой этаж"],
        expected_source_pattern=r"atlassian\.net|Виды на типовых этажах",
    ),
    RAGTestCase(
        id="TC-KR-03",
        name="Сортировка по порядковому номеру (Спецификация сборных)",
        question="В спецификации сборных ж/б элементов сейчас выводятся марки, а ГИП требует, чтобы в первой колонке шел сквозной порядковый номер (позиция 1, 2, 3...). Где в шаблоне это настраивается?",
        expected_keywords=["ADSK_Позиция", "Сорт_Позиция"],
        expected_media=["7442449.gif"],
        expected_source_pattern=r"atlassian\.net|Спецификация сборных элементов",
    ),
    RAGTestCase(
        id="TC-KR-04",
        name="Разница спецификаций конструкций и групп",
        question="У меня в шаблоне есть две заготовки: '10_Опл_Спецификация конструкций' и '10_Опл_Спецификация групп'. Какую из них брать под сборные лестничные марши и вентблоки, а какую если элементы собраны в группу?",
        expected_keywords=["10_Опл_Спецификация конструкций", "10_Опл_Спецификация групп"],
        expected_source_pattern=r"atlassian\.net|Спецификация сборных элементов",
    ),
    RAGTestCase(
        id="TC-KR-05",
        name="Рабочие наборы для лестниц и маршей",
        question="Моделирую монолитную лестницу со сборными маршами. В какой рабочий набор закидывать сами марши, а в какой отверстия под них в стенах?",
        expected_keywords=["КР_Лестницы", "КР_Монолит"],
        expected_source_pattern=r"atlassian\.net|Лестницы КЖ",
    ),
    RAGTestCase(
        id="TC-KR-06",
        name="Лайфхак при работе с плагином Citrus (Лестницы)",
        question="Запустил плагин Citrus для армирования лестницы, а он открыл окно и намертво заблокировал Ревит — я даже размеры марша не могу посмотреть. Как по инструкции с ним правильно работать?",
        expected_keywords=["Citrus", "скриншот"],
        expected_media=["3313415.png"],
        expected_source_pattern=r"atlassian\.net|Лестницы КЖ",
    ),
    RAGTestCase(
        id="TC-KR-07",
        name="Семейство лягушек для площадок лестниц",
        question="Каким семейством у нас по стандарту моделировать лягушки (фиксаторы) в лестничных площадках и на какую основу их сажать?",
        expected_keywords=["261_Стержень_499_Ф", "Грани"],
        expected_media=["7438826.gif"],
        expected_source_pattern=r"atlassian\.net|Лестницы КЖ",
    ),
    RAGTestCase(
        id="TC-KR-08",
        name="Подсчет арматуры в погонных метрах",
        question="У меня фоновая арматура плиты в спецификации считается в штуках и бьется по позициям, а заказчик требует общий метраж (в погонных метрах без позиций). Как это переключить?",
        expected_keywords=["!BIM_CORP_Арм_Подсчет", "2_пог.м"],
        expected_source_pattern=r"atlassian\.net|Ключевые параметры арматуры",
    ),
    RAGTestCase(
        id="TC-KR-09",
        name="Ключ арматуры для каркасов продавливания",
        question="Собираю каркас против продавливания плиты перекрытия. Какой ключ арматуры выбрать для наклонных стержней внутри каркаса, чтобы они посчитались как каркас?",
        expected_keywords=["10.2_КР_Продавл", "Каркас"],
        expected_source_pattern=r"atlassian\.net|Ключевые параметры арматуры",
    ),
    RAGTestCase(
        id="TC-KR-10",
        name="Видимость закладного проката на узлах",
        question="Замоделировал закладной уголок в торце стены, сделал выносной узел 1:20, а закладная деталь пропала — сплошной бетон. Что нужно включить в свойствах вида, чтобы прокат стал виден?",
        expected_keywords=["Показать невидимые линии", "Все"],
        expected_media=["13044461.png"],
        expected_source_pattern=r"atlassian\.net|Арматурные выпуски",
    ),
    RAGTestCase(
        id="TC-KR-11",
        name="Формула смещения секущего диапазона узла",
        question="На узле выпуска торчит фоновая арматура стены и забивает чертеж. Как настроить секущий диапазон узла, чтобы убрать сетку стены, но оставить видимым закладной прокат?",
        expected_keywords=["Параметры задней подрезки", "Независимый"],
        expected_media=["13044462.png"],
        expected_source_pattern=r"atlassian\.net|Арматурные выпуски",
    ),
]

def extract_media_from_text(text: str) -> list[str]:
    """Извлекает URL всех медиафайлов из текста ответа (аналогично логике бота)."""
    raw_urls = re.findall(
        r'https?://[^\s<>"\'\)]+\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\s<>"\'\)]*)?',
        text,
        re.IGNORECASE,
    )
    return list(dict.fromkeys(raw_urls))

async def run_single_rag_test(case: RAGTestCase, session_id: Optional[int] = None) -> RAGTestResult:
    """Выполняет один RAG-запрос и проводит многофакторную валидацию."""
    if session_id is None:
        session_id = int(time.time() * 1000) % 900000 + abs(hash(case.id)) % 100000

    payload = {
        "text": case.question,
        "sessionId": session_id,
        "chat_id": session_id,
        "category": "test_kr"
    }

    start_time = time.perf_counter()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(N8N_RAG_URL, json=payload, timeout=aiohttp.ClientTimeout(total=case.max_latency_sec + 5)) as resp:
                latency = round(time.perf_counter() - start_time, 2)
                status_code = resp.status

                if status_code != 200:
                    return RAGTestResult(
                        case_id=case.id,
                        passed=False,
                        status_code=status_code,
                        latency_sec=latency,
                        response_text="",
                        matched_keywords=[],
                        missing_keywords=case.expected_keywords,
                        found_media=[],
                        missing_media=case.expected_media,
                        source_found=False,
                        error_message=f"HTTP Error {status_code}"
                    )

                data = await resp.json()
                output = ""
                if isinstance(data, dict):
                    output = data.get("output") or data.get("text") or data.get("response") or ""
                elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                    output = data[0].get("output") or data[0].get("text") or ""
                elif isinstance(data, str):
                    output = data

                matched_kw = [kw for kw in case.expected_keywords if kw.lower() in output.lower()]
                missing_kw = [kw for kw in case.expected_keywords if kw.lower() not in output.lower()]

                found_media_urls = extract_media_from_text(output)
                found_media_names = [u.split("/")[-1].split("?")[0] for u in found_media_urls]
                
                missing_media = []
                for exp_m in case.expected_media:
                    if not any(exp_m.lower() in fn.lower() for fn in found_media_names):
                        missing_media.append(exp_m)

                source_found = True
                if case.expected_source_pattern:
                    source_found = bool(re.search(case.expected_source_pattern, output, re.IGNORECASE))

                # Проверка галлюцинаций сторонних ссылок
                has_hallucinations = bool(re.search(r'autodesk\.com|example\.com', output, re.IGNORECASE))

                passed = (
                    len(missing_kw) == 0
                    and len(missing_media) == 0
                    and source_found
                    and not has_hallucinations
                    and latency <= case.max_latency_sec
                    and len(output.strip()) > 20
                )

                error_msg = None
                if not passed:
                    reasons = []
                    if missing_kw:
                        reasons.append(f"Пропущены ключевые слова: {missing_kw}")
                    if missing_media:
                        reasons.append(f"Отсутствуют медиа: {missing_media}")
                    if not source_found:
                        reasons.append("Не найдена ссылка на первоисточник")
                    if has_hallucinations:
                        reasons.append("Обнаружены галлюцинации внешних ссылок")
                    if latency > case.max_latency_sec:
                        reasons.append(f"Превышен таймаут: {latency}s > {case.max_latency_sec}s")
                    if len(output.strip()) <= 20:
                        reasons.append("Слишком короткий/пустой ответ")
                    error_msg = "; ".join(reasons)

                return RAGTestResult(
                    case_id=case.id,
                    passed=passed,
                    status_code=status_code,
                    latency_sec=latency,
                    response_text=output,
                    matched_keywords=matched_kw,
                    missing_keywords=missing_kw,
                    found_media=found_media_urls,
                    missing_media=missing_media,
                    source_found=source_found,
                    error_message=error_msg
                )

    except Exception as e:
        latency = round(time.perf_counter() - start_time, 2)
        return RAGTestResult(
            case_id=case.id,
            passed=False,
            status_code=0,
            latency_sec=latency,
            response_text="",
            matched_keywords=[],
            missing_keywords=case.expected_keywords,
            found_media=[],
            missing_media=case.expected_media,
            source_found=False,
            error_message=f"Exception: {str(e)}"
        )

# Pytest интеграция
@pytest.mark.asyncio
@pytest.mark.parametrize("case", TEST_SUITE_KR, ids=[c.id for c in TEST_SUITE_KR])
async def test_rag_case(case: RAGTestCase):
    """Pytest-раннер для каждого тестового вопроса."""
    result = await run_single_rag_test(case)
    assert result.status_code == 200, f"[{case.id}] Неверный статус ответа: {result.status_code}. Ошибка: {result.error_message}"
    assert result.passed, f"[{case.id}] Тест не пройден: {result.error_message}\nОтвет от ИИ:\n{result.response_text}"

# Автономный CLI запуск
async def run_all_cli():
    print("\n" + "=" * 70)
    print("🚀 ЗАПУСК E2E ВЕРИФИКАЦИИ RAG-ПАЙПЛАЙНА (РАЗДЕЛ КР)")
    print(f"🔗 Целевой вебхук n8n: {N8N_RAG_URL}")
    print("=" * 70)

    total = len(TEST_SUITE_KR)
    passed_count = 0

    for idx, case in enumerate(TEST_SUITE_KR, 1):
        print(f"\n[{idx}/{total}] ❓ Тест {case.id}: {case.name}")
        print(f"   Вопрос: \"{case.question}\"")
        
        res = await run_single_rag_test(case, session_id=999000 + idx)
        
        if res.passed:
            passed_count += 1
            print(f"   ✅ СТАТУС: PASS (RTT: {res.latency_sec}s)")
            if res.found_media:
                print(f"   🖼 Медиа: {res.found_media}")
        else:
            print(f"   ❌ СТАТУС: FAIL (RTT: {res.latency_sec}s, HTTP: {res.status_code})")
            print(f"   ⚠️ Причина: {res.error_message}")
            if res.response_text:
                print(f"   📝 Ответ: {res.response_text[:150]}...")

    print("\n" + "=" * 70)
    print(f"📊 ИТОГИ ТЕСТИРОВАНИЯ: {passed_count}/{total} успешно ({round(passed_count/total*100, 1)}%)")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    asyncio.run(run_all_cli())
