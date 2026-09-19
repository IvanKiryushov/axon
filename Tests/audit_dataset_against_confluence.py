import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

with open('Data/test_bench_kr_dataset.json', 'r', encoding='utf-8') as f:
    cases = json.load(f)

with open('Data/confluence_pages_cache.json', 'r', encoding='utf-8') as f:
    pages = json.load(f)

print(f"Loaded {len(cases)} cases and {len(pages)} pages.")

# Let's inspect each case
audit_results = []
for idx, c in enumerate(cases, 1):
    cid = c['id']
    name = c['name']
    q = c['question']
    rubric = c.get('expected_rubric', '')
    keywords = c.get('expected_keywords_any', [])
    forbidden = c.get('forbidden_keywords', [])
    pids = [str(p) for p in c.get('allowed_page_ids', [])]
    is_refusal = c.get('is_refusal_test', False)
    
    page_texts = []
    page_titles = []
    for pid in pids:
        if pid in pages:
            page_titles.append(f"{pages[pid]['title']} (#{pid})")
            page_texts.append(pages[pid]['text'])
    
    combined_text = " ".join(page_texts)
    
    audit_results.append({
        'idx': idx,
        'id': cid,
        'name': name,
        'question': q,
        'rubric': rubric,
        'keywords': keywords,
        'forbidden': forbidden,
        'pids': pids,
        'page_titles': page_titles,
        'is_refusal': is_refusal,
        'text_len': len(combined_text)
    })

print(f"Prepared audit structure for {len(audit_results)} cases.")
