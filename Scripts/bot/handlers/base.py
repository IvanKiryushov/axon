"""
AxonBot Base Handlers
Основной роутер бота: команды /start, /examples, /clear, /status, /reboot,
навигация по каталогу 15 эталонных кейсов, переключение языка и общий RAG-обработчик.
"""

import os
import sys
import re
import logging
import aiohttp
import subprocess
from datetime import datetime
from pathlib import Path
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import BufferedInputFile, InputMediaPhoto
from aiogram.utils.chat_action import ChatActionSender

from Scripts.bot.keyboards.main_menu import (
    get_main_keyboard,
    get_catalog_categories_keyboard,
    get_category_cases_keyboard
)
from Scripts.bot.utils.n8n_client import call_n8n_rag, reset_user_session
from Scripts.bot.utils.conversation_logger import log_conversation, log_reset
from Scripts.bot.utils.html_formatter import markdown_to_telegram_html
from Scripts.bot.utils.i18n import t
from Scripts.bot.utils.examples_catalog import (
    get_categories,
    get_cases_by_category,
    get_case_by_id
)
from Scripts.bot.utils.access_db import (
    is_authorized,
    is_admin,
    get_user_language,
    set_user_language,
    get_or_create_user,
    get_user_quota,
    refund_quota
)
from Scripts.bot.config import (
    ADMIN_ID, 
    CONFLUENCE_EMAIL, 
    CONFLUENCE_API_TOKEN,
    CONFLUENCE_BASE_URL,
    CONFLUENCE_PUBLIC_URL,
    CONFLUENCE_INVITE_LINK,
    DEFAULT_DEMO_QUOTA,
    DEFAULT_AUTH_QUOTA,
    EXEMPT_USER_IDS
)

router = Router()
MEDIA_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "media_cache"

def build_welcome_text(user_name: str, lang: str, authorized: bool) -> str:
    """Генерирует приветственное сообщение с учетом прав доступа и инвайт-ссылки Confluence."""
    title = t("welcome_title", lang)
    desc = t(
        "welcome_desc" if authorized else "guest_welcome_desc",
        lang,
        name=user_name,
        invite_link=CONFLUENCE_INVITE_LINK
    )
    return f"{title}\n\n{desc}"

