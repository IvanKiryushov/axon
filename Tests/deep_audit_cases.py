import os
import sys
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY missing")
    sys.exit(1)

from google import genai

client = genai.Client(api_key=api_key)

with open('Data/test_bench_kr_dataset.json', 'r', encoding='utf-8') as f:
    cases = json.load(f)

with open('Data/confluence_pages_cache.json', 'r', encoding='utf-8') as f:
    pages = json.load(f)

out_file = Path('Research/audit_dataset_findings.json')
existing_results = {}
if out_file.exists():
    try:
        with open(out_file, 'r', encoding='utf-8') as f:
            for r in json.load(f):
                if r.get('audit_verdict') != 'ERROR':
                    existing_results[r['case_id']] = r
    except Exception:
        pass

AUDIT_PROMPT = """Ты — ведущий инженер по качеству BIM-стандартов КР и строгий аудитор RAG-бенчмарка.
Перед тобой тест-кейс из бенчмарка и реальный текст регламентных статей Confluence, на которые он ссылается.

Твоя задача — критически проверить:
1. Соответствует ли вопрос реальному содержанию регламента?
2. Соответствует ли эталонный ответ (expected_rubric) реальному тексту статьи? Нет ли в рубрике ошибок, домыслов, устаревших формулировок или прямых противоречий регламенту (как, например, предложение запрещенного инструмента 'Редактировать профиль' для подрезки торцов)?
3. Корректны ли ключевые слова (expected_keywords_any) и запрещенные слова (forbidden_keywords)?
4. Требуются ли исправления?

Входные данные:
- ID кейса: {case_id} ({case_name})
- Вопрос пользователя: {question}
- Рубрика эталона (expected_rubric): {rubric}
- Ключевые слова: {keywords}
- Запрещенные слова: {forbidden}
- Out-of-Scope (отказ): {is_refusal}
- Статьи Confluence ({page_titles}):
{confluence_text}

Формат ответа — строго JSON:
{{
  "is_aligned": true,
  "has_rubric_flaw": false,
  "flaw_description": "Описание несоответствия или ошибки в рубрике / ключевых словах (если есть, иначе пустая строка)",
  "recommended_rubric": "Исправленная или подтвержденная точная рубрика по тексту статьи",
  "recommended_keywords": "Рекомендованные группы ключевых слов (или 'без изменений')",
  "recommended_forbidden": "Рекомендованные запрещенные слова (или 'без изменений')",
  "audit_verdict": "OK"
}}
или если найден дефект:
{{
  "is_aligned": false,
  "has_rubric_flaw": true,
  "flaw_description": "...",
  "recommended_rubric": "...",
  "recommended_keywords": "...",
  "recommended_forbidden": "...",
  "audit_verdict": "NEEDS_CORRECTION"
}}
"""

async def audit_case(case):
    cid = case['id']
    name = case['name']
    q = case['question']
    rubric = case.get('expected_rubric', '')
    keywords = case.get('expected_keywords_any', [])
    forbidden = case.get('forbidden_keywords', [])
    pids = [str(p) for p in case.get('allowed_page_ids', [])]
    is_refusal = case.get('is_refusal_test', False)

    page_titles = []
    page_texts = []
    for pid in pids:
        if pid in pages:
            page_titles.append(f"{pages[pid]['title']} (#{pid})")
            page_texts.append(f"=== {pages[pid]['title']} (#{pid}) ===\n{pages[pid]['text'][:3500]}")

    confluence_text = "\n\n".join(page_texts) if page_texts else "(Статьи не привязаны, проверка на Out-of-Scope)"

    prompt = AUDIT_PROMPT.format(
        case_id=cid,
        case_name=name,
        question=q,
        rubric=rubric,
        keywords=keywords,
        forbidden=forbidden,
        is_refusal=is_refusal,
        page_titles=", ".join(page_titles) if page_titles else "Out-of-Scope",
        confluence_text=confluence_text
    )

    for attempt in range(5):
        try:
            response = await client.aio.models.generate_content(
                model='gemini-3.5-flash-lite',
                contents=prompt,
                config={
                    'temperature': 0.1,
                    'response_mime_type': 'application/json'
                }
            )
            data = json.loads(response.text)
            data['case_id'] = cid
            data['case_name'] = name
            print(f"[{cid}] {name} -> {data.get('audit_verdict')}")
            return data
        except Exception as e:
            print(f"[{cid}] Retry {attempt+1}: {e}")
            await asyncio.sleep(5 * (attempt + 1))

    return {
        'case_id': cid,
        'case_name': name,
        'is_aligned': False,
        'has_rubric_flaw': True,
        'flaw_description': "Failed after 5 retries",
        'audit_verdict': "ERROR"
    }

async def main():
    cases_to_run = [c for c in cases if c['id'] not in existing_results]
    print(f"Already completed: {len(existing_results)}. Running remaining {len(cases_to_run)} cases sequentially...")

    for c in cases_to_run:
        res = await audit_case(c)
        existing_results[c['id']] = res
        await asyncio.sleep(4.5)  # 4.5s delay to stay safely under 15 RPM

    all_results = [existing_results[c['id']] for c in cases if c['id'] in existing_results]
    needs_correction = [r for r in all_results if r.get('audit_verdict') == 'NEEDS_CORRECTION']
    ok_cases = [r for r in all_results if r.get('audit_verdict') == 'OK']
    print(f"\nFinal Audit: {len(ok_cases)} OK, {len(needs_correction)} NEEDS_CORRECTION, Total: {len(all_results)}")

    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
