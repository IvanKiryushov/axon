"""
AxonBot Whitelist & Budget Guard Middleware (Aiogram 3)
Обеспечивает изоляцию приватного контура, авторизацию пользователей через SQLite,
перехват Deep Linking реферальных меток, квотирование запросов (Demo Quota & Budget Guard),
Single-Flight Concurrency Lock и защиту от флуда (Anti-Flood Cooldown).
"""

import time
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from Scripts.bot.config import (
    ADMIN_ID,
    DEFAULT_DEMO_QUOTA,
    DEFAULT_AUTH_QUOTA,
    RATE_LIMIT_COOLDOWN,
    GLOBAL_DAILY_DEMO_LIMIT,
    EXEMPT_USER_IDS
)
from Scripts.bot.utils.access_db import (
    get_or_create_user, 
    is_authorized, 
    is_admin, 
    get_user_language, 
    set_user_language,
    update_user_source,
    check_and_consume_quota,
    get_global_daily_demo_count
)
from Scripts.bot.keyboards.main_menu import get_quota_exhausted_keyboard
from Scripts.bot.utils.i18n import t

PUBLIC_COMMANDS = {
    "/start", "/help", "/examples", "/request_access", 
    "/lang", "/language", "/status", "/quota", "/balance", "/limit", "/cancel"
}
PUBLIC_CALLBACK_PREFIXES = {
    "set_lang:", "request_access", "cat:", "menu", "help", 
    "wlu:", "wlp:", "wl_noop", "qa:", "qm:", "qd:", "qu:", "qr:", "wlr:", "wld:"
}
VALID_CHANNELS = {"github", "upwork", "linkedin"}

class WhitelistMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self._in_flight_users: set[int] = set()
        self._last_finished_at: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user: types.User = data.get("event_from_user") or getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        # 1. Извлечение Deep Link источника при старте (/start <ref>)
        deep_link_source = "direct"
        is_start_cmd = isinstance(event, types.Message) and bool(event.text) and event.text.startswith("/start")
        if is_start_cmd:
            parts = event.text.split(maxsplit=1)
            if len(parts) > 1:
                ref_candidate = parts[1].strip().lower()
                for ch in VALID_CHANNELS:
                    if ref_candidate.startswith(ch):
                        deep_link_source = ch
                        break

        # 2. Определение дефолтного языка по клиенту Telegram или deep link
        client_lang = (user.language_code or "ru").lower()
        default_lang = "ru" if client_lang.startswith(("ru", "be", "uk")) else "en"
        if deep_link_source in VALID_CHANNELS:
            default_lang = "en"

        # 3. Синхронизация профиля в SQLite
        user_record = await get_or_create_user(
            user_id=user.id,
            username=user.username or "",
            full_name=user.full_name or "",
            source=deep_link_source,
            language=default_lang
        )

        authorized = await is_authorized(user.id)
        admin = await is_admin(user.id)
        is_exempt = bool(user.id == ADMIN_ID or user.id in EXEMPT_USER_IDS)

        # Если гость пришел по внешнему каналу — принудительно фиксируем источник и английский язык
        if not authorized and not admin and deep_link_source in VALID_CHANNELS:
            await update_user_source(user.id, deep_link_source)
            await set_user_language(user.id, "en")

        user_lang = await get_user_language(user.id)

        # Пробрасываем контекст в data для хэндлеров
        data["user_lang"] = user_lang
        data["is_authorized"] = authorized
        data["is_admin"] = admin
        data["is_exempt"] = is_exempt

        # 4. Классификация события: является ли это запросом к RAG?
        is_rag_query = False
        
        if isinstance(event, types.Message):
            # Пропускаем медиасообщения для выдачи регламентного ответа о текстовом формате
            if event.voice or event.audio or event.video or event.video_note:
                return await handler(event, data)

            if event.contact:
                return await handler(event, data)

            state = data.get("state")
            if state:
                current_state = await state.get_state()
                if current_state and "access" in current_state.lower():
                    return await handler(event, data)

            if event.text:
                cmd = event.text.split()[0].lower()
                if cmd in PUBLIC_COMMANDS or cmd.startswith("/"):
                    return await handler(event, data)
                is_rag_query = True

        elif isinstance(event, types.CallbackQuery):
            cb_data = event.data or ""
            if cb_data.startswith("case:"):
                is_rag_query = True
            elif (
                any(cb_data.startswith(p) for p in PUBLIC_CALLBACK_PREFIXES) 
                or cb_data in ("toggle_lang", "menu_examples", "clear_chat", "menu_main", "menu_quota", "wl_noop")
            ):
                return await handler(event, data)
            elif cb_data.startswith(("approve:", "reject:")):
                return await handler(event, data)
            elif admin:
                return await handler(event, data)

        # Если это не RAG-запрос — немедленно передаем управление дальше
        if not is_rag_query:
            return await handler(event, data)

        # 5. RAG & Budget Guard: Single-Flight Lock, Cooldown, Quota Check
        # 5.1. Single-Flight Lock: строгий запрет параллельных запросов от одного пользователя
        if user.id in self._in_flight_users:
            if isinstance(event, types.Message):
                await event.answer(t("single_flight_warning", user_lang))
            elif isinstance(event, types.CallbackQuery):
                await event.answer(t("single_flight_toast", user_lang), show_alert=False)
            return

        # 5.2. Anti-Flood Cooldown: обязательная пауза RATE_LIMIT_COOLDOWN секунд после ответа
        if not is_exempt and user.id in self._last_finished_at:
            elapsed = time.time() - self._last_finished_at[user.id]
            if elapsed < RATE_LIMIT_COOLDOWN:
                if isinstance(event, types.Message):
                    await event.answer(t("cooldown_warning", user_lang))
                elif isinstance(event, types.CallbackQuery):
                    await event.answer(t("cooldown_warning", user_lang), show_alert=False)
                return

        # Захватываем Single-Flight блокировку до обращения к БД
        self._in_flight_users.add(user.id)
        try:
            # 5.3. Global Daily Demo Limit: суточный стоп-кран демо-стенда
            if not authorized and not admin and not is_exempt:
                daily_count = await get_global_daily_demo_count()
                if daily_count >= GLOBAL_DAILY_DEMO_LIMIT:
                    exhausted_text = t("global_daily_quota_exceeded", user_lang)
                    if isinstance(event, types.Message):
                        await event.answer(exhausted_text, reply_markup=get_quota_exhausted_keyboard(user_lang), parse_mode="HTML")
                    elif isinstance(event, types.CallbackQuery):
                        await event.answer(t("quota_exhausted_title", user_lang), show_alert=True)
                        await event.message.answer(exhausted_text, reply_markup=get_quota_exhausted_keyboard(user_lang), parse_mode="HTML")
                    return

            # 5.4. Атомарная проверка и резервация квоты в SQLite WAL
            default_limit = DEFAULT_AUTH_QUOTA if authorized else DEFAULT_DEMO_QUOTA
            allowed, used, limit = await check_and_consume_quota(
                user_id=user.id,
                role="authorized" if authorized else "guest",
                default_limit=default_limit,
                is_exempt=is_exempt
            )

            if not allowed:
                # Квота исчерпана
                exhausted_title = t("quota_exhausted_title", user_lang)
                exhausted_msg = f"{exhausted_title}\n\n{t('quota_exhausted_text', user_lang, limit=limit)}"
                if isinstance(event, types.Message):
                    await event.answer(exhausted_msg, reply_markup=get_quota_exhausted_keyboard(user_lang), parse_mode="HTML")
                elif isinstance(event, types.CallbackQuery):
                    await event.answer(exhausted_title, show_alert=True)
                    await event.message.answer(exhausted_msg, reply_markup=get_quota_exhausted_keyboard(user_lang), parse_mode="HTML")
                return

            # Пробрасываем актуальную информацию о квоте в data для хэндлера
            data["quota_info"] = {
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used) if limit != -1 else None,
                "is_unlimited": bool(limit == -1 or is_exempt)
            }

            return await handler(event, data)
        finally:
            self._in_flight_users.discard(user.id)
            self._last_finished_at[user.id] = time.time()