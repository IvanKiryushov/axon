"""
Unit tests for Scripts.bot.utils.i18n
Verifies dictionary completeness and parameter interpolation between RU and EN.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Scripts.bot.utils.i18n import MESSAGES, t

def test_i18n_dictionary_symmetry():
    ru_keys = set(MESSAGES["ru"].keys())
    en_keys = set(MESSAGES["en"].keys())
    
    missing_in_en = ru_keys - en_keys
    missing_in_ru = en_keys - ru_keys
    
    assert not missing_in_en, f"Keys present in RU but missing in EN: {missing_in_en}"
    assert not missing_in_ru, f"Keys present in EN but missing in RU: {missing_in_ru}"

def test_i18n_formatting():
    # Test formatting with variables
    ru_welcome = t("welcome_desc", "ru", name="Иван")
    assert "Иван" in ru_welcome
    
    en_welcome = t("welcome_desc", "en", name="John")
    assert "John" in en_welcome
    
    # Test status
    ru_status = t("status_text", "ru", role="Admin")
    assert "Admin" in ru_status
    
    en_status = t("status_text", "en", role="Guest")
    assert "Guest" in en_status
    
    # Test category cases title
    cat_ru = t("category_cases_title", "ru", category="Сваи")
    assert "Сваи" in cat_ru
    
    cat_en = t("category_cases_title", "en", category="Piles")
    assert "Piles" in cat_en

def test_i18n_funnel_and_media_keys():
    # Test strict text messages
    assert t("only_text_accepted", "ru") == "Бот принимает только текстовые сообщения."
    assert t("only_text_accepted", "en") == "The bot only accepts text messages."
    
    # Test funnel prompts
    assert "Confluence" in t("request_step2_email", "ru")
    assert "Confluence" in t("request_step2_email", "en")
    assert "СпецПроект" in t("request_step1_company", "ru")
    assert "SpecProject" in t("request_step1_company", "en")
    
    # Test Confluence approval push
    link = "https://id.atlassian.com/invite/test"
    ru_push = t("request_approved_confluence_push", "ru", invite_link=link)
    en_push = t("request_approved_confluence_push", "en", invite_link=link)
    assert link in ru_push
    assert link in en_push
    assert "Confluence" in ru_push
    assert "Confluence" in en_push

def test_no_emojis_in_system_message_bodies():
    """Проверяет отсутствие эмодзи во всех текстовых телах системных сообщений."""
    import re
    # Разрешенные кнопки навигации и управления
    ALLOWED_BUTTON_KEYS = {
        "btn_lang", "btn_back", "btn_back_categories", 
        "btn_share_contact", "btn_cancel", "btn_skip"
    }
    
    # Регулярное выражение для популярных диапазонов эмодзи
    emoji_pattern = re.compile(
        "[\U00010000-\U0010ffff"
        "\u2600-\u26ff"
        "\u2700-\u27bf"
        "\u2b50-\u2b55"
        "\u231a-\u23f3"
        "]+", 
        flags=re.UNICODE
    )
    
    for lang in ("ru", "en"):
        for key, text in MESSAGES[lang].items():
            if key in ALLOWED_BUTTON_KEYS:
                continue
            matches = emoji_pattern.findall(text)
            assert not matches, f"Emoji {matches} found in message body '{key}' ({lang}): {text}"