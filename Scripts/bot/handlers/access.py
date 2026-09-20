"""
AxonBot Access Control & Lead Generation Handler
Обрабатывает подачу заявок на доступ, FSM-сбор контактных данных и роли,
отправку карточки лида администратору с inline-кнопками и push-уведомления.
"""

import logging
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from Scripts.bot.config import ADMIN_ID, CONFLUENCE_INVITE_LINK
from Scripts.bot.utils.access_db import (
    is_authorized,
    is_admin,
    get_user_language,
    get_or_create_user,
    create_access_request,
    get_pending_request_by_user,
    resolve_access_request,
    get_whitelist_users,
    get_lead_stats,
    revoke_user_access,
    reset_user_completely,
    get_all_users_paged,
    get_user_detail,
    set_user_quota,
    add_user_quota,
    reset_user_quota
)
from Scripts.bot.utils.i18n import t
from Scripts.bot.keyboards.main_menu import (
    get_main_keyboard, 
    get_cancel_keyboard,
    get_goal_step_keyboard,
    get_admin_request_keyboard,
    get_whitelist_paged_keyboard,
    get_user_detail_keyboard
)

router = Router()

class AccessRequestState(StatesGroup):
    waiting_for_company = State()
    waiting_for_details = State()

def is_cancel_message(text: str | None) -> bool:
    """Проверка, является ли сообщение командой отмены."""
    if not text:
        return False
    low = text.strip().lower()
    return low in ("отмена", "cancel", "❌ отмена", "❌ cancel", "/cancel")

