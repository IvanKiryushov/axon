import re
import sys
import time
import uuid
import asyncio
import aiohttp
import pytest
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Scripts.bot.config import N8N_RAG_URL
from Scripts.bot.utils.query_preprocessor import preprocess_query
from Tests.rag_evaluator import RAGEvaluator, JudgeVerdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Attachment Integrity Cache
MEDIA_CACHE_PATH = PROJECT_ROOT / "Data" / "media_captions_cache.json"
VALID_MEDIA_FILENAMES: set[str] = set()
if MEDIA_CACHE_PATH.exists():
    try:
        with open(MEDIA_CACHE_PATH, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
            VALID_MEDIA_FILENAMES = {k.lower() for k in cache_data.get("by_filename", {}).keys()}
    except Exception as e:
        print(f"Warning: Failed to load media captions cache: {e}")

EVALUATOR = RAGEvaluator(model="gemini-3.5-flash-lite")
DATASET_PATH = PROJECT_ROOT / "Data" / "test_bench_kr_dataset.json"

@dataclass(frozen=True)
class RAGTestCase:
    id: str
    name: str
    question: str
    expected_keywords_any: list[list[str]] = field(default_factory=list)
    forbidden_keywords: list[str] = field(default_factory=list)
    expected_media: bool = False
    allowed_page_ids: list[int] = field(default_factory=list)
    expected_mask_regex: Optional[str] = None
    expected_source_pattern: Optional[str] = None
    expected_rubric: Optional[str] = None
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
    hallucinated_media: list[str]
    found_page_ids: list[int]
    page_id_valid: bool
    mask_matched: bool
    source_found: bool
    judge_verdict: Optional[JudgeVerdict] = None
    # Градуальные критерии оценки (0..100)
    retrieval_score: float = 0.0
    factual_score: float = 0.0
    media_score: float = 0.0
    rejection_score: float = 0.0
    composite_score: float = 0.0
    grade: str = "🔴 FAIL"
    breakdown_notes: list[str] = field(default_factory=list)
    error_message: Optional[str] = None

def load_test_suite() -> list[RAGTestCase]:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Тестовый датасет не найден: {DATASET_PATH}")
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        raw_items = json.load(f)
    cases = []
    for item in raw_items:
        cases.append(
            RAGTestCase(
                id=item["id"],
                name=item["name"],
                question=item["question"],
                expected_keywords_any=item.get("expected_keywords_any", []),
                forbidden_keywords=item.get("forbidden_keywords", []),
                expected_media=item.get("expected_media", False),
                allowed_page_ids=item.get("allowed_page_ids", []),
                expected_mask_regex=item.get("expected_mask_regex"),
                expected_source_pattern=item.get("expected_source_pattern"),
                expected_rubric=item.get("expected_rubric"),
                is_refusal_test=item.get("is_refusal_test", False),
                max_latency_sec=item.get("max_latency_sec", 30.0),
            )
        )
    return cases

TEST_SUITE_40_STRESS = load_test_suite()

async def run_single_test(case: RAGTestCase) -> RAGTestResult:
    session_uid = f"bench_{uuid.uuid4().hex[:12]}"
    normalized_question = preprocess_query(case.question)
    payload = {
        "text": normalized_question,
        "sessionId": session_uid,
        "chatId": session_uid,
        "chat_id": session_uid,
    }

    t0 = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                N8N_RAG_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=case.max_latency_sec),
            ) as resp:
                latency = round(time.perf_counter() - t0, 2)
                status_code = resp.status
                if status_code != 200:
                    raw_text = await resp.text()
                    return RAGTestResult(
                        case_id=case.id,
                        name=case.name,
                        question=case.question,
                        passed=False,
                        status_code=status_code,
                        latency_sec=latency,
                        response_text=raw_text,
                        matched_groups=[],
                        missing_groups=case.expected_keywords_any,
                        forbidden_found=[],
                        found_media=[],
                        hallucinated_media=[],
                        found_page_ids=[],
                        page_id_valid=False,
                        mask_matched=False,
                        source_found=False,
                        error_message=f"HTTP Status {status_code}",
                    )
                data = await resp.json()
                raw_output = data.get("output", "")
                if isinstance(raw_output, dict):
                    response_text = raw_output.get("text", str(raw_output))
                else:
                    response_text = str(raw_output)
        except asyncio.TimeoutError:
            latency = round(time.perf_counter() - t0, 2)
            return RAGTestResult(
                case_id=case.id,
                name=case.name,
                question=case.question,
                passed=False,
                status_code=408,
                latency_sec=latency,
                response_text="",
                matched_groups=[],
                missing_groups=case.expected_keywords_any,
                forbidden_found=[],
                found_media=[],
                hallucinated_media=[],
                found_page_ids=[],
                page_id_valid=False,
                mask_matched=False,
                source_found=False,
                error_message=f"Timeout ({case.max_latency_sec}s)",
            )
        except Exception as e:
            latency = round(time.perf_counter() - t0, 2)
            return RAGTestResult(
                case_id=case.id,
                name=case.name,
                question=case.question,
                passed=False,
                status_code=500,
                latency_sec=latency,
                response_text="",
                matched_groups=[],
                missing_groups=case.expected_keywords_any,
                forbidden_found=[],
                found_media=[],
                hallucinated_media=[],
                found_page_ids=[],
                page_id_valid=False,
                mask_matched=False,
                source_found=False,
                error_message=str(e),
            )

    # 1. Парсинг медиа-вложений (поддержка формата бота [Изображение/Анимация] и стандартного markdown ![alt](url))
    found_media = re.findall(r"(?:!\[[^\]]*\]|\[(?:Изображение|Анимация):[^\]]*\])\((https?://[^\)]+)\)", response_text, re.IGNORECASE)
    tag_filenames = re.findall(r"\[(?:Изображение|Анимация):\s*([a-zA-Z0-9_\-\.]+\.(?:png|jpg|jpeg|gif))", response_text, re.IGNORECASE)
    url_filenames = [u.split("/")[-1].split("?")[0] for u in found_media]
    all_referenced_media = set(f.strip() for f in (tag_filenames + url_filenames) if f.strip())

    hallucinated_media = [
        f for f in all_referenced_media
        if VALID_MEDIA_FILENAMES and f.lower() not in VALID_MEDIA_FILENAMES
    ]

    # 2. Парсинг Page IDs Confluence
    found_page_ids = [int(pid) for pid in re.findall(r"pages/(\d+)", response_text)]
    if not found_page_ids:
        found_page_ids = [int(pid) for pid in re.findall(r"attachments/(\d+)", response_text)]

    resp_lower = response_text.lower()
    breakdown_notes = []

    # 3. Оценка отказных тестов (Out-of-Scope)
    if case.is_refusal_test:
        refusal_markers = [
            "нет информации",
            "не относится к",
            "вне компетенции",
            "обратитесь к bim-координатору",
            "не содержит",
            "только по разделу кр",
            "не найдено",
            "не входит в регламент",
        ]
        has_refusal = any(m in resp_lower for m in refusal_markers)
        has_source = "📌" in response_text or "atlassian.net" in response_text or len(found_page_ids) > 0
        has_media = len(found_media) > 0 or len(all_referenced_media) > 0

        # Скоринг отказа
        rejection_score = 100.0 if has_refusal else 0.0
        retrieval_score = 100.0 if not has_source else 0.0
        media_score = 100.0 if (not has_media and len(hallucinated_media) == 0) else 0.0
        factual_score = 100.0 if has_refusal else 0.0

        if not has_refusal:
            breakdown_notes.append("Не выдан регламентный отказ на Out-of-Scope вопрос")
        if has_source:
            breakdown_notes.append(f"Ложный источник при отказе (IDs: {found_page_ids})")
        if has_media:
            breakdown_notes.append(f"Ложные медиа при отказе: {list(all_referenced_media)}")

        composite_score = round(0.50 * rejection_score + 0.30 * retrieval_score + 0.20 * media_score, 1)
        passed = composite_score >= 80.0

        grade = "🟢 EXCELLENT" if composite_score >= 90 else "🟡 GOOD" if composite_score >= 70 else "🟠 PARTIAL" if composite_score >= 40 else "🔴 FAIL"

        return RAGTestResult(
            case_id=case.id,
            name=case.name,
            question=case.question,
            passed=passed,
            status_code=status_code,
            latency_sec=latency,
            response_text=response_text,
            matched_groups=["Отказ зафиксирован"] if has_refusal else [],
            missing_groups=[["Регламентный отказ"]] if not has_refusal else [],
            forbidden_found=[],
            found_media=found_media,
            hallucinated_media=hallucinated_media,
            found_page_ids=found_page_ids,
            page_id_valid=(len(found_page_ids) == 0),
            mask_matched=True,
            source_found=not has_source,
            judge_verdict=None,
            retrieval_score=retrieval_score,
            factual_score=factual_score,
            media_score=media_score,
            rejection_score=rejection_score,
            composite_score=composite_score,
            grade=grade,
            breakdown_notes=breakdown_notes,
            error_message="; ".join(breakdown_notes) if not passed else None,
        )

    # 4. Оценка знаниевых тестов (Regular Knowledge Case)
    matched_groups = []
    missing_groups = []
    for grp in case.expected_keywords_any:
        matched_kw = [kw for kw in grp if kw.lower() in resp_lower]
        if matched_kw:
            matched_groups.append(matched_kw[0])
        else:
            missing_groups.append(grp)

    forbidden_found = [kw for kw in case.forbidden_keywords if kw.lower() in resp_lower]
    if hallucinated_media:
        forbidden_found.extend([f"HALLUCINATED_MEDIA: {f}" for f in hallucinated_media])

    # 4.1. Retrieval & Source Scoring
    source_found = True
    if case.expected_source_pattern:
        source_found = bool(re.search(case.expected_source_pattern, response_text, re.IGNORECASE))

    page_id_valid = True
    if case.allowed_page_ids:
        page_id_valid = bool(found_page_ids) and any(pid in case.allowed_page_ids for pid in found_page_ids)

    if page_id_valid and source_found:
        retrieval_score = 100.0
    elif page_id_valid and not source_found:
        retrieval_score = 70.0
        breakdown_notes.append("Page ID совпал, но имя статьи в источнике не сматчилось")
    elif not page_id_valid and found_page_ids:
        retrieval_score = 40.0
        breakdown_notes.append(f"Возвращен сторонний Page ID: {found_page_ids} (ожидались: {case.allowed_page_ids})")
    else:
        retrieval_score = 0.0
        breakdown_notes.append("Ссылка на регламент Confluence не найдена")

    # 4.2. Media Scoring
    if hallucinated_media:
        media_score = 0.0
        breakdown_notes.append(f"Обнаружены галлюцинации файлов медиа: {hallucinated_media}")
    elif case.expected_media:
        if len(found_media) > 0:
            media_score = 100.0
        else:
            media_score = 0.0
            breakdown_notes.append("Ожидались иллюстрации/анимации, но бот их не вернул")
    else:
        media_score = 100.0

    # 4.3. Mask Regex Check
    mask_matched = True
    if case.expected_mask_regex:
        mask_matched = bool(re.search(case.expected_mask_regex, response_text, re.IGNORECASE))
        if not mask_matched:
            breakdown_notes.append(f"Маска именования не совпала с regex: {case.expected_mask_regex}")

    # 4.4. Factual / Engineering Scoring (LLM-as-a-Judge)
    judge_verdict = None
    if EVALUATOR.is_available and case.expected_rubric:
        judge_verdict = await EVALUATOR.evaluate(
            question=case.question,
            response_text=response_text,
            expected_rubric=case.expected_rubric,
            is_refusal_test=case.is_refusal_test,
        )
        if judge_verdict.is_correct is True:
            factual_score = 100.0 if judge_verdict.confidence >= 0.8 else 85.0
        elif judge_verdict.is_correct is False:
            if "нет информации" in resp_lower or "обратитесь к bim-координатору" in resp_lower:
                factual_score = 0.0
                breakdown_notes.append(f"Ложный регламентный отказ: {judge_verdict.verdict_reason}")
            else:
                factual_score = 45.0
                breakdown_notes.append(f"Инженерное замечание: {judge_verdict.verdict_reason}")
        else:
            kw_ratio = len(matched_groups) / max(1, len(case.expected_keywords_any))
            factual_score = round(kw_ratio * 100, 1)
    else:
        kw_ratio = len(matched_groups) / max(1, len(case.expected_keywords_any))
        factual_score = round(kw_ratio * 100, 1)

    if not mask_matched:
        factual_score = min(factual_score, 50.0)

    # 4.5. Composite Score Calculation
    latency_score = 100.0 if latency <= 10.0 else max(0.0, 100.0 - (latency - 10.0) * 5)
    composite_score = round(
        0.45 * factual_score + 0.35 * retrieval_score + 0.15 * media_score + 0.05 * latency_score,
        1
    )

    passed = (composite_score >= 70.0) and (retrieval_score >= 40.0) and (factual_score >= 40.0)

    grade = (
        "🟢 EXCELLENT" if composite_score >= 90.0
        else "🟡 GOOD" if composite_score >= 70.0
        else "🟠 PARTIAL" if composite_score >= 40.0
        else "🔴 FAIL"
    )

    return RAGTestResult(
        case_id=case.id,
        name=case.name,
        question=case.question,
        passed=passed,
        status_code=status_code,
        latency_sec=latency,
        response_text=response_text,
        matched_groups=matched_groups,
        missing_groups=missing_groups,
        forbidden_found=forbidden_found,
        found_media=found_media,
        hallucinated_media=hallucinated_media,
        found_page_ids=found_page_ids,
        page_id_valid=page_id_valid,
        mask_matched=mask_matched,
        source_found=source_found,
        judge_verdict=judge_verdict,
        retrieval_score=retrieval_score,
        factual_score=factual_score,
        media_score=media_score,
        rejection_score=100.0,
        composite_score=composite_score,
        grade=grade,
        breakdown_notes=breakdown_notes,
        error_message="; ".join(breakdown_notes) if not passed else None,
    )

