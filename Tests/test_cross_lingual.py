# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import uuid
import asyncio
import aiohttp
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict

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

EVALUATOR = RAGEvaluator(model="gemini-3.5-flash-lite")
DATASET_PATH = PROJECT_ROOT / "Data" / "test_cross_lingual_dataset.json"

@dataclass
class CrossLingualCase:
    id: str
    pair_id: str
    lang: str
    name: str
    question: str
    expected_keywords_any: list = field(default_factory=list)
    expected_media: bool = False
    allowed_page_ids: list = field(default_factory=list)
    expected_rubric: str = ""
    is_refusal_test: bool = False

@dataclass
class CaseResult:
    case: CrossLingualCase
    passed: bool
    status_code: int
    latency: float
    response_text: str
    judge_verdict: Optional[JudgeVerdict] = None
    media_found: list = field(default_factory=list)
    source_found: bool = False
    error: Optional[str] = None

def load_dataset() -> List[CrossLingualCase]:
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)
    return [
        CrossLingualCase(
            id=i["id"],
            pair_id=i["pair_id"],
            lang=i["lang"],
            name=i["name"],
            question=i["question"],
            expected_keywords_any=i.get("expected_keywords_any", []),
            expected_media=i.get("expected_media", False),
            allowed_page_ids=i.get("allowed_page_ids", []),
            expected_rubric=i.get("expected_rubric", ""),
            is_refusal_test=i.get("is_refusal_test", False)
        )
        for i in items
    ]

async def run_case(case: CrossLingualCase, session: aiohttp.ClientSession) -> CaseResult:
    session_uid = f"cl_{uuid.uuid4().hex[:10]}"
    processed_q = preprocess_query(case.question) if case.lang == "ru" else case.question
    payload = {
        "text": processed_q,
        "sessionId": session_uid,
        "chatId": session_uid
    }
    t0 = time.perf_counter()
    try:
        async with session.post(N8N_RAG_URL, json=payload, timeout=aiohttp.ClientTimeout(total=35)) as resp:
            lat = round(time.perf_counter() - t0, 2)
            if resp.status != 200:
                text = await resp.text()
                return CaseResult(case, False, resp.status, lat, text, error=f"HTTP {resp.status}")
            data = await resp.json()
            out = data.get("output", "")
            resp_text = str(out.get("text", out) if isinstance(out, dict) else out)
    except Exception as e:
        lat = round(time.perf_counter() - t0, 2)
        return CaseResult(case, False, 0, lat, "", error=str(e))

    # LLM Judge evaluation
    verdict = await EVALUATOR.evaluate(
        question=case.question,
        response_text=resp_text,
        expected_rubric=case.expected_rubric,
        is_refusal_test=case.is_refusal_test
    )

    # Keywords check
    kw_passed = True
    resp_l = resp_text.lower()
    for grp in case.expected_keywords_any:
        if not any(k.lower() in resp_l for k in grp):
            kw_passed = False
            break

    # Media check
    import re
    media_imgs = re.findall(r"🖼\s*\[(?:Изображение|Image):\s*([^\]]+)\]", resp_text, re.I)
    media_gifs = re.findall(r"🎥\s*\[(?:Анимация|Animation):\s*([^\]]+)\]", resp_text, re.I)
    md_imgs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", resp_text)
    total_media = media_imgs + media_gifs + md_imgs
    media_ok = (len(total_media) > 0) if case.expected_media else True

    # Source check
    source_ok = bool(re.search(r"📌\s*<b>(?:Источник|Source):</b>", resp_text, re.I)) or case.is_refusal_test

    passed = verdict.is_correct and (kw_passed or verdict.confidence >= 0.8) and media_ok
    return CaseResult(
        case=case,
        passed=passed,
        status_code=200,
        latency=lat,
        response_text=resp_text,
        judge_verdict=verdict,
        media_found=total_media,
        source_found=source_ok
    )