@router.message(Command("request_access"))
@router.callback_query(F.data == "request_access")
async def handle_request_access_start(event: types.Message | types.CallbackQuery, state: FSMContext):
    """Старт процесса подачи заявки на доступ (Шаг 1: Компания и роль)."""
    user = event.from_user
    lang = await get_user_language(user.id)
    
    if await is_authorized(user.id):
        text = "Вы уже авторизованы в системе." if lang == "ru" else "You already have full access."
        if isinstance(event, types.CallbackQuery):
            await event.message.answer(text, reply_markup=get_main_keyboard(is_authorized=True, lang=lang), parse_mode="HTML")
            await event.answer()
        else:
            await event.answer(text, reply_markup=get_main_keyboard(is_authorized=True, lang=lang), parse_mode="HTML")
        return

    # Проверяем, есть ли уже заявка в ожидании
    pending = await get_pending_request_by_user(user.id)
    if pending:
        msg_text = t("request_already_pending", lang)
        if isinstance(event, types.CallbackQuery):
            await event.message.answer(msg_text, reply_markup=get_main_keyboard(is_authorized=False, lang=lang), parse_mode="HTML")
            await event.answer()
        else:
            await event.answer(msg_text, reply_markup=get_main_keyboard(is_authorized=False, lang=lang), parse_mode="HTML")
        return

    # Устанавливаем FSM состояние Шага 1 (Компания и роль)
    await state.set_state(AccessRequestState.waiting_for_company)
    prompt_text = t("request_step1_company", lang)
    
    if isinstance(event, types.CallbackQuery):
        await event.message.answer(prompt_text, reply_markup=get_cancel_keyboard(lang), parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(prompt_text, reply_markup=get_cancel_keyboard(lang), parse_mode="HTML")

@router.message(AccessRequestState.waiting_for_company)
async def process_step1_company(message: types.Message, state: FSMContext):
    """Шаг 1: Прием компании и роли соискателя (обязательный шаг)."""
    user = message.from_user
    lang = await get_user_language(user.id)
    
    if is_cancel_message(message.text):
        await state.clear()
        cancel_text = t("request_cancelled", lang)
        await message.answer(cancel_text, reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        await message.answer(t("welcome_title", lang), reply_markup=get_main_keyboard(is_authorized=False, lang=lang), parse_mode="HTML")
        return

    if not message.text or not message.text.strip():
        await message.answer(t("request_step1_company", lang), reply_markup=get_cancel_keyboard(lang), parse_mode="HTML")
        return

    company_info = message.text.strip()
    await state.update_data(company=company_info)
    
    # Определяем источник пользователя: для прямого запроса (source == 'direct') Шаг 2 обязателен
    user_data = await get_or_create_user(user.id)
    source = user_data.get("source", "direct")
    allow_skip = source in ("upwork", "github", "linkedin")
    await state.update_data(allow_skip=allow_skip, source=source)

    # Переход к Шагу 2: Цель запроса
    await state.set_state(AccessRequestState.waiting_for_details)
    prompt_key = "request_step2_details" if allow_skip else "request_step2_details_mandatory"
    await message.answer(
        t(prompt_key, lang), 
        reply_markup=get_goal_step_keyboard(lang=lang, allow_skip=allow_skip), 
        parse_mode="HTML"
    )

@router.message(AccessRequestState.waiting_for_details)
async def process_step2_details(message: types.Message, state: FSMContext):
    """Шаг 2: Выбор цели из кнопок или ввод своего варианта сообщением."""
    user = message.from_user
    lang = await get_user_language(user.id)
    
    if is_cancel_message(message.text):
        await state.clear()
        cancel_text = t("request_cancelled", lang)
        await message.answer(cancel_text, reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        await message.answer(t("welcome_title", lang), reply_markup=get_main_keyboard(is_authorized=False, lang=lang), parse_mode="HTML")
        return

    data = await state.get_data()
    company = data.get("company", "")
    allow_skip = data.get("allow_skip", False)
    source = data.get("source")
    if not source:
        user_data = await get_or_create_user(user.id)
        source = user_data.get("source", "direct")
        allow_skip = source in ("upwork", "github", "linkedin")

    if not message.text or not message.text.strip():
        prompt_key = "request_step2_details" if allow_skip else "request_step2_details_mandatory"
        await message.answer(
            t(prompt_key, lang), 
            reply_markup=get_goal_step_keyboard(lang, allow_skip=allow_skip), 
            parse_mode="HTML"
        )
        return

    txt = message.text.strip()
    low = txt.lower()
    is_skip = "пропустить" in low or "skip" in low or low in ("⏭", "пропустить ⏭", "skip ⏭")

    if is_skip:
        if not allow_skip:
            # Для прямого запроса пропуск запрещен — требуем выбрать цель кнопкой или написать свой вариант
            await message.answer(
                t("request_step2_details_mandatory", lang),
                reply_markup=get_goal_step_keyboard(lang, allow_skip=False),
                parse_mode="HTML"
            )
            return
        goal = ""
    else:
        goal = txt

    # Регистрируем заявку в SQLite
    req_id = await create_access_request(
        user_id=user.id,
        source=source,
        company=company,
        email="",
        contact_phone="",
        goal=goal
    )
    await state.clear()

    # Уведомляем пользователя об успешной отправке
    await message.answer(t("request_submitted", lang), reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    await message.answer(
        t("welcome_title", lang), 
        reply_markup=get_main_keyboard(is_authorized=False, lang=lang), 
        parse_mode="HTML"
    )

    # Отправляем карточку лида администратору
    if ADMIN_ID and ADMIN_ID > 0:
        username_str = f"@{user.username}" if user.username else "не указан"
        admin_card = (
            f"<b>Новая заявка на доступ к AxonBot</b>\n\n"
            f"<b>Соискатель:</b> {user.full_name} ({username_str})\n"
            f"<b>Telegram ID:</b> <code>{user.id}</code>\n"
            f"<b>Компания / Роль:</b> {company or 'не указана'}\n"
            f"<b>Цель:</b> {goal or 'не указана'}\n"
            f"<b>Источник:</b> <code>{source}</code>\n"
            f"<b>Язык:</b> {lang.upper()}\n"
            f"<b>Время заявки:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        try:
            await message.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_card,
                reply_markup=get_admin_request_keyboard(req_id),
                parse_mode="HTML"
            )
            logging.info(f"Заявка #{req_id} от user_id={user.id} отправлена администратору {ADMIN_ID}")
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление администратору: {e}")

@router.callback_query(F.data.startswith("approve:"))
async def handle_admin_approve(callback: types.CallbackQuery):
    """Обработка нажатия администратором кнопки [✅ Одобрить]."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только администратор может утверждать заявки.", show_alert=True)
        return

    req_id = int(callback.data.split(":")[1])
    res = await resolve_access_request(request_id=req_id, approve=True, admin_id=callback.from_user.id)
    
    if not res:
        await callback.answer("Заявка не найдена или уже обработана.", show_alert=True)
        return

    target_user_id, target_lang, _ = res

    # Обновляем карточку администратора
    original_text = callback.message.html_text or callback.message.text
    updated_text = (
        f"{original_text}\n\n"
        f"<b>ОДОБРЕНО</b> администратором @{callback.from_user.username or callback.from_user.id} "
        f"в {datetime.now().strftime('%H:%M:%S')}"
    )
    await callback.message.edit_text(updated_text, reply_markup=None, parse_mode="HTML")
    await callback.answer("Заявка одобрена.")

    # Отправляем push-уведомление соискателю с инвайт-ссылкой на Confluence
    try:
        push_msg = t("request_approved_push", target_lang, invite_link=CONFLUENCE_INVITE_LINK)
        await callback.bot.send_message(
            chat_id=target_user_id,
            text=push_msg,
            reply_markup=get_main_keyboard(is_authorized=True, lang=target_lang),
            parse_mode="HTML"
        )
        logging.info(f"Push об одобрении отправлен пользователю {target_user_id}")
    except Exception as e:
        logging.error(f"Не удалось отправить push пользователю {target_user_id}: {e}")

@router.callback_query(F.data.startswith("reject:"))
async def handle_admin_reject(callback: types.CallbackQuery):
    """Обработка нажатия администратором кнопки [❌ Отклонить]."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только администратор может отклонять заявки.", show_alert=True)
        return

    req_id = int(callback.data.split(":")[1])
    res = await resolve_access_request(request_id=req_id, approve=False, admin_id=callback.from_user.id)
    
    if not res:
        await callback.answer("Заявка не найдена или уже обработана.", show_alert=True)
        return

    target_user_id, target_lang, _ = res

    # Обновляем карточку администратора
    original_text = callback.message.html_text or callback.message.text
    updated_text = (
        f"{original_text}\n\n"
        f"<b>ОТКЛОНЕНО</b> администратором @{callback.from_user.username or callback.from_user.id} "
        f"в {datetime.now().strftime('%H:%M:%S')}"
    )
    await callback.message.edit_text(updated_text, reply_markup=None, parse_mode="HTML")
    await callback.answer("Заявка отклонена.")

    # Отправляем вежливое уведомление соискателю
    try:
        reject_msg = t("request_rejected_push", target_lang)
        await callback.bot.send_message(
            chat_id=target_user_id,
            text=reject_msg,
            reply_markup=get_main_keyboard(is_authorized=False, lang=target_lang),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление об отказе {target_user_id}: {e}")

async def render_whitelist_master(page: int = 0) -> tuple[str, types.InlineKeyboardMarkup]:
    """Генерирует текст и клавиатуру Master View списка пользователей."""
    users, total = await get_all_users_paged(page=page, page_size=5)
    if not users and page > 0:
        page = max(0, page - 1)
        users, total = await get_all_users_paged(page=page, page_size=5)

    text = (
        f"<b>Управление доступом и квотами (Whitelist):</b>\n\n"
        f"Всего пользователей в системе: <b>{total}</b>\n"
        f"Выберите пользователя для просмотра аналитики и изменения лимитов:"
    )
    kb = get_whitelist_paged_keyboard(users, page=page, total_count=total, page_size=5)
    return text, kb

async def render_user_detail(user_id: int, page: int = 0) -> tuple[str, types.InlineKeyboardMarkup] | None:
    """Генерирует текст и клавиатуру Detail View профиля пользователя."""
    u = await get_user_detail(user_id)
    if not u:
        return None

    role = u.get("role", "guest")
    used = u.get("queries_used") or 0
    raw_lim = u.get("queries_limit")
    if role == "admin" or raw_lim == -1:
        lim_str = "Безлимит (∞)"
        remaining_str = "∞"
    else:
        actual_lim = raw_lim if raw_lim is not None else (15 if role == "guest" else 100)
        lim_str = str(actual_lim)
        remaining_str = str(max(0, actual_lim - used))

    uname = f"@{u['username']}" if u.get("username") else "не указан"
    created = u.get("created_at") or "—"
    last_q = u.get("last_query_at") or "—"
    source = u.get("source") or "direct"

    req_info = ""
    if u.get("request_company") or u.get("request_goal"):
        req_info = (
            f"\n\n<b>Данные заявки:</b>\n"
            f"• Компания/Роль: {u.get('request_company') or '—'}\n"
            f"• Цель: {u.get('request_goal') or '—'}\n"
            f"• Статус заявки: <code>{u.get('request_status') or '—'}</code>"
        )

    status_label = "Администратор" if role == "admin" else ("Авторизован (Full Access)" if role == "authorized" else "Ознакомительный (Guest)")

    text = (
        f"<b>Профиль пользователя:</b> {u.get('full_name')} ({uname})\n"
        f"<b>ID:</b> <code>{u['user_id']}</code>\n"
        f"<b>Статус:</b> {status_label}\n"
        f"<b>Источник:</b> <code>{source}</code>\n"
        f"<b>Использовано:</b> <b>{used}</b> из <b>{lim_str}</b> (остаток: <b>{remaining_str}</b>)\n"
        f"<b>Первый вход:</b> {created}\n"
        f"<b>Посл. запрос:</b> {last_q}"
        f"{req_info}"
    )

    is_target_admin = (role == "admin")
    kb = get_user_detail_keyboard(user_id=user_id, page=page, is_target_admin=is_target_admin)
    return text, kb

@router.message(Command("whitelist"))
async def cmd_admin_whitelist(message: types.Message):
    """Админская команда: Master View списка пользователей с пагинацией."""
    if message.from_user.id != ADMIN_ID:
        return
    text, kb = await render_whitelist_master(page=0)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("wlp:"))
async def handle_callback_whitelist_page(callback: types.CallbackQuery):
    """Переключение страницы в Master View списка пользователей."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    page = int(callback.data.split(":")[1])
    text, kb = await render_whitelist_master(page=page)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data == "wl_noop")
async def handle_callback_whitelist_noop(callback: types.CallbackQuery):
    """Информационная кнопка номера страницы."""
    await callback.answer()

@router.callback_query(F.data.startswith("wlu:"))
async def handle_callback_whitelist_user(callback: types.CallbackQuery):
    """Переход в Detail View карточки пользователя."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    parts = callback.data.split(":")
    target_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    detail = await render_user_detail(target_id, page=page)
    if not detail:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    text, kb = detail
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("qa:"))
async def handle_callback_quota_add(callback: types.CallbackQuery):
    """Начисление +15 запросов пользователю."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    parts = callback.data.split(":")
    target_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    success, new_limit = await add_user_quota(target_id, 15)
    if success:
        await callback.answer(f"Начислено +15 запросов (лимит: {new_limit}).")
        detail = await render_user_detail(target_id, page=page)
        if detail:
            text, kb = detail
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
    else:
        await callback.answer("Пользователь не найден.", show_alert=True)

@router.callback_query(F.data.startswith("qm:"))
async def handle_callback_quota_minus(callback: types.CallbackQuery):
    """Списание -15 запросов у пользователя."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    parts = callback.data.split(":")
    target_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    success, new_limit = await add_user_quota(target_id, -15)
    if success:
        await callback.answer(f"Списано 15 запросов (лимит: {new_limit}).")
        detail = await render_user_detail(target_id, page=page)
        if detail:
            text, kb = detail
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
    else:
        await callback.answer("Пользователь не найден.", show_alert=True)

@router.callback_query(F.data.startswith("qd:"))
async def handle_callback_quota_default(callback: types.CallbackQuery):
    """Установка дефолтного лимита 15 запросов."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    parts = callback.data.split(":")
    target_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    success = await set_user_quota(target_id, 15)
    if success:
        await callback.answer("Установлен дефолтный лимит: 15 запросов.")
        detail = await render_user_detail(target_id, page=page)
        if detail:
            text, kb = detail
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
    else:
        await callback.answer("Пользователь не найден.", show_alert=True)

@router.callback_query(F.data.startswith("qu:"))
async def handle_callback_quota_unlimited(callback: types.CallbackQuery):
    """Установка вечного безлимита пользователю (-1)."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    parts = callback.data.split(":")
    target_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    success = await set_user_quota(target_id, -1)
    if success:
        await callback.answer("Установлен вечный безлимит.")
        detail = await render_user_detail(target_id, page=page)
        if detail:
            text, kb = detail
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
    else:
        await callback.answer("Пользователь не найден.", show_alert=True)

@router.callback_query(F.data.startswith("qr:"))
async def handle_callback_quota_reset(callback: types.CallbackQuery):
    """Сброс счетчика использованных запросов в 0."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    parts = callback.data.split(":")
    target_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    success = await reset_user_quota(target_id)
    if success:
        await callback.answer("Счетчик использованных запросов сброшен в 0.")
        detail = await render_user_detail(target_id, page=page)
        if detail:
            text, kb = detail
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
    else:
        await callback.answer("Пользователь не найден.", show_alert=True)

@router.callback_query(F.data.startswith("wlr:"))
async def handle_callback_whitelist_revoke(callback: types.CallbackQuery):
    """Отзыв прав доступа из Detail View (перевод в guest)."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    parts = callback.data.split(":")
    target_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    success = await revoke_user_access(target_id, ADMIN_ID)
    if success:
        await callback.answer("Доступ отозван. Пользователь переведен в статус гостя.", show_alert=True)
        try:
            await callback.bot.send_message(
                chat_id=target_id,
                text="Ваш доступ к AxonBot был приостановлен администратором.",
                reply_markup=get_main_keyboard(is_authorized=False, lang="ru"),
                parse_mode="HTML"
            )
        except Exception:
            pass
        detail = await render_user_detail(target_id, page=page)
        if detail:
            text, kb = detail
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
    else:
        await callback.answer("Не удалось отозвать доступ (нельзя отозвать у администратора).", show_alert=True)

@router.callback_query(F.data.startswith("wld:"))
async def handle_callback_whitelist_delete(callback: types.CallbackQuery):
    """Полный сброс пользователя из Detail View."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    parts = callback.data.split(":")
    target_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    success = await reset_user_completely(target_id, ADMIN_ID)
    if success:
        await callback.answer("Пользователь удален из базы.", show_alert=True)
        try:
            await callback.bot.send_message(
                chat_id=target_id,
                text="Ваш профиль был сброшен. Нажмите /start для повторного прохождения воронки регистрации.",
                reply_markup=get_main_keyboard(is_authorized=False, lang="ru"),
                parse_mode="HTML"
            )
        except Exception:
            pass
        text, kb = await render_whitelist_master(page=page)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    else:
        await callback.answer("Не удалось удалить пользователя.", show_alert=True)

@router.callback_query(F.data.startswith("revoke:"))
async def handle_callback_revoke(callback: types.CallbackQuery):
    """Обработка legacy-нажатия кнопки отзыва доступа."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только администратор может отзывать доступ.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    success = await revoke_user_access(target_id, ADMIN_ID)
    if success:
        await callback.answer("Доступ отозван. Пользователь переведен в статус гостя.", show_alert=True)
        try:
            await callback.bot.send_message(
                chat_id=target_id,
                text="Ваш доступ к AxonBot был приостановлен администратором.",
                reply_markup=get_main_keyboard(is_authorized=False, lang="ru"),
                parse_mode="HTML"
            )
        except Exception:
            pass
        await cmd_admin_whitelist(callback.message)
    else:
        await callback.answer("Не удалось отозвать доступ (нельзя отозвать у администратора).", show_alert=True)

@router.callback_query(F.data.startswith("reset_user:"))
async def handle_callback_reset_user(callback: types.CallbackQuery):
    """Обработка legacy-нажатия кнопки полного сброса пользователя."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только администратор может сбрасывать пользователей.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    success = await reset_user_completely(target_id, ADMIN_ID)
    if success:
        await callback.answer("Пользователь полностью удален из базы для чистого теста.", show_alert=True)
        try:
            await callback.bot.send_message(
                chat_id=target_id,
                text="Ваш профиль был сброшен. Нажмите /start для повторного прохождения воронки регистрации.",
                reply_markup=get_main_keyboard(is_authorized=False, lang="ru"),
                parse_mode="HTML"
            )
        except Exception:
            pass
        await cmd_admin_whitelist(callback.message)
    else:
        await callback.answer("Не удалось сбросить пользователя.", show_alert=True)

@router.message(Command("kick"))
@router.message(Command("revoke"))
async def cmd_admin_kick(message: types.Message):
    """Админская команда /kick <user_id> или /revoke <user_id>."""
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.strip().split()
    if len(args) < 2:
        await message.answer("Укажите Telegram ID пользователя. Пример: <code>/kick 7776694188</code>", parse_mode="HTML")
        return
    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("Некорректный ID пользователя. ID должен быть целым числом.", parse_mode="HTML")
        return

    success = await revoke_user_access(target_id, ADMIN_ID)
    if success:
        await message.answer(f"Доступ для пользователя <code>{target_id}</code> успешно отозван (роль сброшена на 'guest').", parse_mode="HTML")
        try:
            await message.bot.send_message(
                chat_id=target_id,
                text="Ваш доступ к AxonBot был приостановлен администратором.",
                reply_markup=get_main_keyboard(is_authorized=False, lang="ru"),
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await message.answer("Не удалось отозвать доступ (нельзя отозвать у администратора).", parse_mode="HTML")

@router.message(Command("reset_user"))
async def cmd_admin_reset_user(message: types.Message):
    """Админская команда /reset_user <user_id> для полного сброса тестового аккаунта."""
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.strip().split()
    if len(args) < 2:
        await message.answer("Укажите Telegram ID пользователя. Пример: <code>/reset_user 7776694188</code>", parse_mode="HTML")
        return
    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("Некорректный ID пользователя. ID должен быть целым числом.", parse_mode="HTML")
        return

    success = await reset_user_completely(target_id, ADMIN_ID)
    if success:
        await message.answer(f"Пользователь <code>{target_id}</code> полностью удален из базы данных. Теперь он может пройти воронку заново с чистого листа.", parse_mode="HTML")
        try:
            await message.bot.send_message(
                chat_id=target_id,
                text="Ваш профиль был сброшен. Нажмите /start для повторного прохождения воронки регистрации.",
                reply_markup=get_main_keyboard(is_authorized=False, lang="ru"),
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await message.answer("Не удалось удалить пользователя.", parse_mode="HTML")

@router.message(Command("set_quota"))
async def cmd_admin_set_quota(message: types.Message):
    """Админская команда /set_quota <user_id> <limit> (-1 для безлимита)."""
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.strip().split()
    if len(args) < 3:
        await message.answer("Использование: <code>/set_quota &lt;user_id&gt; &lt;limit&gt;</code> (-1 для безлимита)", parse_mode="HTML")
        return
    try:
        target_id = int(args[1])
        new_limit = int(args[2])
    except ValueError:
        await message.answer("Параметры должны быть целыми числами.", parse_mode="HTML")
        return

    success = await set_user_quota(target_id, new_limit)
    if success:
        lim_str = "безлимит (∞)" if new_limit == -1 else str(new_limit)
        await message.answer(f"Лимит для пользователя <code>{target_id}</code> установлен: <b>{lim_str}</b>.", parse_mode="HTML")
    else:
        await message.answer("Пользователь с таким ID не найден в базе.", parse_mode="HTML")

@router.message(Command("add_quota"))
async def cmd_admin_add_quota(message: types.Message):
    """Админская команда /add_quota <user_id> <amount>."""
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.strip().split()
    if len(args) < 3:
        await message.answer("Использование: <code>/add_quota &lt;user_id&gt; &lt;amount&gt;</code>", parse_mode="HTML")
        return
    try:
        target_id = int(args[1])
        amount = int(args[2])
    except ValueError:
        await message.answer("Параметры должны быть целыми числами.", parse_mode="HTML")
        return

    success, new_lim = await add_user_quota(target_id, amount)
    if success:
        lim_str = "безлимит (∞)" if new_lim == -1 else str(new_lim)
        await message.answer(f"Пользователю <code>{target_id}</code> добавлено <b>{amount}</b> запросов. Новый лимит: <b>{lim_str}</b>.", parse_mode="HTML")
    else:
        await message.answer("Пользователь с таким ID не найден в базе.", parse_mode="HTML")

@router.message(Command("reset_quota"))
async def cmd_admin_reset_quota(message: types.Message):
    """Админская команда /reset_quota <user_id>."""
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.strip().split()
    if len(args) < 2:
        await message.answer("Использование: <code>/reset_quota &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("ID пользователя должен быть целым числом.", parse_mode="HTML")
        return

    success = await reset_user_quota(target_id)
    if success:
        await message.answer(f"Счетчик использованных запросов пользователя <code>{target_id}</code> сброшен в 0.", parse_mode="HTML")
    else:
        await message.answer("Пользователь с таким ID не найден в базе.", parse_mode="HTML")

@router.message(Command("stats"))
async def cmd_admin_stats(message: types.Message):
    """Админская команда: сводная статистика лидогенерации и источников."""
    if message.from_user.id != ADMIN_ID:
        return

    stats = await get_lead_stats()
    lines = [
        "<b>Аналитика лидогенерации AxonBot:</b>\n",
        f"• Всего контактов в базе: <b>{stats['total_users']}</b>",
        f"• Авторизованных (Approved): <b>{stats['authorized_users']}</b>",
        f"• В ожидании (Pending): <b>{stats['pending_requests']}</b>",
        f"• Всего RAG-запросов выполнено: <b>{stats.get('total_queries', 0)}</b>",
        f"• Гостей с исчерпанной квотой: <b>{stats.get('exhausted_guests', 0)}</b>\n",
        "<b>Распределение по источникам трафика (Deep Linking):</b>"
    ]

    for s in stats["sources"]:
        lines.append(f"  - <code>{s['source']}</code>: <b>{s['count']}</b> пользователей")

    await message.answer("\n".join(lines), parse_mode="HTML")