async def fetch_media_bytes(session: aiohttp.ClientSession, url: str) -> tuple[bytes, str] | None:
    """
    Скачивает медиафайл. Для Confluence Cloud использует официальный REST API v1 с Basic Auth.
    Для GIF проверяет наличие готового легковесного MP4 в media_cache.
    """
    filename = url.split('/')[-1].split('?')[0] or "image.png"
    stem = Path(filename).stem
    ext = Path(filename.lower()).suffix

    # 0. Проверяем локальный кэш (для GIF, оптимизированных в MP4 H.264)
    if ext == '.gif':
        cached_mp4 = MEDIA_CACHE_DIR / f"{stem}.mp4"
        if cached_mp4.exists() and cached_mp4.stat().st_size > 0:
            try:
                data = cached_mp4.read_bytes()
                return data, f"{stem}.mp4"
            except Exception as e:
                logging.warning(f"Ошибка чтения media_cache ({cached_mp4}): {e}")

    auth = aiohttp.BasicAuth(CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN) if CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN else None

    # Проверяем, является ли URL ссылкой на вложение Confluence Cloud
    confluence_match = re.search(r'(https?://[^/]+)/wiki/(?:download/)?attachments/(\d+)/([^/?#]+)', url)
    if confluence_match:
        base_domain = confluence_match.group(1)
        page_id = confluence_match.group(2)
        target_filename = confluence_match.group(3)

        try:
            meta_url = f"{base_domain}/wiki/rest/api/content/{page_id}/child/attachment?filename={target_filename}"
            async with session.get(meta_url, auth=auth, timeout=aiohttp.ClientTimeout(total=10)) as meta_resp:
                if meta_resp.status == 200:
                    meta_data = await meta_resp.json()
                    results = meta_data.get('results', [])
                    if results and '_links' in results[0] and 'download' in results[0]['_links']:
                        dl_path = results[0]['_links']['download']
                        dl_url = f"{base_domain}/wiki{dl_path}" if not dl_path.startswith('/wiki') else f"{base_domain}{dl_path}"
                        
                        async with session.get(dl_url, auth=auth, timeout=aiohttp.ClientTimeout(total=20)) as dl_resp:
                            if dl_resp.status == 200:
                                data = await dl_resp.read()
                                return data, filename
                            else:
                                logging.warning(f"REST API download returned {dl_resp.status} for {dl_url}")
        except Exception as e:
            logging.error(f"Ошибка получения вложения Confluence через REST API: {e}")

    # Fallback: прямой GET запрос
    try:
        async with session.get(url, auth=auth, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.read()
                return data, filename
            else:
                logging.warning(f"Не удалось загрузить медиа ({resp.status}): {url}")
    except Exception as e:
        logging.error(f"Ошибка загрузки медиа {url}: {e}")

    return None

import io
from PIL import Image

def get_media_dimensions(data: bytes) -> tuple[int, int] | None:
    """Извлекает оригинальное разрешение для Telegram."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:
        return None

async def send_confluence_media_batch(bot, chat_id: int, urls: list[str]):
    """Скачивает и отправляет медиафайлы из базы знаний в Telegram."""
    if not urls:
        return

    auth = None
    if CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN:
        auth = aiohttp.BasicAuth(login=CONFLUENCE_EMAIL, password=CONFLUENCE_API_TOKEN)

    photos_to_group = []
    animations = []
    videos = []

    async with aiohttp.ClientSession(auth=auth) as session:
        for url in urls[:20]:
            result = await fetch_media_bytes(session, url)
            if result:
                data, filename = result
                dims = get_media_dimensions(data)
                ext = Path(filename.lower()).suffix
                if ext == '.gif':
                    animations.append((data, filename, dims))
                elif ext == '.mp4' and (MEDIA_CACHE_DIR / filename).exists():
                    animations.append((data, filename, dims))
                elif ext in ['.mp4', '.mov', '.webm']:
                    videos.append((data, filename))
                else:
                    input_file = BufferedInputFile(data, filename=filename)
                    photos_to_group.append((input_file, filename, dims))

    if photos_to_group:
        for i in range(0, len(photos_to_group), 10):
            chunk = photos_to_group[i:i+10]
            if len(chunk) > 1:
                media_group = [InputMediaPhoto(media=inp) for inp, fn, dims in chunk]
                try:
                    await bot.send_media_group(chat_id=chat_id, media=media_group)
                except Exception as e:
                    logging.error(f"Ошибка отправки медиагруппы: {e}")
                    for inp, fn, dims in chunk:
                        await bot.send_photo(chat_id=chat_id, photo=inp)
            else:
                inp, fn, dims = chunk[0]
                await bot.send_photo(chat_id=chat_id, photo=inp)

    for data, filename, dims in animations:
        inp = BufferedInputFile(data, filename=filename)
        try:
            await bot.send_animation(chat_id=chat_id, animation=inp, request_timeout=120)
        except Exception as e:
            logging.error(f"Ошибка отправки анимации ({filename}): {e}")

    for data, filename in videos:
        try:
            inp = BufferedInputFile(data, filename=filename)
            await bot.send_video(chat_id=chat_id, video=inp, request_timeout=120)
        except Exception as e:
            logging.error(f"Ошибка отправки видео: {e}")

async def execute_rag_pipeline(bot, chat_id: int, user_id: int, user_name: str, query_text: str, lang: str, quota_info: dict | None = None):
    """Единая функция выполнения RAG-пайплайна, маскирования ссылок и отправки ответа."""
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
            response_text = await call_n8n_rag(
                user_text=query_text,
                chat_id=chat_id,
                category="general"
            )
    except Exception as e:
        logging.error(f"Сбой выполнения RAG пайплайна для {user_id}: {e}, производим возврат квоты")
        await refund_quota(user_id)
        raise e

    # Если n8n вернул ошибку сервиса — возвращаем квоту пользователю
    if not response_text or "Ошибка связи с RAG-сервером" in response_text:
        await refund_quota(user_id)

    log_conversation(
        user_id=user_id,
        user_name=user_name,
        question=query_text,
        answer=response_text
    )

    # Тихое уведомление администратору об активности пользователя (если спрашивает не сам админ)
    if ADMIN_ID and user_id != ADMIN_ID:
        try:
            admin_notify = (
                f"<b>Активность пользователя:</b>\n"
                f"• <b>Пользователь:</b> {user_name} (ID: <code>{user_id}</code>)\n"
                f"• <b>Вопрос:</b> {query_text}\n"
                f"• <b>Время:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_notify,
                parse_mode="HTML",
                disable_notification=True
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить тихое уведомление администратору: {e}")

    # Поиск ссылок на медиафайлы
    raw_urls = re.findall(r"https?://[^\s<>'\"\)]+\.(?:png|jpe?g|gif|webp|svg|mp4|mov|webm)(?:\?[^\s<>'\"\)]*)?", response_text, re.IGNORECASE)
    media_urls = list(dict.fromkeys(raw_urls))

    # Очистка и нормализация текста
    clean_text = response_text
    clean_text = re.sub(r'🖼\s*\[[^\]]*\]\([^\)]+\)', '', clean_text)
    clean_text = re.sub(r'🎥\s*\[[^\]]*\]\([^\)]+\)', '', clean_text)
    clean_text = re.sub(r'!\[[^\]]*\]\([^\)]+\)', '', clean_text)
    clean_text = re.sub(r'\[(?:Изображение|Image|Схема|Медиа|Media|Видео|Video|Анимация|Animation)[^\]]*\]\([^\)]+\)', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'(?:Вложения|Медиа|Media|Изображения|Images?|Видео|Videos?)[^\n]*:\s*\n?(?:-\s*https?://[^\s]+\n?)+', '', clean_text, flags=re.IGNORECASE)
    for u in media_urls:
        clean_text = clean_text.replace(u, '')

    # OpSec: Обезличивание Confluence-домена и ссылок при цитировании источников
    def sanitize_confluence_url(match):
        full_url = match.group(0)
        page_id_match = re.search(r'pages/(\d+)', full_url)
        space_match = re.search(r'spaces/([A-Za-z0-9_-]+)', full_url)
        space_key = space_match.group(1) if space_match and not space_match.group(1).startswith('~') else "MFS"
        if page_id_match:
            return f"{CONFLUENCE_PUBLIC_URL}/wiki/spaces/{space_key}/pages/{page_id_match.group(1)}"
        return f"{CONFLUENCE_PUBLIC_URL}/wiki"

    # Заменяем любые персональные ссылки Atlassian Confluence на чистый нейтральный адрес
    clean_text = re.sub(
        r'https?://[a-zA-Z0-9_\-\.]+\.atlassian\.net[^\s\)\"\'>]*',
        sanitize_confluence_url,
        clean_text
    )

    # Жесткая санитарная зачистка персональных идентификаторов в тексте
    clean_text = re.sub(r'ivankiryushov[a-zA-Z0-9_\-]*', 'axonbim', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'Иван\s+Кирюшов', 'BIM Team', clean_text, flags=re.IGNORECASE)

    # Защита от ложной атрибуции при отказе (Grounding Guard): при отказе ссылка на источник недопустима
    refusal_markers = [
        "нет информации",
        "не регламентир",
        "no guideline",
        "no guidelines",
        "no information",
        "not covered",
        "out of scope",
        "consult with the bim",
        "refer to the bim",
        "bim department",
        "any deviations or custom solutions must be approved"
    ]
    if any(rm in clean_text.lower() for rm in refusal_markers):
        clean_text = re.sub(r'📌\s*<b>(?:Источник|Source):</b>\s*<a\s+href=[^>]+>[^<]+</a>', '', clean_text, flags=re.IGNORECASE).strip()
        clean_text = re.sub(r'📌\s*(?:Источник|Source):[^\n]+', '', clean_text, flags=re.IGNORECASE).strip()

    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()

    try:
        formatted_html = markdown_to_telegram_html(clean_text)
        await bot.send_message(chat_id=chat_id, text=formatted_html, parse_mode="HTML")
    except Exception as e:
        logging.warning(f"Ошибка HTML-парсинга: {e}, отправляем обычный текст")
        await bot.send_message(chat_id=chat_id, text=clean_text)

    if media_urls:
        async with ChatActionSender.upload_photo(bot=bot, chat_id=chat_id):
            await send_confluence_media_batch(bot=bot, chat_id=chat_id, urls=media_urls)

    # Предупреждение о скором исчерпании лимита (если осталось <= 2)
    if quota_info:
        remaining = quota_info.get("remaining")
        limit = quota_info.get("limit")
        if remaining is not None and remaining <= 2:
            warn_msg = t("quota_warning_separate", lang, remaining=remaining, limit=limit)
            try:
                await bot.send_message(chat_id=chat_id, text=warn_msg)
            except Exception as e:
                logging.warning(f"Не удалось отправить сервисное предупреждение о квоте: {e}")

@router.message(Command("reboot"))
async def cmd_reboot(message: types.Message):
    """Админская команда на перезагрузку бота."""
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer("Система получила сигнал на перезапуск. Выполняется перезагрузка...")
    subprocess.Popen([sys.executable, "-m", "Scripts.bot.main", "--reboot", str(message.chat.id)])
    sys.exit()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """Приветственное меню бота с поддержкой Deep Linking рефералов."""
    user = message.from_user
    lang = await get_user_language(user.id)
    authorized = await is_authorized(user.id)
    admin = await is_admin(user.id)
    
    welcome_text = build_welcome_text(user.full_name, lang, authorized)
    
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(is_authorized=authorized, lang=lang, is_admin=admin),
        parse_mode="HTML"
    )

@router.message(Command("examples"))
@router.callback_query(F.data == "menu_examples")
async def cmd_examples_menu(event: types.Message | types.CallbackQuery):
    """Вывод рубрикатора каталога 15 проверенных кейсов."""
    user = event.from_user
    lang = await get_user_language(user.id)
    text = t("catalog_title", lang)
    keyboard = get_catalog_categories_keyboard(lang)
    
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("cat:"))
async def handle_category_select(callback: types.CallbackQuery):
    """Вывод списка вопросов внутри выбранной рубрики."""
    cat_key = callback.data.split(":")[1]
    lang = await get_user_language(callback.from_user.id)
    categories = get_categories()
    cat_info = categories.get(cat_key, {})
    cat_name = cat_info.get(f"title_{lang}", cat_key)
    
    text = t("category_cases_title", lang, category=cat_name)
    keyboard = get_category_cases_keyboard(cat_key, lang)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("case:"))
async def handle_case_select(callback: types.CallbackQuery, quota_info: dict | None = None):
    """Отправка выбранного эталонного кейса в RAG-пайплайн."""
    case_id = callback.data.split(":")[1]
    user = callback.from_user
    lang = await get_user_language(user.id)

    case = get_case_by_id(case_id)
    if not case:
        await callback.answer("Кейс не найден.", show_alert=True)
        return

    # Если осталось <= 2 запросов — выдаем нативный модальный алерт Telegram без спама в чат
    if quota_info:
        remaining = quota_info.get("remaining")
        limit = quota_info.get("limit")
        if remaining is not None and remaining <= 2:
            await callback.answer(t("quota_warning_popup", lang, remaining=remaining, limit=limit), show_alert=True)
        else:
            await callback.answer()
    else:
        await callback.answer()

    query_text = case.question_en if lang == "en" else case.question_ru
    prefix = "<b>Benchmark Case:</b>" if lang == "en" else "<b>Кейс:</b>"
    await callback.message.answer(f"{prefix}\n<i>«{query_text}»</i>", parse_mode="HTML")
    
    await execute_rag_pipeline(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        user_id=user.id,
        user_name=user.full_name,
        query_text=query_text,
        lang=lang,
        quota_info=quota_info
    )

@router.callback_query(F.data == "toggle_lang")
async def handle_toggle_language(callback: types.CallbackQuery):
    """Интерактивное переключение языка интерфейса (RU / EN)."""
    user = callback.from_user
    current_lang = await get_user_language(user.id)
    new_lang = "en" if current_lang == "ru" else "ru"
    await set_user_language(user.id, new_lang)
    
    alert_msg = "Язык интерфейса: Русский" if new_lang == "ru" else "Interface language: English"
    await callback.answer(alert_msg)
    
    authorized = await is_authorized(user.id)
    admin = await is_admin(user.id)
    welcome_text = build_welcome_text(user.full_name, new_lang, authorized)
    
    await callback.message.edit_text(
        welcome_text,
        reply_markup=get_main_keyboard(is_authorized=authorized, lang=new_lang, is_admin=admin),
        parse_mode="HTML"
    )

@router.message(Command("lang", "language"))
async def cmd_lang(message: types.Message):
    """Интерактивное переключение языка интерфейса (RU / EN) по команде."""
    user = message.from_user
    current_lang = await get_user_language(user.id)
    new_lang = "en" if current_lang == "ru" else "ru"
    await set_user_language(user.id, new_lang)
    
    authorized = await is_authorized(user.id)
    admin = await is_admin(user.id)
    welcome_text = build_welcome_text(user.full_name, new_lang, authorized)
    
    confirm_msg = "Язык интерфейса: Русский" if new_lang == "ru" else "Interface language: English"
    await message.answer(
        f"<b>{confirm_msg}</b>\n\n{welcome_text}",
        reply_markup=get_main_keyboard(is_authorized=authorized, lang=new_lang, is_admin=admin),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "menu_main")
async def handle_back_to_menu(callback: types.CallbackQuery):
    """Возврат в главное меню."""
    user = callback.from_user
    lang = await get_user_language(user.id)
    authorized = await is_authorized(user.id)
    admin = await is_admin(user.id)
    welcome_text = build_welcome_text(user.full_name, lang, authorized)
    
    await callback.message.edit_text(
        welcome_text,
        reply_markup=get_main_keyboard(is_authorized=authorized, lang=lang, is_admin=admin),
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(Command("clear", "reset"))
@router.callback_query(F.data == "clear_chat")
async def cmd_clear(event: types.Message | types.CallbackQuery):
    """Сброс контекста диалога в n8n Memory."""
    chat_id = event.chat.id if isinstance(event, types.Message) else event.message.chat.id
    user = event.from_user
    lang = await get_user_language(user.id)
    
    reset_user_session(chat_id)
    log_reset(user_id=user.id, user_name=user.full_name)
    
    msg_text = t("memory_cleared", lang)
    if isinstance(event, types.CallbackQuery):
        await event.message.answer(msg_text, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(msg_text, parse_mode="HTML")

@router.message(Command("status"))
@router.callback_query(F.data == "menu_status")
async def cmd_status(event: types.Message | types.CallbackQuery):
    """Вывод статуса системы и версии RAG (только для администратора)."""
    user = event.from_user
    if not await is_admin(user.id):
        if isinstance(event, types.CallbackQuery):
            await event.answer("Команда доступна только администратору.", show_alert=True)
        return

    lang = await get_user_language(user.id)
    role_str = t("role_admin", lang)
    text = t("status_text", lang, role=role_str)
    if isinstance(event, types.CallbackQuery):
        await event.message.answer(text, reply_markup=get_main_keyboard(is_authorized=True, lang=lang, is_admin=True), parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=get_main_keyboard(is_authorized=True, lang=lang, is_admin=True), parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    """Справка по использованию бота."""
    user = message.from_user
    lang = await get_user_language(user.id)
    authorized = await is_authorized(user.id)
    admin = await is_admin(user.id)
    welcome_text = build_welcome_text(user.full_name, lang, authorized)
    await message.answer(welcome_text, reply_markup=get_main_keyboard(is_authorized=authorized, lang=lang, is_admin=admin), parse_mode="HTML")

@router.message(F.voice | F.audio | F.video | F.video_note)
async def handle_media_prohibited(message: types.Message):
    """Блокировка голосовых сообщений, аудио, видео и видео-заметок."""
    lang = await get_user_language(message.from_user.id)
    text = (
        "Бот принимает только текстовые сообщения."
        if lang == "ru"
        else "The bot only accepts text messages."
    )
    await message.answer(text)

@router.message(Command("quota", "balance", "limit"))
@router.callback_query(F.data == "menu_quota")
async def cmd_quota(event: types.Message | types.CallbackQuery):
    """Отображение карточки баланса и лимита запросов пользователя."""
    user = event.from_user
    lang = await get_user_language(user.id)
    authorized = await is_authorized(user.id)
    admin = await is_admin(user.id)
    is_exempt = bool(user.id == ADMIN_ID or user.id in EXEMPT_USER_IDS)
    
    default_lim = DEFAULT_AUTH_QUOTA if authorized else DEFAULT_DEMO_QUOTA
    q_data = await get_user_quota(user.id, default_limit=default_lim)
    
    used = q_data["used"]
    limit = q_data["limit"]
    is_unlim = q_data["is_unlimited"] or is_exempt or admin
    
    if is_unlim:
        mode_str = t("mode_admin", lang) if admin else t("mode_unlimited", lang)
        used_str = str(used)
        limit_str = t("unlimited_text", lang)
        rem_str = t("unlimited_text", lang)
        cta_note = t("quota_cta_full", lang)
    elif authorized:
        mode_str = t("mode_authorized", lang)
        used_str = str(used)
        limit_str = str(limit)
        rem_str = f"{q_data['remaining']} запросов" if lang == "ru" else f"{q_data['remaining']} queries"
        cta_note = t("quota_cta_full", lang)
    else:
        mode_str = t("mode_guest", lang)
        used_str = str(used)
        limit_str = str(limit)
        rem_str = f"{q_data['remaining']} запросов" if lang == "ru" else f"{q_data['remaining']} queries"
        cta_note = t("quota_cta_request", lang)
        
    card_text = t(
        "quota_info_card",
        lang,
        mode=mode_str,
        used=used_str,
        limit=limit_str,
        remaining=rem_str,
        cta_note=cta_note
    )
    
    kb = get_main_keyboard(is_authorized=authorized, lang=lang, is_admin=admin)
    if isinstance(event, types.CallbackQuery):
        await event.message.answer(card_text, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(card_text, reply_markup=kb, parse_mode="HTML")

@router.message(F.text)
async def handle_message(message: types.Message, quota_info: dict | None = None):
    """Общий обработчик произвольных текстовых вопросов."""
    user = message.from_user
    lang = await get_user_language(user.id)
    
    await execute_rag_pipeline(
        bot=message.bot,
        chat_id=message.chat.id,
        user_id=user.id,
        user_name=user.full_name,
        query_text=message.text,
        lang=lang,
        quota_info=quota_info
    )

@router.message(~F.text)
async def handle_non_text_message(message: types.Message):
    """Ответ на остальные нетекстовые сообщения (стикеры, вложения документов)."""
    lang = await get_user_language(message.from_user.id)
    text = (
        "Пожалуйста, задайте текстовый вопрос по базе регламентов Revit КР или выберите вопрос в /examples."
        if lang == "ru"
        else "Please send a text question about Revit KR standards or select a case in /examples."
    )
    await message.answer(text)