@pytest.mark.asyncio
@pytest.mark.parametrize("case", TEST_SUITE_40_STRESS, ids=lambda c: f"{c.id}_{c.name[:25]}")
async def test_rag_e2e_case(case: RAGTestCase):
    result = await run_single_test(case)
    assert result.passed, (
        f"[{result.case_id}] {result.name} {result.grade} (Score: {result.composite_score})\n"
        f"Retrieval: {result.retrieval_score}%, Factual: {result.factual_score}%, Media: {result.media_score}%\n"
        f"Notes: {result.breakdown_notes}\n"
        f"Response:\n{result.response_text[:300]}..."
    )

async def main():
    suite = load_test_suite()
    total = len(suite)

    print("=" * 85)
    print(f"🚀 СТАРТ КОМПЛЕКСНОГО СТРЕСС-ТЕСТИРОВАНИЯ E2E RAG ({total} ТЕСТОВ)")
    print(f"Датасет: {DATASET_PATH.relative_to(PROJECT_ROOT)}")
    print(f"URL стенда: {N8N_RAG_URL}")
    print(f"LLM-as-a-Judge: {'🟢 Включен (Pool: gemini-3.5-flash-lite + gemini-3.1-flash-lite)' if EVALUATOR.is_available else '🟡 Отключен (локальный фоллбэк)'}")
    print("Параллельность: 2 одновременных запроса с адаптивным пейсингом (RPM safety)")
    print("=" * 85)

    sem = asyncio.Semaphore(2)

    async def sem_task(c: RAGTestCase, idx: int):
        async with sem:
            print(f"[{idx:02d}/{total:02d}] Запуск: {c.id} — {c.name}...")
            res = await run_single_test(c)
            media_info = f"Медиа: {len(res.found_media)}"
            if res.hallucinated_media:
                media_info += f" ⚠️ ГАЛЛЮЦИНАЦИЯ: {res.hallucinated_media}"
            print(f"[{idx:02d}/{total:02d}] {res.grade} | {res.case_id} | Score: {res.composite_score}% | {res.latency_sec}s | {media_info} | Confluence ID: {res.found_page_ids}")
            if res.breakdown_notes:
                print(f"     Нюансы: {'; '.join(res.breakdown_notes)}")
            # Пейсинг для безопасного удержания в рамках 15 RPM
            await asyncio.sleep(2.5)
            return res

    tasks = [sem_task(case, i) for i, case in enumerate(suite, 1)]
    results = await asyncio.gather(*tasks)

    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    pass_rate = round((passed / total) * 100, 1)
    avg_composite = round(sum(r.composite_score for r in results) / total, 1)
    avg_retrieval = round(sum(r.retrieval_score for r in results) / total, 1)
    avg_factual = round(sum(r.factual_score for r in results) / total, 1)
    avg_media = round(sum(r.media_score for r in results) / total, 1)
    avg_latency = round(sum(r.latency_sec for r in results) / total, 2)
    total_media_sent = sum(len(r.found_media) for r in results)
    cases_with_media = sum(1 for r in results if len(r.found_media) > 0)

    # Подсчет грейдов
    excellent_cnt = sum(1 for r in results if r.composite_score >= 90.0)
    good_cnt = sum(1 for r in results if 70.0 <= r.composite_score < 90.0)
    partial_cnt = sum(1 for r in results if 40.0 <= r.composite_score < 70.0)
    fail_cnt = sum(1 for r in results if r.composite_score < 40.0)

    print("\n" + "=" * 85)
    print(f"📊 ИТОГОВЫЙ МНОГОКРИТЕРИАЛЬНЫЙ ОТЧЕТ БЕНЧМАРКА ({total} ТЕСТОВ)")
    print("=" * 85)
    print(f"Всего тестов в реестре:         {total}")
    print(f"Успешно (Score >= 70%):         {passed} / {total} ({pass_rate}%)")
    print(f"Средний Composite Score:        {avg_composite}%")
    print("-" * 85)
    print(f"🔍 Retrieval & Grounding:       {avg_retrieval}%")
    print(f"🧠 Engineering / Factual Acc:   {avg_factual}%")
    print(f"🖼 Media Delivery & Integrity:  {avg_media}%")
    print(f"⏱ Среднее время ответа:         {avg_latency} сек")
    print(f"📦 Всего доставлено медиа:      {total_media_sent} вложений в {cases_with_media} тестах")
    print("-" * 85)
    print("Распределение по грейдам качества:")
    print(f"  🟢 EXCELLENT (90-100%):       {excellent_cnt} ({round(excellent_cnt/total*100, 1)}%)")
    print(f"  🟡 GOOD (70-89%):             {good_cnt} ({round(good_cnt/total*100, 1)}%)")
    print(f"  🟠 PARTIAL (40-69%):          {partial_cnt} ({round(partial_cnt/total*100, 1)}%)")
    print(f"  🔴 FAIL (0-39%):              {fail_cnt} ({round(fail_cnt/total*100, 1)}%)")
    print("=" * 85)

    report_file = PROJECT_ROOT / "Research" / "test_e2e_40stress_run_log.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(f"# Полный отчет многокритериального бенчмарка E2E RAG ({total} тестов)\n\n")
        f.write(f"- **Дата запуска**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **Всего тестов**: {total}\n")
        f.write(f"- **Успешных (Score $\\ge$ 70%)**: {passed} / {total} ({pass_rate}%)\n")
        f.write(f"- **Средний Composite Score**: **{avg_composite}%**\n")
        f.write(f"- **Retrieval & Grounding Score**: {avg_retrieval}%\n")
        f.write(f"- **Factual & Engineering Accuracy**: {avg_factual}%\n")
        f.write(f"- **Media Integrity Score**: {avg_media}%\n")
        f.write(f"- **Средняя задержка**: {avg_latency} сек\n")
        f.write(f"- **Доставлено медиа**: {total_media_sent} шт. в {cases_with_media} тестах\n")
        f.write(f"- **Грейды качества**: 🟢 {excellent_cnt} | 🟡 {good_cnt} | 🟠 {partial_cnt} | 🔴 {fail_cnt}\n\n")
        f.write("---\n\n")
        f.write("## 📝 Протоколы тестирования каждого кейса\n\n")

        for idx, r in enumerate(results, 1):
            f.write(f"### [{idx:02d}/{total:02d}] {r.case_id}: {r.name} — {r.grade} ({r.composite_score}%)\n\n")
            f.write(f"- **Вопрос пользователя**: *«{r.question}»*\n")
            f.write(f"- **Метрики кейса**: Composite: **{r.composite_score}%** | Retrieval: {r.retrieval_score}% | Factual: {r.factual_score}% | Media: {r.media_score}%\n")
            f.write(f"- **Время ответа**: `{r.latency_sec} сек` (HTTP {r.status_code})\n")
            f.write(f"- **Медиа-вложения**: {len(r.found_media)} шт. {r.found_media}\n")
            if r.hallucinated_media:
                f.write(f"- **⚠️ Галлюцинации медиа (HALLUCINATED_MEDIA)**: `{r.hallucinated_media}`\n")
            f.write(f"- **Числовые Page ID Confluence**: `{r.found_page_ids}` (Валидность: {'✅' if r.page_id_valid else '❌'})\n")
            f.write(f"- **Ссылка на источник**: {'✅ Найдена' if r.source_found else '❌ Отсутствует'}\n")
            if r.judge_verdict:
                f.write(f"- **⚖️ Вердикт арбитра (LLM-as-a-Judge)**: `{'✅ Верно' if r.judge_verdict.is_correct else '❌ Неверно'}` (Уверенность: {r.judge_verdict.confidence})\n")
                f.write(f"- **Обоснование арбитра**: *{r.judge_verdict.verdict_reason}*\n")
            if r.breakdown_notes:
                f.write(f"- **Нюансы / Диагностика**: `{r.breakdown_notes}`\n")
            f.write("\n**Полный текст ответа бота:**\n\n")
            f.write(f"> {r.response_text.replace(chr(10), chr(10) + '> ')}\n\n")
            f.write("---\n\n")

    print(f"\n📁 Детальный отчет с матрицей оценок сохранен в: {report_file}")

if __name__ == "__main__":
    asyncio.run(main())
