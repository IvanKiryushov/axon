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
    reset_user_completely
)
from Scripts.bot.utils.i18n import t
from Scripts.bot.keyboards.main_menu import (
    get_main_keyboard, 
    get_cancel_keyboard,
    get_goal_step_keyboard,
    get_admin_request_keyboard
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
    
    # Переход к Шагу 2: Цель запроса и контакт (опциональный шаг)
    await state.set_state(AccessRequestState.waiting_for_details)
    await message.answer(t("request_step2_details", lang), reply_markup=get_goal_step_keyboard(lang), parse_mode="HTML")

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
    goal = ""

    if message.text:
        txt = message.text.strip()
        low = txt.lower()
        if "пропустить" in low or "skip" in low or low in ("⏭", "пропустить ⏭", "skip ⏭"):
            goal = ""
        else:
            goal = txt

    user_data = await get_or_create_user(user.id)
    source = user_data.get("source", "direct")

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

@router.message(Command("whitelist"))
async def cmd_admin_whitelist(message: types.Message):
    """Админская команда: вывод списка авторизованных пользователей с кнопками управления."""
    if message.from_user.id != ADMIN_ID:
        return

    users = await get_whitelist_users()
    if not users:
        await message.answer("В белом списке пока нет пользователей.", parse_mode="HTML")
        return

    lines = [
        "<b>Список авторизованных пользователей (Whitelist):</b>\n",
        "Для управления пользователями используйте кнопки ниже или команды:\n"
        "• <code>/kick &lt;user_id&gt;</code> — отозвать доступ (вернуть в статус гостя)\n"
        "• <code>/reset_user &lt;user_id&gt;</code> — удалить из базы для теста с нуля\n"
    ]
    
    kb_builder = InlineKeyboardBuilder()
    for u in users:
        uname = f"@{u['username']}" if u['username'] else "no_username"
        role_label = "[ADMIN]" if u['role'] == "admin" else "[USER]"
        lines.append(f"{role_label} <b>{u['full_name']}</b> ({uname}) | ID: <code>{u['user_id']}</code> | Ref: <code>{u['source']}</code>")
        if u['role'] != "admin":
            short_name = (u['full_name'] or str(u['user_id']))[:15]
            kb_builder.row(
                InlineKeyboardButton(text=f"🚫 Отозвать {short_name}", callback_data=f"revoke:{u['user_id']}"),
                InlineKeyboardButton(text=f"🔄 Сбросить {short_name}", callback_data=f"reset_user:{u['user_id']}")
            )

    await message.answer(
        "\n".join(lines), 
        reply_markup=kb_builder.as_markup() if kb_builder.buttons else None, 
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("revoke:"))
async def handle_callback_revoke(callback: types.CallbackQuery):
    """Обработка нажатия кнопки отзыва доступа из списка whitelist."""
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
    """Обработка нажатия кнопки полного сброса пользователя для повторного тестирования."""
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
        f"• В ожидании (Pending): <b>{stats['pending_requests']}</b>\n",
        "<b>Распределение по источникам трафика (Deep Linking):</b>"
    ]
    
    for s in stats["sources"]:
        lines.append(f"  - <code>{s['source']}</code>: <b>{s['count']}</b> пользователей")

    await message.answer("\n".join(lines), parse_mode="HTML")