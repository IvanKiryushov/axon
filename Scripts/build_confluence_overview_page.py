"""
Сборка и публикация витринной страницы «Обзор» (homepage) пространства AXON BIM в Confluence Cloud.

Аудитория: потенциальные клиенты и пользователи, впервые открывающие базу знаний.

Режимы:
    python Scripts/build_confluence_overview_page.py            # dry-run: storage-XHTML в Data/backups/overview_preview.xml
    python Scripts/build_confluence_overview_page.py --apply    # бэкап текущей версии + публикация
    python Scripts/build_confluence_overview_page.py --rollback # откат к последнему бэкапу

Счетчики (страницы, медиа, разделы) берутся из API в момент сборки, а не хардкодятся.
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from requests.auth import HTTPBasicAuth

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUPS_DIR = REPO_ROOT / "Data" / "backups"
SPACE_KEY = "~70121c7daeab8fb0d42e5a9d66704bad7c375"
ROOT_SECTION_TITLE = "04.04.02 КР"

BOT_URL = "https://t.me/bim_axonbot?start=confluence"
OFFER_URL = "https://t.me/bim_axonbot?start=offer"
VIDEO_URL = "https://youtu.be/70uFZETkw-g"
GITHUB_URL = "https://github.com/IvanKiryushov/axon"


# ----------------------------------------------------------------------------
# Confluence client
# ----------------------------------------------------------------------------
def load_env_file(env_path: Path):
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


def get_client():
    load_env_file(REPO_ROOT / ".env")
    email = os.environ.get("CONFLUENCE_EMAIL")
    token = os.environ.get("CONFLUENCE_API_TOKEN")
    if not email or not token:
        print("❌ В .env не заданы CONFLUENCE_EMAIL или CONFLUENCE_API_TOKEN")
        sys.exit(1)
    base_url = "https://axon-bim.atlassian.net/wiki/rest/api"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    return base_url, HTTPBasicAuth(email, token), headers


def api_get(base_url, auth, path):
    res = requests.get(f"{base_url}{path}", auth=auth, headers={"Accept": "application/json"})
    res.encoding = "utf-8"
    res.raise_for_status()
    return res.json()


def get_homepage(base_url, auth):
    sp = api_get(base_url, auth, f"/space/{SPACE_KEY}?expand=homepage")
    hp_id = sp["homepage"]["id"]
    return api_get(base_url, auth, f"/content/{hp_id}?expand=body.storage,version,space")


def collect_stats(base_url, auth):
    def total(cql):
        return api_get(base_url, auth, f"/search?cql={requests.utils.quote(cql)}&limit=1").get("totalSize", 0)

    pages = total(f'space="{SPACE_KEY}" and type=page')
    media = total(f'space="{SPACE_KEY}" and type=attachment')
    root = api_get(base_url, auth, f"/content?spaceKey={requests.utils.quote(SPACE_KEY)}"
                                   f"&title={requests.utils.quote(ROOT_SECTION_TITLE)}")
    sections = 0
    if root.get("results"):
        rid = root["results"][0]["id"]
        sections = api_get(base_url, auth, f"/content/{rid}/child/page?limit=100").get("size", 0)
    return {"pages": pages, "media": media, "sections": sections}


# ----------------------------------------------------------------------------
# Storage-format helpers
# ----------------------------------------------------------------------------
def link(title, text=None):
    body = f"<ac:plain-text-link-body><![CDATA[{text}]]></ac:plain-text-link-body>" if text else ""
    return f'<ac:link><ri:page ri:content-title="{escape(title, {chr(34): "&quot;"})}" />{body}</ac:link>'


def ext_link(url, text):
    return f'<a href="{escape(url)}">{escape(text)}</a>'


def status(title, colour="Grey", subtle=False):
    sub = '<ac:parameter ac:name="subtle">true</ac:parameter>' if subtle else ""
    return (f'<ac:structured-macro ac:name="status" ac:schema-version="1">'
            f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
            f'<ac:parameter ac:name="title">{escape(title)}</ac:parameter>{sub}</ac:structured-macro>')


def panel(kind, inner, title=None):
    """kind: info | tip | note | warning — нативные панели Confluence Cloud."""
    t = f'<ac:parameter ac:name="title">{escape(title)}</ac:parameter>' if title else ""
    return (f'<ac:structured-macro ac:name="{kind}" ac:schema-version="1">{t}'
            f'<ac:rich-text-body>{inner}</ac:rich-text-body></ac:structured-macro>')


def section(layout, *cells):
    body = "".join(f"<ac:layout-cell>{c}</ac:layout-cell>" for c in cells)
    return f'<ac:layout-section ac:type="{layout}">{body}</ac:layout-section>'


def ul(items):
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def stat_card(kind, value, label):
    return panel(kind, f'<h1 style="text-align: center;">{value}</h1>'
                       f'<p style="text-align: center;">{label}</p>')


# ----------------------------------------------------------------------------
# Page content
# ----------------------------------------------------------------------------
def build_storage(stats):
    media_rounded = f"{(stats['media'] // 10) * 10}+" if stats["media"] >= 10 else str(stats["media"])

    hero = section(
        "fixed-width",
        '<h1>🏗️ AXON BIM — база знаний по BIM-стандарту раздела КР</h1>'
        '<p><strong>Регламенты моделирования, армирования и выпуска документации конструктивного '
        'раздела в Autodesk Revit</strong> — в одном месте, со скриншотами интерфейса, GIF-инструкциями '
        'и правилами, которые обычно живут только в головах ведущих инженеров.</p>'
        f'<p>{status("Autodesk Revit", "Blue")} {status("КР / КЖ", "Green")} '
        f'{status("AI-ассистент", "Purple")} {status("Демо-версия", "Yellow")}</p>'
    )

    demo_notice = section(
        "fixed-width",
        panel("info",
              '<p>Это <strong>демонстрационная копия</strong> реального корпоративного стандарта. '
              'Названия компаний, шифры объектов и персональные данные удалены или обезличены — '
              'структура, логика регламентов и иллюстрации сохранены полностью.</p>',
              title="О базе")
    )

    stats_row = section(
        "three_equal",
        stat_card("info", stats["pages"], "страниц регламентов"),
        stat_card("tip", media_rounded, "скриншотов, схем и GIF"),
        stat_card("note", stats["sections"], "тематических разделов"),
    )

    start_header = section("fixed-width", "<h2>🧭 С чего начать</h2>"
                           "<p>Три маршрута по базе — в зависимости от того, что вам нужно прямо сейчас.</p>")

    start_cards = section(
        "three_equal",
        panel("tip", "<p><strong>Базовые правила проекта</strong></p>" + ul([
            link("Правила именования КР"),
            link("Запрещенные инструменты и действия"),
            link("Шаблон", "Шаблон проекта"),
            link("Обучение"),
        ]), title="📘 Первый день в проекте"),
        panel("info", "<p><strong>Как строить модель</strong></p>" + ul([
            link("Моделирование"),
            link("Семейства"),
            link("Армирование"),
            link("Инструменты"),
        ]), title="🧱 Моделирование"),
        panel("note", "<p><strong>Как выпускать документацию</strong></p>" + ul([
            link("Параметры"),
            link("Спецификации"),
            link("Оформление"),
            link("Выдача документации"),
        ]), title="📐 Документация"),
    )

    bot_section = section(
        "two_equal",
        "<h2>🤖 Не хотите искать — спросите AxonBot</h2>"
        "<p><strong>AxonBot</strong> — AI-консультант в Telegram, обученный на этой базе. "
        "Задайте вопрос своими словами — на русском или английском — и получите ответ "
        "<strong>за несколько секунд</strong>:</p>"
        + ul([
            "с точными именами семейств и параметров из регламента;",
            "со скриншотами и GIF-инструкциями прямо в чате;",
            "со ссылкой на страницу-первоисточник;",
            "с честным отказом, если в стандарте ответа нет, — без выдумок.",
        ])
        + f'<p>👉 {ext_link(BOT_URL, "Открыть @bim_axonbot в Telegram")}<br />'
          f'🎬 {ext_link(VIDEO_URL, "Посмотреть 2,5-минутное демо")}</p>',
        panel("tip",
              "<p><em>«У меня свайное поле на 800 свай — как быстро раскопировать по сетке "
              "и какой параметр отвечает за срубку голов?»</em></p>"
              "<p><em>«Какая маска используется для монолитных стен и пилонов?»</em></p>"
              "<p><em>«Какое семейство регламентировано для фиксаторов сеток и как они расставляются?»</em></p>"
              "<p><em>«How do I model a bent rebar with a custom hook without breaking the schedule?»</em></p>",
              title="💬 Примеры вопросов"),
    )

    how_to = section(
        "fixed-width",
        "<h3>Как получить доступ к боту</h3>"
        "<ol>"
        f"<li>Откройте {ext_link(BOT_URL, '@bim_axonbot')} и нажмите <strong>Start</strong>.</li>"
        "<li>Заполните короткую заявку (2 шага) — она сразу уходит администратору.</li>"
        "<li>После одобрения задавайте вопросы в свободной форме.</li>"
        "</ol>"
    )

    structure = section(
        "fixed-width",
        "<h2>🗂️ Полная структура базы</h2>"
        '<ac:structured-macro ac:name="expand" ac:schema-version="1">'
        '<ac:parameter ac:name="title">Показать все разделы и страницы</ac:parameter>'
        "<ac:rich-text-body>"
        '<ac:structured-macro ac:name="children" ac:schema-version="2">'
        f'<ac:parameter ac:name="page">{link(ROOT_SECTION_TITLE)}</ac:parameter>'
        '<ac:parameter ac:name="depth">2</ac:parameter>'
        '<ac:parameter ac:name="sort">title</ac:parameter>'
        "</ac:structured-macro>"
        "</ac:rich-text-body></ac:structured-macro>"
    )

    updates = section(
        "two_equal",
        "<h2>🕒 Последние обновления</h2>"
        '<ac:structured-macro ac:name="recently-updated" ac:schema-version="1">'
        '<ac:parameter ac:name="max">5</ac:parameter>'
        '<ac:parameter ac:name="types">page</ac:parameter>'
        "</ac:structured-macro>",
        "<h2>💡 Как пользоваться базой</h2>"
        + ul([
            "Поиск Confluence (<code>/</code> или строка поиска сверху) ищет по всем регламентам.",
            "Имена семейств и параметров приведены дословно — их можно копировать в Revit.",
            "Раздел «Запрещенные инструменты и действия» имеет приоритет над остальными страницами.",
            "Нет ответа в регламенте — исключения согласуются с BIM-отделом.",
        ]),
    )

    footer = section(
        "fixed-width",
        "<hr />"
        "<p style=\"text-align: center;\">"
        f"🤖 {ext_link(BOT_URL, 'AxonBot в Telegram')} · "
        f"💼 {ext_link(OFFER_URL, 'Обсудить внедрение')} · "
        f"🎬 {ext_link(VIDEO_URL, 'Видео-демо')} · "
        f"💻 {ext_link(GITHUB_URL, 'Исходный код на GitHub')}"
        "</p>"
        "<p style=\"text-align: center;\"><span style=\"color: rgb(151,160,175);\">"
        "Хотите такую же базу знаний и AI-ассистента для своего BIM-стандарта? "
        f"{ext_link(OFFER_URL, 'Заполните короткую анкету в боте')} — обсудим.</span></p>"
    )

    return "<ac:layout>" + "".join([
        hero, demo_notice, stats_row, start_header, start_cards,
        bot_section, how_to, structure, updates, footer,
    ]) + "</ac:layout>"


# ----------------------------------------------------------------------------
# Backup / apply / rollback
# ----------------------------------------------------------------------------
def save_backup(page):
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BACKUPS_DIR / f"confluence_overview_{page['id']}_backup_{ts}.json"
    path.write_text(json.dumps({
        "page_id": page["id"],
        "title": page["title"],
        "version": page["version"]["number"],
        "body_storage": page["body"]["storage"]["value"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 Бэкап: {path}")
    return path


def put_page(base_url, auth, headers, page, new_body, message):
    payload = {
        "id": page["id"],
        "type": "page",
        "title": page["title"],
        "space": {"key": SPACE_KEY},
        "body": {"storage": {"value": new_body, "representation": "storage"}},
        "version": {"number": page["version"]["number"] + 1, "message": message},
    }
    res = requests.put(f"{base_url}/content/{page['id']}", headers=headers, auth=auth,
                       data=json.dumps(payload).encode("utf-8"))
    if res.status_code != 200:
        print(f"❌ Ошибка публикации: {res.status_code} — {res.text[:1000]}")
        sys.exit(1)
    print(f"✅ Опубликовано: версия {res.json()['version']['number']}")


def verify_render(base_url, auth, page_id):
    view = api_get(base_url, auth, f"/content/{page_id}?expand=body.view")["body"]["view"]["value"]
    problems = [m for m in ("Unknown macro", "Неизвестный макрос", "error rendering", "Ошибка отображения")
                if m.lower() in view.lower()]
    if problems:
        print(f"⚠️ В отрендеренной странице найдены маркеры ошибок: {problems}")
    else:
        print("✅ Рендер без ошибок макросов")


def main():
    parser = argparse.ArgumentParser(description="Витринная страница «Обзор» пространства AXON BIM")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()

    base_url, auth, headers = get_client()
    page = get_homepage(base_url, auth)

    if args.rollback:
        backups = sorted(BACKUPS_DIR.glob(f"confluence_overview_{page['id']}_backup_*.json"))
        if not backups:
            print("❌ Бэкапы не найдены")
            sys.exit(1)
        data = json.loads(backups[-1].read_text(encoding="utf-8"))
        put_page(base_url, auth, headers, page, data["body_storage"], f"Rollback to v{data['version']}")
        return

    stats = collect_stats(base_url, auth)
    print(f"📊 Статистика: {stats}")
    body = build_storage(stats)

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    preview = BACKUPS_DIR / "overview_preview.xml"
    preview.write_text(body, encoding="utf-8")
    print(f"📝 Storage-XHTML: {preview}")

    if args.apply:
        save_backup(page)
        put_page(base_url, auth, headers, page, body, "Витринная страница обзора базы знаний")
        verify_render(base_url, auth, page["id"])
    else:
        print("💡 Для публикации: python Scripts/build_confluence_overview_page.py --apply")


if __name__ == "__main__":
    main()
