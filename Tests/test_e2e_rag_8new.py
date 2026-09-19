import re
import sys
import time
import uuid
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
    expected_keywords_any: list[list[str]] = field(default_factory=list) # группы: в каждой группе хотя бы одно совпадение
    forbidden_keywords: list[str] = field(default_factory=list)
    expected_media: list[str] = field(default_factory=list)
    expected_source_pattern: Optional[str] = None
    is_refusal_test: bool = False
    max_latency_sec: float = 30.0

@dataclass
class RAGTestResult:
    case_id: str
    name: str
    question: str
    passed: bool
    status_code: int
    latency_sec: float
    response_text: str
    matched_groups: list[str]
    missing_groups: list[list[str]]
    forbidden_found: list[str]
    found_media: list[str]
    missing_media: list[str]
    source_found: bool
    error_message: Optional[str] = None

TEST_SUITE_8_NEW = [
    RAGTestCase(
        id="TC-KR-NEW-01",
        name="Scope Box типовых этажей",
        question="Как настроить видимость и подрезку разрезов на планах типовых этажей, чтобы они не засоряли чужие этажи, и какой параметр нужно заполнить у исходного вида?",
        expected_keywords_any=[
            ["ADSK_Примечание"],
            ["Типовой этаж"],
        ],
        expected_source_pattern=r"atlassian\.net|Виды на типовых этажах",
    ),
    RAGTestCase(
        id="TC-KR-NEW-02",
        name="Фильтры спецификаций сборных элементов",
        question="Какие стандартные параметры фильтрации используются в заготовке '10_Опл_Спецификация конструкций' для сборных элементов и какую категорию нужно выставить вместо фундамента?",
        expected_keywords_any=[
            ["Обобщенные модели", "Обобщенная модель"],
            ["Категория", "Фильтр"],
        ],
        expected_source_pattern=r"atlassian\.net|Спецификация сборных элементов",
    ),
    RAGTestCase(
        id="TC-KR-NEW-03",
        name="!BIM_CORP_Арм_Назначение для пилонов/колонн",
        question="Какое значение ключевого параметра '!BIM_CORP_Арм_Назначение' следует назначить для дополнительных вертикальных стержней или конструктивного армирования пилонов и колонн?",
        expected_keywords_any=[
            ["6_Верт_Доп", "7_Конструктивная", "Конструктивная", "Верт_Доп"],
            ["!BIM_CORP_Арм_Назначение", "Арм_Назначение", "назначение"],
        ],
        expected_source_pattern=r"atlassian\.net|Ключевые параметры арматуры",
    ),
    RAGTestCase(
        id="TC-KR-NEW-04",
        name="!BIM_CORP_Арм_Подсчет (погонаж)",
        question="Какой ключ в '!BIM_CORP_Арм_Подсчет' переключает арматуру на погонные метры и какое значение при этом получает целевой параметр 'ADSK_Размер в погонных метрах'?",
        expected_keywords_any=[
            ["2_пог.м", "пог.м"],
            ["!BIM_CORP_Арм_Подсчет", "Размер в погонных метрах", "погонных метрах"],
        ],
        expected_source_pattern=r"atlassian\.net|Ключевые параметры арматуры",
    ),
    RAGTestCase(
        id="TC-KR-NEW-05",
        name="Отверстия в монолитных лестницах (КР_Монолит)",
        question="Каким семейством создавать отверстия в местах заделки монолитных лестничных площадок в стены и в какой рабочий набор их обязательно помещать?",
        expected_keywords_any=[
            ["231_Отверстие", "231_Отверстие_Прямоугольное", "Отверстие"],
            ["КР_Монолит"],
        ],
        expected_source_pattern=r"atlassian\.net|Лестницы КЖ",
    ),
    RAGTestCase(
        id="TC-KR-NEW-06",
        name="Схема раскладки выпусков BCм-1",
        question="Какими семействами 2D элементов (штриховки и условные обозначения таблицы) оформляются зоны и схемы раскладки арматурных выпусков на плане?",
        expected_keywords_any=[
            ["079_Зона выпусков", "079_Зона", "Зона выпусков"],
            ["076_УО_Зоны выпусков", "076_УО", "УО_Зоны выпусков"],
        ],
        expected_source_pattern=r"atlassian\.net|Арматурные выпуски",
    ),
    RAGTestCase(
        id="TC-KR-NEW-07",
        name="Шаблон разрезов лестниц",
        question="Какие шаблоны вида применяются для опалубочных и арматурных разрезов лестниц (включая схему гнутых стержней)?",
        expected_keywords_any=[
            ["40 - Лестницы - Опл - Разрез", "40-Лестницы-Опл-Разрез", "40 - Лестницы", "Лестницы - Опл - Разрез"],
            ["41 - Лестницы - Арм - Разрез", "41- Лестницы - Арм - Разрез", "41-Лестницы-Арм-Разрез", "41 - Лестницы", "Лестницы - Арм - Разрез"],
        ],
        expected_source_pattern=r"atlassian\.net|Лестницы КЖ",
    ),
    RAGTestCase(
        id="TC-KR-NEW-08",
        name="Тест-ловушка на аналитику свайного поля (проверка честного отказа)",
        question="Как в Revit выполнить геотехнический расчет несущей способности свайного поля и выполнить автоматический расчет осадки куста свай по СП 24.13330?",
        expected_keywords_any=[],
        forbidden_keywords=["Revit рассчитывает осадку", "встроенный геотехнический расчет"],
        is_refusal_test=True,
    ),
]