async def main():
    import sys
    cases = load_dataset()
    if "--lang" in sys.argv:
        target_lang = sys.argv[sys.argv.index("--lang") + 1].lower()
        cases = [c for c in cases if c.lang == target_lang]
    elif "--en" in sys.argv:
        cases = [c for c in cases if c.lang == "en"]
    elif "--ru" in sys.argv:
        cases = [c for c in cases if c.lang == "ru"]
    print(f"Loaded {len(cases)} test cases to run")
    results: List[CaseResult] = []

    async with aiohttp.ClientSession() as session:
        for i, c in enumerate(cases):
            print(f"[{i+1}/{len(cases)}] Running {c.id} ({c.name})...", end=" ", flush=True)
            res = await run_case(c, session)
            results.append(res)
            status_icon = "🟢 PASS" if res.passed else "🔴 FAIL"
            score = f"Conf: {res.judge_verdict.confidence:.2f}" if res.judge_verdict else ""
            print(f"{status_icon} ({res.latency}s) {score}")
            if not res.passed and res.judge_verdict:
                print(f"   Reason: {res.judge_verdict.verdict_reason}")
            await asyncio.sleep(2.0)

    # Summary
    ru_res = [r for r in results if r.case.lang == "ru"]
    en_res = [r for r in results if r.case.lang == "en"]

    ru_pass = sum(1 for r in ru_res if r.passed)
    en_pass = sum(1 for r in en_res if r.passed)

    print("\n" + "="*60)
    print("CROSS-LINGUAL BENCHMARK SUMMARY")
    print("="*60)
    if ru_res:
        print(f"Russian Cases (RU): {ru_pass} / {len(ru_res)} ({ru_pass/len(ru_res)*100:.1f}%)")
    if en_res:
        print(f"English Cases (EN): {en_pass} / {len(en_res)} ({en_pass/len(en_res)*100:.1f}%)")
    if results:
        print(f"Total Pass Rate:    {sum(1 for r in results if r.passed)} / {len(results)} ({sum(1 for r in results if r.passed)/len(results)*100:.1f}%)")
        print(f"Average Latency:    {sum(r.latency for r in results)/len(results):.2f}s")

    # Pair consistency
    pairs = {}
    for r in results:
        pairs.setdefault(r.case.pair_id, {})[r.case.lang] = r.passed
    consistent = 0
    if ru_res and en_res:
        consistent = sum(1 for p in pairs.values() if p.get("ru") == p.get("en"))
        print(f"Cross-Lingual Consistency: {consistent} / {len(pairs)} pairs ({consistent/len(pairs)*100:.1f}%)")
    print("="*60)

    # Save detailed markdown report
    report_file = PROJECT_ROOT / "Research" / "test_cross_lingual_run_log.md"
    total_media_sent = sum(len(r.media_found) for r in results)
    total_passed = sum(1 for r in results if r.passed)
    avg_latency = round(sum(r.latency for r in results) / len(results), 2) if results else 0

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(f"# Полный отчет кросс-языкового бенчмарка Cross-Lingual RAG ({len(results)} тестов)\n\n")
        f.write(f"- **Дата запуска**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **Всего тестов**: {len(results)}\n")
        if ru_res:
            f.write(f"- **Russian Cases (RU)**: {ru_pass} / {len(ru_res)} ({ru_pass/len(ru_res)*100:.1f}%)\n")
        if en_res:
            f.write(f"- **English Cases (EN)**: {en_pass} / {len(en_res)} ({en_pass/len(en_res)*100:.1f}%)\n")
        f.write(f"- **Общий Pass Rate**: {total_passed} / {len(results)} ({total_passed/len(results)*100:.1f}%)\n")
        if ru_res and en_res:
            f.write(f"- **Кросс-языковая согласованность (Consistency)**: {consistent} / {len(pairs)} пар ({consistent/len(pairs)*100:.1f}%)\n")
        f.write(f"- **Средняя задержка**: {avg_latency} сек\n")
        f.write(f"- **Доставлено медиа-вложений**: {total_media_sent} шт.\n\n")
        f.write("---\n\n")
        f.write("## 📝 Протоколы тестирования каждого кейса\n\n")

        for idx, r in enumerate(results, 1):
            status_icon = "🟢 PASS" if r.passed else "🔴 FAIL"
            f.write(f"### [{idx:02d}/{len(results):02d}] {r.case.id}: {r.case.name} [{r.case.lang.upper()}] — {status_icon}\n\n")
            f.write(f"- **Вопрос пользователя**: *«{r.case.question}»*\n")
            f.write(f"- **Время ответа**: `{r.latency} сек` (HTTP {r.status_code})\n")
            f.write(f"- **Медиа-вложения**: {len(r.media_found)} шт. {r.media_found}\n")
            f.write(f"- **Ссылка на источник**: {'✅ Найдена' if r.source_found else '❌ Отсутствует'}\n")
            if r.judge_verdict:
                f.write(f"- **⚖️ Вердикт арбитра (LLM-as-a-Judge)**: `{'✅ Верно' if r.judge_verdict.is_correct else '❌ Неверно'}` (Уверенность: {r.judge_verdict.confidence})\n")
                f.write(f"- **Обоснование арбитра**: *{r.judge_verdict.verdict_reason}*\n")
            if r.error:
                f.write(f"- **⚠️ Ошибка**: `{r.error}`\n")
            f.write("\n**Полный текст ответа бота:**\n\n")
            quoted_resp = r.response_text.replace(chr(10), chr(10) + '> ')
            f.write(f"> {quoted_resp}\n\n")
            f.write("---\n\n")

    print(f"\n📁 Детальный кросс-языковой отчет сохранен в: {report_file}")

if __name__ == "__main__":
    asyncio.run(main())
