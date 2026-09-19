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
    
    if is_authorized:
        builder.row(
            InlineKeyboardButton(text=t("btn_examples", lang), callback_data="menu_examples")
        )
        if is_admin:
            builder.row(
                InlineKeyboardButton(text=t("btn_clear", lang), callback_data="clear_chat"),
                InlineKeyboardButton(text=t("btn_status", lang), callback_data="menu_status")
            )
        else:
            builder.row(
                InlineKeyboardButton(text=t("btn_clear", lang), callback_data="clear_chat")
            )
    else:
        builder.row(
            InlineKeyboardButton(text=t("btn_request_access", lang), callback_data="request_access")
        )
        builder.row(
            InlineKeyboardButton(text=t("btn_examples", lang), callback_data="menu_examples")
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

def get_goal_step_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Reply-клавиатура шага выбора цели (Шаг 2)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("goal_company", lang)), KeyboardButton(text=t("goal_upwork", lang))],
            [KeyboardButton(text=t("goal_testing", lang)), KeyboardButton(text=t("goal_other", lang))],
            [KeyboardButton(text=t("btn_skip", lang)), KeyboardButton(text=t("btn_cancel", lang))]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_contact_request_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Reply-клавиатура с кнопкой отправки контакта (для обратной совместимости)."""
    return get_goal_step_keyboard(lang)

def get_admin_request_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для администратора для решения по заявке соискателя."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{request_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{request_id}")
    )
    return builder.as_markup()