def extract_media_from_text(text: str) -> list[str]:
    raw_urls = re.findall(
        r'https?://[^\s<>"\'\)]+\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\s<>"\'\)]*)?',
        text,
        re.IGNORECASE,
    )
    return list(dict.fromkeys(raw_urls))

async def run_single_rag_test(case: RAGTestCase, session_id: Optional[int] = None) -> RAGTestResult:
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
                        name=case.name,
                        question=case.question,
                        passed=False,
                        status_code=status_code,
                        latency_sec=latency,
                        response_text="",
                        matched_groups=[],
                        missing_groups=case.expected_keywords_any,
                        forbidden_found=[],
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

                matched_groups = []
                missing_groups = []
                for group in case.expected_keywords_any:
                    matched = [kw for kw in group if kw.lower() in output.lower()]
                    if matched:
                        matched_groups.append(f"({' | '.join(matched)})")
                    else:
                        missing_groups.append(group)

                forbidden_found = [f_kw for f_kw in case.forbidden_keywords if f_kw.lower() in output.lower()]

                found_media_urls = extract_media_from_text(output)
                found_media_names = [u.split("/")[-1].split("?")[0] for u in found_media_urls]
                
                missing_media = []
                for exp_m in case.expected_media:
                    if not any(exp_m.lower() in fn.lower() for fn in found_media_names):
                        missing_media.append(exp_m)

                source_found = True
                if case.expected_source_pattern:
                    source_found = bool(re.search(case.expected_source_pattern, output, re.IGNORECASE))

                has_hallucinations = bool(re.search(r"autodesk\.com|example\.com", output, re.IGNORECASE))

                if case.is_refusal_test:
                    refusal_phrases = ["не содержит", "нет информации", "не выполняется", "не предназначена", "отсутствует", "не производит", "не рассчитывает", "сторонних", "расчетных комплексах", "scad", "лира", "не делает", "не позволяет", "нет данных", "базе знаний нет"]
                    is_refused = any(p in output.lower() for p in refusal_phrases)
                    passed = is_refused and len(forbidden_found) == 0 and not has_hallucinations and latency <= case.max_latency_sec
                    reasons = []
                    if not is_refused:
                        reasons.append("Модель не дала четкий отказ на вопрос вне базы знаний")
                    if forbidden_found:
                        reasons.append(f"Обнаружены запрещенные слова: {forbidden_found}")
                    error_msg = "; ".join(reasons) if not passed else None
                else:
                    passed = (
                        len(missing_groups) == 0
                        and len(missing_media) == 0
                        and len(forbidden_found) == 0
                        and source_found
                        and not has_hallucinations
                        and latency <= case.max_latency_sec
                        and len(output.strip()) > 20
                    )

                    reasons = []
                    if missing_groups:
                        reasons.append(f"Пропущены ключевые группы: {missing_groups}")
                    if missing_media:
                        reasons.append(f"Отсутствуют медиа: {missing_media}")
                    if forbidden_found:
                        reasons.append(f"Обнаружены запрещенные слова: {forbidden_found}")
                    if not source_found:
                        reasons.append("Не найдена ссылка на первоисточник")
                    if has_hallucinations:
                        reasons.append("Обнаружены галлюцинации внешних ссылок")
                    if latency > case.max_latency_sec:
                        reasons.append(f"Превышен таймаут: {latency}s > {case.max_latency_sec}s")
                    if len(output.strip()) <= 20:
                        reasons.append("Слишком короткий/пустой ответ")
                    error_msg = "; ".join(reasons) if not passed else None

                return RAGTestResult(
                    case_id=case.id,
                    name=case.name,
                    question=case.question,
                    passed=passed,
                    status_code=status_code,
                    latency_sec=latency,
                    response_text=output,
                    matched_groups=matched_groups,
                    missing_groups=missing_groups,
                    forbidden_found=forbidden_found,
                    found_media=found_media_urls,
                    missing_media=missing_media,
                    source_found=source_found,
                    error_message=error_msg
                )

    except Exception as e:
        latency = round(time.perf_counter() - start_time, 2)
        return RAGTestResult(
            case_id=case.id,
            name=case.name,
            question=case.question,
            passed=False,
            status_code=0,
            latency_sec=latency,
            response_text="",
            matched_groups=[],
            missing_groups=case.expected_keywords_any,
            forbidden_found=[],
            found_media=[],
            missing_media=case.expected_media,
            source_found=False,
            error_message=f"Exception: {str(e)}"
        )

