import os
import sys
import json
import re
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

email = os.getenv("CONFLUENCE_EMAIL")
token = os.getenv("CONFLUENCE_API_TOKEN")
if not email or not token:
    print("Error: Confluence credentials missing")
    sys.exit(1)

auth = HTTPBasicAuth(email, token)

with open('Data/test_bench_kr_dataset.json', 'r', encoding='utf-8') as f:
    cases = json.load(f)

page_ids = set()
for c in cases:
    for p in c.get('allowed_page_ids', []):
        page_ids.add(p)

cache_file = Path('Data/confluence_pages_cache.json')
pages_data = {}
if cache_file.exists():
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            pages_data = json.load(f)
    except Exception:
        pass

for pid in sorted(list(page_ids)):
    pid_str = str(pid)
    if pid_str in pages_data:
        continue
    url = f"https://ivankiryushovsworkspace-25173842.atlassian.net/wiki/rest/api/content/{pid_str}?expand=body.storage"
    try:
        r = requests.get(url, auth=auth, timeout=15)
        if r.status_code == 200:
            d = r.json()
            title = d.get('title', '')
            html_body = d.get('body', {}).get('storage', {}).get('value', '')
            clean_text = re.sub(r'<[^<]+?>', ' ', html_body)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            pages_data[pid_str] = {
                'id': pid,
                'title': title,
                'text': clean_text
            }
            print(f"Fetched {pid_str}: {title} ({len(clean_text)} chars)")
        else:
            print(f"Failed {pid_str}: HTTP {r.status_code}")
    except Exception as e:
        print(f"Error fetching {pid_str}: {e}")

with open(cache_file, 'w', encoding='utf-8') as f:
    json.dump(pages_data, f, ensure_ascii=False, indent=2)

print(f"Done. Cached {len(pages_data)} pages to Data/confluence_pages_cache.json")
