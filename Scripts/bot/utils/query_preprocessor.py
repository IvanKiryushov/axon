"""
Модуль предварительной обработки и нормализации запросов пользователей по разделу КР.
Выполняет детерминированное снятие омонимии со сленга проектировщиков (ГК, ВК)
без засорения системных промптов LLM словарями и списками сокращений.
"""
import re

# Регулярные выражения для снятия омонимии терминов КР (регламент 56432052)
RE_GK = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])ГК(?![A-Za-zА-Яа-я0-9_])', re.IGNORECASE)
RE_VK = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])(?<!ОВ/)(?<!ОВ и )ВК(?![A-Za-zА-Яа-я0-9_])', re.IGNORECASE)
RE_MEP_CHECK = re.compile(r'\b(?:ОВ\s*/\s*ВК|ОВ\s+и\s+ВК)\b', re.IGNORECASE)

RE_FP = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])ФП(?![A-Za-zА-Яа-я0-9_])', re.IGNORECASE)
RE_FO = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])ФО(?![A-Za-zА-Яа-я0-9_])', re.IGNORECASE)
RE_LP = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])Лп(?![A-Za-zА-Яа-я0-9_])')
RE_LM = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])Лм(?![A-Za-zА-Яа-я0-9_])')
RE_STLB = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])СТЛБ(?![A-Za-zА-Яа-я0-9_])', re.IGNORECASE)
RE_PRK = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])ПРК(?![A-Za-zА-Яа-я0-9_])', re.IGNORECASE)
RE_OPL = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])Опл(?![A-Za-zА-Яа-я0-9_])', re.IGNORECASE)
RE_ARM = re.compile(r'(?<![A-Za-zА-Яа-я0-9_])Арм(?![A-Za-zА-Яа-я0-9_])', re.IGNORECASE)

# Паттерны для мягкого синонимического расширения (без искажения оригинального запроса)
RE_FROGS = re.compile(r'\b(лягушк\w*)\b', re.IGNORECASE)
RE_POGONAJ = re.compile(r'\b(погонаж\w*)\b', re.IGNORECASE)

def preprocess_query(text: str) -> str:
    """
    Нормализует запрос пользователя перед передачей в RAG-пайплайн.
    
    Примеры:
    - "Как именовать ГК?" -> "Как именовать горизонтальные конструкции?"
    - "Какие требования к ВК?" -> "Какие требования к вертикальные конструкции?"
    - "Как армировать ФП?" -> "Как армировать фундаментная плита?"
    - "Вопрос по разделу ОВ/ВК" -> "Вопрос по разделу ОВ/ВК" (не трогаем инженерию)
    """
    if not text:
        return ""

    # Если в вопросе явно фигурирует смежный инженерный раздел ОВ/ВК, оставляем ВК как есть
    is_mep = bool(RE_MEP_CHECK.search(text))
    # Защита: если пользователь спрашивает расшифровку самого термина ("что такое ГК", "сокращение ВК")
    is_def = bool(re.search(r'\b(?:что (?:такое|за)|сокращени|расшифр|значит)\b', text, re.IGNORECASE))

    processed = text
    if not is_def:
        processed = RE_GK.sub("горизонтальные конструкции", processed)
        if not is_mep:
            processed = RE_VK.sub("вертикальные конструкции", processed)
    else:
        processed = f"{processed} (Правила именования КР: общепринятые сокращения ГК ВК)"

    processed = RE_FP.sub("фундаментная плита", processed)
    processed = RE_FO.sub("фундамент оборудования", processed)
    processed = RE_LP.sub("лестничная площадка", processed)
    processed = RE_LM.sub("лестничный марш", processed)
    processed = RE_STLB.sub("стилобат", processed)
    processed = RE_PRK.sub("паркинг", processed)
    processed = RE_OPL.sub("опалубка", processed)
    processed = RE_ARM.sub("армирование", processed)

    # Нормализация разговорного сленга проектировщиков КР
    processed = re.sub(r'\bсборняк(?:а|у|ом|е)?\b', 'сборные конструкции сборные элементы', processed, flags=re.IGNORECASE)
    processed = re.sub(r'\bраскидать(?:\s+по\s+осям)?\b', 'расставить по осям', processed, flags=re.IGNORECASE)
    processed = re.sub(r'\bавтоматом\b', 'автоматически', processed, flags=re.IGNORECASE)
    processed = re.sub(r'\bзеркалить\b', 'зеркалировать', processed, flags=re.IGNORECASE)
    processed = re.sub(r'\bна\s+\d+\s+свай\b', 'свай', processed, flags=re.IGNORECASE)

    # Мягкое синонимическое расширение (сохраняет оригинальный термин и добавляет нормативный синоним)
    # 1. Лягушки -> лягушки (фиксаторы сеток)
    if not re.search(r'фиксатор', processed, re.IGNORECASE):
        processed = RE_FROGS.sub(r'\1 (фиксаторы сеток)', processed)

    # 2. Погонаж -> погонаж (погонные метры)
    if not re.search(r'погонн\w*\s+метр', processed, re.IGNORECASE):
        processed = RE_POGONAJ.sub(r'\1 (погонные метры)', processed)

    return processed