@pytest.mark.asyncio
@pytest.mark.parametrize("case", TEST_SUITE_8_NEW, ids=[c.id for c in TEST_SUITE_8_NEW])
async def test_rag_case_new(case: RAGTestCase):
    result = await run_single_rag_test(case)
    assert result.status_code == 200, f"[{case.id}] Неверный статус ответа: {result.status_code}. Ошибка: {result.error_message}"
    assert result.passed, f"[{case.id}] Тест не пройден: {result.error_message}\nОтвет от ИИ:\n{result.response_text}"

async def run_all_cli():
    print("\n" + "=" * 70)
    print("🚀 ЗАПУСК ДОПОЛНИТЕЛЬНОГО ТЕСТОВОГО НАБОРА (8 НОВЫХ ВОПРОСОВ)")
    print(f"🔗 Целевой вебхук n8n: {N8N_RAG_URL}")
    print("=" * 70)

    total = len(TEST_SUITE_8_NEW)
    passed_count = 0

    results = []
    for idx, case in enumerate(TEST_SUITE_8_NEW, 1):
        print(f"\n[{idx}/{total}] ❓ Тест {case.id}: {case.name}")
        print(f"   Вопрос: \"{case.question}\"")
        
        res = await run_single_rag_test(case, session_id=int(time.time()*1000) % 900000 + idx*100)
        results.append(res)
        
        if res.passed:
            passed_count += 1
            print(f"   ✅ СТАТУС: PASS (RTT: {res.latency_sec}s)")
            if res.found_media:
                print(f"   🖼 Медиа: {res.found_media}")
        else:
            print(f"   ❌ СТАТУС: FAIL (RTT: {res.latency_sec}s, HTTP: {res.status_code})")
            print(f"   ⚠️ Причина: {res.error_message}")
            if res.response_text:
                print(f"   📝 Ответ: {res.response_text[:200]}...")

    print("\n" + "=" * 70)
    print(f"📊 ИТОГИ ТЕСТИРОВАНИЯ: {passed_count}/{total} успешно ({round(passed_count/total*100, 1)}%)")
    print("=" * 70 + "\n")
    return results

if __name__ == "__main__":
    asyncio.run(run_all_cli())
