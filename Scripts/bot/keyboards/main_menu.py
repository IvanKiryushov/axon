"""
AxonBot Keyboards Module
Создает двуязычные клавиатуры: главное меню, каталог 15 кейсов,
подачу заявок, смену языка и админские действия.
"""

from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    ReplyKeyboardMarkup, 
    KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from Scripts.bot.utils.i18n import t
from Scripts.bot.utils.examples_catalog import get_categories, get_cases_by_category

def get_main_keyboard(is_authorized: bool = False, lang: str = "ru", is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главная inline-клавиатура бота."""
    builder = InlineKeyboardBuilder()
    
    # Каталог кейсов доступен всем
    builder.row(
        InlineKeyboardButton(text=t("btn_examples", lang), callback_data="menu_examples")
    )
    
    if is_authorized:
        builder.row(
            InlineKeyboardButton(text=t("btn_quota", lang), callback_data="menu_quota"),
            InlineKeyboardButton(text=t("btn_clear", lang), callback_data="clear_chat")
        )
        if is_admin:
            builder.row(
                InlineKeyboardButton(text=t("btn_status", lang), callback_data="menu_status")
            )
    else:
        builder.row(
            InlineKeyboardButton(text=t("btn_quota", lang), callback_data="menu_quota")
        )
        builder.row(
            InlineKeyboardButton(text=t("btn_request_access", lang), callback_data="request_access")
        )
    
    # Кнопка смены языка
    builder.row(
        InlineKeyboardButton(text=t("btn_lang", lang), callback_data="toggle_lang")
    )
    
    return builder.as_markup()

def get_catalog_categories_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура выбора рубрик каталога."""
    builder = InlineKeyboardBuilder()
    categories = get_categories()
    
    for cat_key, cat_data in categories.items():
        title = cat_data["title_ru"] if lang == "ru" else cat_data["title_en"]
        builder.row(
            InlineKeyboardButton(text=title, callback_data=f"cat:{cat_key}")
        )
        
    builder.row(
        InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu_main")
    )
    return builder.as_markup()

def get_category_cases_keyboard(category_key: str, lang: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура со списком вопросов внутри выбранной категории."""
    builder = InlineKeyboardBuilder()
    cases = get_cases_by_category(category_key)
    
    for case in cases:
        title = case.title_ru if lang == "ru" else case.title_en
        builder.row(
            InlineKeyboardButton(text=f"• {title}", callback_data=f"case:{case.id}")
        )
        
    builder.row(
        InlineKeyboardButton(text=t("btn_back_categories", lang), callback_data="menu_examples")
    )
    return builder.as_markup()

def get_cancel_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Reply-клавиатура с кнопкой отмены (Шаг 1)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_cancel", lang))]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_email_step_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Reply-клавиатура шага ввода Email (Шаг 2)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_skip", lang))],
            [KeyboardButton(text=t("btn_cancel", lang))]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_goal_step_keyboard(lang: str = "ru", allow_skip: bool = False) -> ReplyKeyboardMarkup:
    """Reply-клавиатура шага выбора цели (Шаг 2).
    Кнопка 'Пропустить' доступна только для пользователей внешних каналов (allow_skip=True).
    Для прямого трафика (direct) заполнение обоих шагов обязательно.
    """
    rows = [
        [KeyboardButton(text=t("goal_company", lang)), KeyboardButton(text=t("goal_upwork", lang))],
        [KeyboardButton(text=t("goal_testing", lang)), KeyboardButton(text=t("goal_other", lang))],
    ]
    if allow_skip:
        rows.append([KeyboardButton(text=t("btn_skip", lang)), KeyboardButton(text=t("btn_cancel", lang))])
    else:
        rows.append([KeyboardButton(text=t("btn_cancel", lang))])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_contact_request_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Reply-клавиатура с кнопкой отправки контакта (для обратной совместимости)."""
    return get_goal_step_keyboard(lang, allow_skip=False)

def get_admin_request_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для администратора для решения по заявке соискателя."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{request_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{request_id}")
    )
    return builder.as_markup()

def get_quota_exhausted_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура экрана исчерпания квоты."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("btn_request_access", lang), callback_data="request_access")
    )
    builder.row(
        InlineKeyboardButton(text=t("btn_lang", lang), callback_data="toggle_lang")
    )
    return builder.as_markup()

def get_whitelist_paged_keyboard(users: list[dict], page: int, total_count: int, page_size: int = 5) -> InlineKeyboardMarkup:
    """
    Клавиатура Master View в /whitelist:
    Список пользователей формируется прямо в виде кнопок (1 кнопка = 1 пользователь),
    внизу ряд пагинации: [◀️ Назад] [Стр. X/Y] [Вперед ▶️].
    """
    builder = InlineKeyboardBuilder()
    
    for u in users:
        used = u.get("queries_used") or 0
        raw_lim = u.get("queries_limit")
        lim_str = "∞" if (u.get("role") == "admin" or raw_lim == -1) else str(raw_lim if raw_lim is not None else (15 if u.get("role") == "guest" else 100))
        status_flag = " (Исчерпан)" if (lim_str != "∞" and used >= int(lim_str)) else ""
        name = (u.get("full_name") or u.get("username") or str(u["user_id"]))[:16]
        
        btn_text = f"{name} • {used}/{lim_str}{status_flag}"
        builder.row(
            InlineKeyboardButton(text=btn_text, callback_data=f"wlu:{u['user_id']}:{page}")
        )
    
    # Ряд пагинации
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"wlp:{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"Стр. {page + 1}/{total_pages}", callback_data="wl_noop"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"wlp:{page + 1}"))
    
    builder.row(*nav_buttons)
    builder.row(InlineKeyboardButton(text="◀️ В главное меню", callback_data="menu_main"))
    return builder.as_markup()

def get_user_detail_keyboard(user_id: int, page: int = 0, is_target_admin: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура Detail View для управления конкретным пользователем."""
    builder = InlineKeyboardBuilder()
    
    # Ряд 1: Быстрое изменение квоты (+15, -15, Дефолт 15)
    builder.row(
        InlineKeyboardButton(text="➕ +15", callback_data=f"qa:{user_id}:{page}"),
        InlineKeyboardButton(text="➖ -15", callback_data=f"qm:{user_id}:{page}"),
        InlineKeyboardButton(text="🎯 Дефолт 15", callback_data=f"qd:{user_id}:{page}")
    )
    
    # Ряд 2: Безлимит и Сброс квоты
    builder.row(
        InlineKeyboardButton(text="♾ Безлимит", callback_data=f"qu:{user_id}:{page}"),
        InlineKeyboardButton(text="🔄 Сброс квоты", callback_data=f"qr:{user_id}:{page}")
    )
    
    # Ряд 3: Управление правами (только если это не админ)
    if not is_target_admin:
        builder.row(
            InlineKeyboardButton(text="🚫 Отозвать", callback_data=f"wlr:{user_id}:{page}"),
            InlineKeyboardButton(text="❌ Сбросить аккаунт", callback_data=f"wld:{user_id}:{page}")
        )
    
    # Ряд 4: Возврат в список
    builder.row(
        InlineKeyboardButton(text="◀️ Назад к списку", callback_data=f"wlp:{page}")
    )
    return builder.as_markup()