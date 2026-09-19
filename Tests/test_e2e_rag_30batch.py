import re
import sys
import time
import uuid
import asyncio
import aiohttp
import pytest
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# Добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
    expected_keywords_any: list[list[str]] = field(default_factory=list)
    forbidden_keywords: list[str] = field(default_factory=list)
    expected_media: bool = False
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
    source_found: bool
    error_message: Optional[str] = None

TEST_SUITE_30_BATCH = [
    # 1. Спецификация сборных элементов (7438884) — Регресс
    RAGTestCase(
        id="TC-01",
        name="Фильтры спецификаций сборных элементов",
        question="Как настроить фильтры в заготовке спецификации конструкций для сборняка и какую категорию элементов там ставить?",
        expected_keywords_any=[
            ["Обобщенные модели", "Обобщенная модель", "категори"],
            ["Фильтр", "Спецификаци"],
        ],
        expected_media=True,
        expected_source_pattern=r"atlassian\.net|Спецификация сборных элементов",
    ),

    # 2. Виды на типовых этажах (7438983) — Повтор фикса
    RAGTestCase(
        id="TC-02",
        name="Scope Box типовых этажей",
        question="У меня разрезы на планах типовых этажей не отображаются, как настроить их видимость через рамку и что прописать в свойствах вида?",
        expected_keywords_any=[
            ["ADSK_Примечание"],
            ["Типовой этаж", "этаж", "разрез"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Виды на типовых этажах",
    ),

    # 3. Виды на типовых этажах (7438983) — Новый
    RAGTestCase(
        id="TC-03",
        name="Тиражирование планов типовых этажей",
        question="Как правильно тиражировать оформленный вид опалубки на остальные типовые этажи и какой плагин использовать?",
        expected_keywords_any=[
            ["Тиражирование", "плагин", "вид", "этаж"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Виды на типовых этажах",
    ),

    # 4. Ключевые параметры арматуры (374675) — Повтор фикса
    RAGTestCase(
        id="TC-04",
        name="!BIM_CORP_Арм_Назначение для пилонов",
        question="Какое назначение арматуры выставлять в ключе для доборных вертикальных стержней в пилонах и колоннах?",
        expected_keywords_any=[
            ["6_Верт_Доп", "7_Конструктивная", "Конструктивная", "Верт_Доп"],
            ["!BIM_CORP_Арм_Назначение", "Арм_Назначение", "назначение"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Ключевые параметры арматуры",
    ),

    # 5. Ключевые параметры арматуры (374675) — Регресс
    RAGTestCase(
        id="TC-05",
        name="!BIM_CORP_Арм_Подсчет погонаж",
        question="Куда тыкать в свойствах арматуры, чтобы она считалась в спецификации погонными метрами, а не штуками?",
        expected_keywords_any=[
            ["2_пог.м", "пог.м"],
            ["Размер в погонных метрах", "погонных метрах", "!BIM_CORP_Арм_Подсчет", "BIM_CORP_Арм_Подсчет"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Ключевые параметры арматуры",
    ),

    # 6. Ключевые параметры арматуры (374675) — Новый
    RAGTestCase(
        id="TC-06",
        name="Фоновое армирование плиты",
        question="Какое значение ключа в '!BIM_CORP_Арм_Назначение' нужно ставить для фоновой нижней и верхней арматуры плиты?",
        expected_keywords_any=[
            ["1_Нижн_Фон", "2_Верх_Фон", "Фон", "Нижн_Фон", "Верх_Фон"],
            ["Арм_Назначение", "назначение", "плит"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Ключевые параметры арматуры",
    ),

    # 7. Библиотека материалов (26542469) — Новый
    RAGTestCase(
        id="TC-07",
        name="Назначение бетона В25 W6 F150",
        question="Как правильно назначить тяжелый бетон В25 W6 F150 для монолитных конструкций из общей библиотеки?",
        expected_keywords_any=[
            ["Бетон", "B25", "В25", "материал", "тяжелый"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Библиотека материалов",
    ),

    # 8. 204_Балконная плита_С термовкладышами (374665) — Регресс
    RAGTestCase(
        id="TC-08",
        name="Балконная плита с термовкладышами",
        question="Балконная плита с термовкладышами не стыкуется с монолитным перекрытием, как правильно её посадить и соединить геометрию?",
        expected_keywords_any=[
            ["Соединить", "компонент", "плита"],
            ["термовкладыш", "Полистирол", "термовкладышами"],
        ],
        expected_media=True,
        expected_source_pattern=r"atlassian\.net|204_Балконная плита",
    ),

    # 9. 204_Балконная плита_С термовкладышами (374665) — Новый
    RAGTestCase(
        id="TC-09",
        name="Утеплитель термовкладыша балкона",
        question="Какой утеплитель заложен в термовкладыш балконной плиты 204 и как поменять толщину термовкладыша?",
        expected_keywords_any=[
            ["Полистирол", "утеплитель", "термовкладыш"],
            ["толщина", "параметр", "плит"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|204_Балконная плита",
    ),

    # 10. 205_Лестничная площадка (374669) — Новый
    RAGTestCase(
        id="TC-10",
        name="Отключение задней балки площадки",
        question="Как убрать заднюю балку у лестничной площадки 205, если площадка опирается прямо на монолитную стену?",
        expected_keywords_any=[
            ["балкаСзади_Вкл", "балкаСзади", "балка", "отключ", "галочк"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|205_Лестничная площадка",
    ),

    # 11. 205_Лестничная площадка (374669) — Повтор фикса
    RAGTestCase(
        id="TC-11",
        name="Опирание балок лестничной площадки",
        question="Как включить опирание балок у лестничной площадки, чтобы они отображались на опалубке?",
        expected_keywords_any=[
            ["балки_Опирание_Вкл", "Опирание", "балки"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|205_Лестничная площадка",
    ),

    # 12. 205_Лестничный марш (5276503) — Новый
    RAGTestCase(
        id="TC-12",
        name="Параметры ступеней марша 205",
        question="Как в семействе лестничного марша 205 поменять количество ступеней и ширину проступи?",
        expected_keywords_any=[
            ["ступен", "проступ", "подступенок", "марш", "205"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|205_Лестничный марш",
    ),

    # 13. 205_Лобовая балка (374736) — Новый
    RAGTestCase(
        id="TC-13",
        name="Четверть под плиту в лобовой балке",
        question="Как включить четверть под плиту перекрытия в семействе лобовой балки 205?",
        expected_keywords_any=[
            ["четверть", "плит", "балк", "опирани"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|205_Лобовая балка",
    ),

    # 14. Лестницы КЖ (374705) — Новый
    RAGTestCase(
        id="TC-14",
        name="Шаблоны видов лестниц КЖ",
        question="Каким шаблоном вида оформляются опалубочные планы и разрезы монолитных лестниц?",
        expected_keywords_any=[
            ["КР_Опл", "Лестниц", "шаблон", "вид"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Лестницы КЖ",
    ),

    # 15. 266_АрмСтены_Торец стены (13045175) — Новый
    RAGTestCase(
        id="TC-15",
        name="Шпильки в торце стены",
        question="Как в торце стены включить установку шпилек и настроить их шаг?",
        expected_keywords_any=[
            ["шпильк", "шаг", "торец", "стержн"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|266_АрмСтены_Торец стены",
    ),

    # 16. 266_АрмСтены_Торец стены (13045175) — Повтор фикса
    RAGTestCase(
        id="TC-16",
        name="П-образные стержни торца стены",
        question="Как в семействе обрамления торца стены поменять шаг и отступы П-образных хомутов?",
        expected_keywords_any=[
            ["Побр_Шаг", "Побр_Отступ", "П-образный", "Побр"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|266_АрмСтены_Торец стены",
    ),

    # 17. 266_АрмСтены_Угол стены (13045828) — Повтор фикса
    RAGTestCase(
        id="TC-17",
        name="Армирование угла стены габариты",
        question="Какие толщины стен и перекрытия нужно забить в свойствах армирования угла стены, чтобы хомуты встали по размеру?",
        expected_keywords_any=[
            ["плита Толщина", "стена 1 Толщина", "стена 2 Толщина", "Толщина"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|266_АрмСтены_Угол стены",
    ),

    # 18. 266_АрмСтены_Угол стены (13045828) — Новый
    RAGTestCase(
        id="TC-18",
        name="Г-образные стержни угла стены",
        question="Как включить Г-образные стержни обрамления угла стены и настроить их нахлестку?",
        expected_keywords_any=[
            ["Г-образн", "Гобр", "нахлестк", "стержн", "угол"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|266_АрмСтены_Угол стены",
    ),

    # 19. 266_АрмСтены_Армирование пилона (7443388) — Новый
    RAGTestCase(
        id="TC-19",
        name="Зоны сгущения хомутов пилона",
        question="Как настроить шаг и зоны сгущения хомутов по высоте в семействе армирования пилона?",
        expected_keywords_any=[
            ["хомут", "шаг", "сгущен", "пилон", "высот"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|266_АрмСтены_Армирование пилона",
    ),

    # 20. Котлован (374702) — Повтор фикса
    RAGTestCase(
        id="TC-20",
        name="Формирование котлована семейством 233",
        question="Чем вырезать объем котлована из топоповерхности грунта и какой командой состыковать?",
        expected_keywords_any=[
            ["233_Полый переход фиктивный", "233", "Полый переход"],
            ["Вырезать", "Соединение элементов", "вырезание"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Котлован",
    ),

    # 21. Котлован (374702) — Новый
    RAGTestCase(
        id="TC-21",
        name="Песчаная подсыпка котлована",
        question="Как замоделировать песчаную или гравийную подсыпку под фундаментную плиту в котловане?",
        expected_keywords_any=[
            ["подсыпк", "песчан", "гравийн", "плит", "котлован"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Котлован",
    ),

    # 22. Котлован (374702) — Повтор фикса
    RAGTestCase(
        id="TC-22",
        name="Граница грунта котлована",
        question="Какой штриховкой (цветовой областью) по стандарту показывать границу грунта на разрезе котлована?",
        expected_keywords_any=[
            ["051_Граница грунта", "Граница грунта", "грунт"],
        ],
        expected_media=True,
        expected_source_pattern=r"atlassian\.net|Котлован",
    ),

    # 23. Сваи и свайное поле (374661) — Повтор фикса
    RAGTestCase(
        id="TC-23",
        name="Срубка свай и заделка в ростверк",
        question="Сколько миллиметров по регламенту ставить на срубку голов свай и на заделку сваи в ростверк?",
        expected_keywords_any=[
            ["250", "300", "срубаемаяЧасть_Высота", "срубк"],
            ["50", "заделкаВРостверк", "ростверк"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Сваи и свайное поле",
    ),

    # 24. Сваи и свайное поле (374661) — Новый
    RAGTestCase(
        id="TC-24",
        name="Куст свай и острие",
        question="Каким семейством размещать куст свай и как изменить отметку острия сваи?",
        expected_keywords_any=[
            ["сва", "куст", "остри", "отметк", "201"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Сваи и свайное поле",
    ),

    # 25. Стены и пилоны. Опалубка (374822) — Новый
    RAGTestCase(
        id="TC-25",
        name="Стык стены и пилона",
        question="Как состыковать монолитную стену с пилоном, чтобы штриховка бетона не пересекалась на планах?",
        expected_keywords_any=[
            ["Соединить", "стена", "пилон", "геометри", "штриховк"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Стены и пилоны",
    ),

    # 26. Стены и пилоны. Опалубка (374822) — Новый
    RAGTestCase(
        id="TC-26",
        name="Рабочий набор стен подвала",
        question="В какой рабочий набор помещать стены подвала и надземной части здания?",
        expected_keywords_any=[
            ["КР_Монолит", "рабочий набор", "ворсет"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Стены и пилоны",
    ),

    # 27. Балки (7442721) — Новый
    RAGTestCase(
        id="TC-27",
        name="Соединение балки с перекрытием",
        question="Как настроить соединение монолитной балки с перекрытием, чтобы балка не вырезалась плитой?",
        expected_keywords_any=[
            ["Соединить", "балка", "перекрыти", "порядок"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Балки",
    ),

    # 28. Отверстия в местах пересечения шахт с плитами (374831) — Регресс
    RAGTestCase(
        id="TC-28",
        name="Скрипт пересечения шахт с плитами",
        question="Как автоматически прорезать плиты перекрытий под вентшахты через скрипт в надстройках?",
        expected_keywords_any=[
            ["RevitPythonShell", "Python Shell", "скрипт", "Надстройки"],
            ["шахт", "плит", "отверсти"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Отверстия в местах пересечения шахт с плитами",
    ),

    # 29. Правила именования КР (56432052) — Новый
    RAGTestCase(
        id="TC-29",
        name="Именование листов спецификаций",
        question="Как правильно назвать лист спецификации арматуры и какой префикс присвоить в диспетчере проекта?",
        expected_keywords_any=[
            ["КР", "Лист", "Спецификаци", "префикс"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Правила именования КР",
    ),

    # 30. Запрещенные инструменты и действия (374627) — Новый
    RAGTestCase(
        id="TC-30",
        name="Запрет редактирования профиля",
        question="Можно ли редактировать профиль стены в Ревите, чтобы сделать скошенный парапет или проем?",
        expected_keywords_any=[
            ["Редактировать профиль", "профиль", "запрещен", "недопустим"],
        ],
        expected_media=False,
        expected_source_pattern=r"atlassian\.net|Запрещенные инструменты и действия",
    ),

    # 31. Out-of-Scope / Негативный тест: Раздел ОВ (Вентиляция)
    RAGTestCase(
        id="TC-31",
        name="[Negative] Вопрос по смежному разделу ОВ",
        question="Как правильно соединить круглый воздуховод с дроссель-клапаном в системе приточной вентиляции?",
        is_refusal_test=True,
        expected_keywords_any=[
            ["нет информации", "не найден", "обратитесь к BIM-координатору", "уточните запрос"],
        ],
        forbidden_keywords=["дроссель-клапан", "приточная вентиляция"],
        expected_media=False,
    ),

    # 32. Out-of-Scope / Негативный тест: Несуществующая серия / регламент
    RAGTestCase(
        id="TC-32",
        name="[Negative] Несуществующая типовая серия",
        question="По какой типовой серии 1.412.1-3 подбирать арматурные сетки для монолитных ростверков по регламенту КР?",
        is_refusal_test=True,
        expected_keywords_any=[
            ["нет информации", "не найден", "обратитесь к BIM-координатору", "уточните запрос"],
        ],
        forbidden_keywords=["1.412.1-3"],
        expected_media=False,
    ),
]

def extract_media_from_text(text: str) -> list[str]:
    raw_urls = re.findall(
        r'https?://[^\s<>"\'\)]+\.(?:png|jpe?g|gif|webp|svg|mp4|mov|webm)(?:\?[^\s<>"\'\)]*)?',
        text,
        re.IGNORECASE,
    )
    return list(dict.fromkeys(raw_urls))

async def verify_media_urls_reachable(session: aiohttp.ClientSession, urls: list[str]) -> list[str]:
    """Проверяет реальную сетевую доступность медиа-вложений (HTTP 200/202/206/301/302)."""
    dead_urls = []
    for url in urls:
        try:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=5.0), allow_redirects=True) as resp:
                if resp.status not in (200, 202, 206, 301, 302):
                    # Некоторые серверы блокируют HEAD, пробуем быстрый GET с Range
                    async with session.get(url, headers={"Range": "bytes=0-100"}, timeout=aiohttp.ClientTimeout(total=5.0)) as get_resp:
                        if get_resp.status not in (200, 202, 206, 301, 302):
                            dead_urls.append(f"{url} (HTTP {get_resp.status})")
        except Exception as e:
            dead_urls.append(f"{url} ({e})")
    return dead_urls

async def run_single_rag_test(case: RAGTestCase, session_id: Optional[int] = None, max_retries: int = 1) -> RAGTestResult:
    if session_id is None:
        session_id = int(time.time() * 1000) % 900000 + abs(hash(case.id)) % 100000

    payload = {
        "text": case.question,
        "sessionId": session_id,
        "chat_id": session_id,
        "category": "test_kr_batch20"
    }

    for attempt in range(max_retries + 1):
        start_time = time.perf_counter()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(N8N_RAG_URL, json=payload, timeout=aiohttp.ClientTimeout(total=case.max_latency_sec + 10)) as resp:
                    latency = round(time.perf_counter() - start_time, 2)
                    status_code = resp.status

                    if status_code in (429, 502, 503, 504) and attempt < max_retries:
                        await asyncio.sleep(2.0)
                        continue

                    if status_code != 200:
                        err_txt = await resp.text()
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
                            source_found=False,
                            error_message=f"HTTP {status_code}: {err_txt[:200]}"
                        )

                    resp_json = await resp.json()
                    response_text = ""
                    if isinstance(resp_json, dict):
                        response_text = resp_json.get("output") or resp_json.get("text") or resp_json.get("response") or str(resp_json)
                    elif isinstance(resp_json, list) and len(resp_json) > 0:
                        first = resp_json[0]
                        if isinstance(first, dict):
                            response_text = first.get("output") or first.get("text") or first.get("response") or str(first)
                        else:
                            response_text = str(first)
                    else:
                        response_text = str(resp_json)

                    # Проверка ключевых слов по группам
                    matched_groups = []
                    missing_groups = []
                    for group in case.expected_keywords_any:
                        group_matched = any(re.search(re.escape(kw), response_text, re.IGNORECASE) for kw in group)
                        if group_matched:
                            matched_groups.append(" | ".join(group))
                        else:
                            missing_groups.append(group)

                    # Проверка запрещенных слов
                    forbidden_found = [
                        fkw for fkw in case.forbidden_keywords
                        if re.search(re.escape(fkw), response_text, re.IGNORECASE)
                    ]

                    # Поиск и валидация медиа
                    found_media = extract_media_from_text(response_text)
                    dead_media = await verify_media_urls_reachable(session, found_media) if found_media else []
                    if dead_media:
                        forbidden_found.append(f"Битая медиа-ссылка: {dead_media}")

                    # Строгая валидация HTML-разметки источника Confluence
                    source_match = re.search(r'📌\s*<b>Источник:</b>\s*<a\s+href="([^"]+)">([^<]+)</a>', response_text, re.IGNORECASE)
                    has_strict_source_tag = bool(source_match)
                    
                    source_found = True
                    source_tag_valid = True
                    if case.is_refusal_test:
                        # При отказе источника и картинок быть НЕ ДОЛЖНО
                        if has_strict_source_tag or "📌" in response_text or len(found_media) > 0:
                            forbidden_found.append("Присутствует ссылка на источник или медиа при отказе")
                    else:
                        # При позитивном ответе ссылка ОБЯЗАТЕЛЬНА в строгом формате HTML
                        if not has_strict_source_tag:
                            source_tag_valid = False
                        else:
                            source_url, source_title = source_match.groups()
                            # Запрет локальных подзаголовков в названии статьи
                            if source_title.strip() in ("Настройка", "Моделирование", "Общие сведения", "Шаг 1", "Шаг 2"):
                                forbidden_found.append(f"Подзаголовок '{source_title}' вместо имени статьи в HTML-ссылке")
                            if case.expected_source_pattern:
                                source_found = bool(re.search(case.expected_source_pattern, response_text, re.IGNORECASE))

                    # Общий вердикт теста
                    passed = (
                        len(missing_groups) == 0 and
                        len(forbidden_found) == 0 and
                        source_found and
                        source_tag_valid and
                        latency <= case.max_latency_sec
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
                        source_found=source_found and source_tag_valid
                    )

        except Exception as e:
            latency = round(time.perf_counter() - start_time, 2)
            if attempt < max_retries:
                await asyncio.sleep(2.0)
                continue
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
                source_found=False,
                error_message=f"Connection error: {e}"
            )

async def main():
    print("=" * 80)
    print("🚀 СТРЕСС-ТЕСТИРОВАНИЕ E2E RAG (30 ТЕСТОВЫХ СИТУАЦИЙ ПО 20 СТАТЬЯМ КР)")
    print(f"Целевой URL n8n: {N8N_RAG_URL}")
    print("=" * 80)

    results: list[RAGTestResult] = []
    
    for idx, case in enumerate(TEST_SUITE_30_BATCH, 1):
        print(f"\n[{idx:02d}/30] ⏳ Тестирование: {case.id} — «{case.name}»...")
        res = await run_single_rag_test(case)
        results.append(res)
        
        status_icon = "✅ PASSED" if res.passed else "❌ FAILED"
        media_info = f"🖼 Медиа: {len(res.found_media)} шт." if res.found_media else "📄 Текст"
        source_info = "🔗 Ссылка OK" if res.source_found else "⚠️ Нет ссылки"
        print(f"       {status_icon} ({res.latency_sec}s) | {media_info} | {source_info}")
        
        if not res.passed:
            if res.missing_groups:
                print(f"       [!] Не найдены группы ключевых слов: {res.missing_groups}")
            if res.forbidden_found:
                print(f"       [!] Обнаружены галлюцинации/запрещенные слова: {res.forbidden_found}")
            if not res.source_found and case.expected_source_pattern:
                print(f"       [!] Не найден первоисточник по паттерну: {case.expected_source_pattern}")
            if res.error_message:
                print(f"       [!] Ошибка выполнения: {res.error_message}")
        
        # Небольшая пауза между запросами для избежания Rate Limits LLM
        await asyncio.sleep(0.8)

    # Итоговая сводка
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    pass_rate = round((passed / total) * 100, 1)
    avg_latency = round(sum(r.latency_sec for r in results) / total, 2)
    total_media_sent = sum(len(r.found_media) for r in results)
    cases_with_media = sum(1 for r in results if len(r.found_media) > 0)

    print("\n" + "=" * 80)
    print("📊 ИТОГОВЫЙ ОТЧЕТ СТРЕСС-ТЕСТИРОВАНИЯ (30 ТЕСТОВ)")
    print("=" * 80)
    print(f"Всего тестов:              {total}")
    print(f"Успешно пройдено:          {passed} / {total} ({pass_rate}%)")
    print(f"Провалено:                 {failed}")
    print(f"Среднее время ответа:      {avg_latency} сек")
    print(f"Тестов с медиа-вложениями: {cases_with_media} из {total}")
    print(f"Всего доставлено медиа:    {total_media_sent} вложений (GIF / PNG)")
    print("=" * 80)

    # Сохраняем полный лог с ответами в Markdown-файл
    report_file = PROJECT_ROOT / "Research" / "test_e2e_30batch_run_log.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# Полный лог результатов стресс-тестирования E2E RAG (30 тестов)\n\n")
        f.write(f"- **Дата запуска**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **Всего тестов**: {total}\n")
        f.write(f"- **Успешно**: {passed} / {total} ({pass_rate}%)\n")
        f.write(f"- **Провалено**: {failed}\n")
        f.write(f"- **Средняя задержка**: {avg_latency} сек\n")
        f.write(f"- **Доставлено медиа**: {total_media_sent} шт. в {cases_with_media} тестах\n\n")
        f.write("---\n\n")
        f.write("## 📝 Детальные протоколы каждого теста\n\n")
        
        for idx, r in enumerate(results, 1):
            status = "🟢 PASSED" if r.passed else "🔴 FAILED"
            f.write(f"### [{idx:02d}/30] {r.case_id}: {r.name} — {status}\n\n")
            f.write(f"- **Вопрос пользователя**: *«{r.question}»*\n")
            f.write(f"- **Время ответа**: `{r.latency_sec} сек` (HTTP {r.status_code})\n")
            f.write(f"- **Медиа-вложения**: {len(r.found_media)} шт. {r.found_media}\n")
            f.write(f"- **Ссылка на источник**: {'✅ Найдена' if r.source_found else '❌ Отсутствует'}\n")
            if not r.passed:
                if r.missing_groups:
                    f.write(f"- **Отсутствующие ключевые группы**: `{r.missing_groups}`\n")
                if r.forbidden_found:
                    f.write(f"- **Обнаруженные галлюцинации**: `{r.forbidden_found}`\n")
                if r.error_message:
                    f.write(f"- **Ошибка**: `{r.error_message}`\n")
            f.write("\n**Полный текст ответа бота:**\n\n")
            f.write(f"> {r.response_text.replace(chr(10), chr(10) + '> ')}\n\n")
            f.write("---\n\n")

    print(f"\n📁 Полный лог с текстами всех 30 ответов сохранен в: {report_file}")

if __name__ == "__main__":
    asyncio.run(main())
