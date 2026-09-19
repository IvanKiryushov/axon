"""
AxonBot Whitelist Middleware (Aiogram 3)
Обеспечивает изоляцию приватного контура, авторизацию пользователей через SQLite,
перехват Deep Linking реферальных меток и фильтрацию неавторизованных запросов.
"""

from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from Scripts.bot.utils.access_db import (
    get_or_create_user, 
    is_authorized, 
    is_admin, 
    get_user_language, 
    set_user_language,
    update_user_source
)
from Scripts.bot.utils.i18n import t

PUBLIC_COMMANDS = {"/start", "/help", "/examples", "/request_access", "/lang", "/status"}
PUBLIC_CALLBACK_PREFIXES = {"set_lang:", "request_access", "cat:", "menu", "help"}
VALID_CHANNELS = {"github", "upwork", "linkedin"}

class WhitelistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user: types.User = data.get("event_from_user")
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

        # Если гость пришел по внешнему каналу — принудительно фиксируем источник и английский язык
        if not authorized and not admin and deep_link_source in VALID_CHANNELS:
            await update_user_source(user.id, deep_link_source)
            await set_user_language(user.id, "en")

        user_lang = await get_user_language(user.id)

        # Пробрасываем контекст в data для хэндлеров
        data["user_lang"] = user_lang
        data["is_authorized"] = authorized
        data["is_admin"] = admin

        # 4. Проверка прав доступа
        if authorized or admin:
            return await handler(event, data)

        # 5. Фильтрация для гостей (неавторизованных)
        # Разрешаем прохождение публичных команд
        if isinstance(event, types.Message):
            # Пропускаем медиасообщения для выдачи строгого регламентного ответа о текстовом формате
            if event.voice or event.audio or event.video or event.video_note:
                return await handler(event, data)

            # Разрешаем команды и отправку контактов
            if event.text:
                cmd = event.text.split()[0].lower()
                if cmd in PUBLIC_COMMANDS:
                    return await handler(event, data)
            
            # Разрешаем отправку контакта (для заявки)
            if event.contact:
                return await handler(event, data)

            # Проверяем активное состояние FSM (если пользователь в процессе заполнения заявки)
            state = data.get("state")
            if state:
                current_state = await state.get_state()
                if current_state and "access" in current_state.lower():
                    return await handler(event, data)

            # Для любых других сообщений (вопросов в RAG) — выдаем экран блокировки
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("btn_request_access", user_lang), callback_data="request_access")],
                [InlineKeyboardButton(text=t("btn_examples", user_lang), callback_data="menu_examples")],
                [InlineKeyboardButton(text=t("btn_lang", user_lang), callback_data="toggle_lang")]
            ])
            
            lock_text = f"{t('access_denied_title', user_lang)}\n\n{t('access_denied_text', user_lang)}"
            await event.answer(lock_text, reply_markup=keyboard, parse_mode="HTML")
            return

        if isinstance(event, types.CallbackQuery):
            cb_data = event.data or ""
            # Разрешаем публичные колбэки
            if any(cb_data.startswith(p) for p in PUBLIC_CALLBACK_PREFIXES) or cb_data in ("toggle_lang", "menu_examples", "clear_chat"):
                return await handler(event, data)
            
            # Админские действия обрабатываются отдельно
            if cb_data.startswith(("approve:", "reject:")):
                return await handler(event, data)

            # Если колбэк на отправку вопроса кейса, а гость не авторизован:
            if cb_data.startswith("case:"):
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=t("btn_request_access", user_lang), callback_data="request_access")],
                    [InlineKeyboardButton(text=t("btn_back", user_lang), callback_data="menu_examples")]
                ])
                await event.message.answer(
                    f"{t('access_denied_title', user_lang)}\n\n{t('access_denied_text', user_lang)}",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                await event.answer()
                return

        return await handler(event, data)