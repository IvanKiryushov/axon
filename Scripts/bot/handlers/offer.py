"""
AxonBot Implementation Inquiry (Offer) Handler
Короткая текстовая 3-шаговая анкета для потенциальных клиентов, желающих обсудить
внедрение AxonBot на базе собственных BIM-стандартов.
"""

import html
import logging
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from Scripts.bot.utils.alert_manager import send_ops_event
from Scripts.bot.utils.access_db import (
    is_authorized,
    is_admin,
    get_user_language,
    get_or_create_user,
    create_offer_request,
)
from Scripts.bot.utils.i18n import t
from Scripts.bot.keyboards.main_menu import (
    get_main_keyboard,
    get_offer_kb_step_keyboard,
    get_offer_scope_step_keyboard,
    get_offer_contact_step_keyboard,
)

router = Router()


class OfferRequestState(StatesGroup):
    waiting_for_kb_status = State()
    waiting_for_scope = State()
    waiting_for_contact = State()


def _is_cancel(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in ("отмена", "cancel", "❌ отмена", "❌ cancel", "/cancel")


def _is_skip(text: str | None) -> bool:
    if not text:
        return False
    low = text.strip().lower()
    return "пропустить" in low or "skip" in low or low in ("⏭", "пропустить ⏭", "skip ⏭")


async def _cancel_offer(message: types.Message, state: FSMContext, lang: str):
    await state.clear()
    user = message.from_user
    authorized = await is_authorized(user.id)
    admin = await is_admin(user.id)
    await message.answer(t("offer_cancelled", lang), reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    await message.answer(
        t("welcome_title", lang),
        reply_markup=get_main_keyboard(is_authorized=authorized, lang=lang, is_admin=admin),
        parse_mode="HTML",
    )


async def start_offer_funnel(
    event: types.Message | types.CallbackQuery,
    state: FSMContext,
    source_override: str | None = None,
):
    """Запуск 3-шаговой анкеты внедрения (из команды /offer, кнопки или диплинка /start offer)."""
    user = event.from_user
    lang = await get_user_language(user.id)
    user_data = await get_or_create_user(user.id)
    source = source_override or user_data.get("source", "direct")

    await state.clear()
    await state.set_state(OfferRequestState.waiting_for_kb_status)
    await state.update_data(source=source)

    prompt = t("offer_step1_kb", lang)
    kb = get_offer_kb_step_keyboard(lang)

    if isinstance(event, types.CallbackQuery):
        await event.message.answer(prompt, reply_markup=kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(prompt, reply_markup=kb, parse_mode="HTML")


@router.message(Command("offer"))
@router.callback_query(F.data == "offer_start")
async def handle_offer_start(event: types.Message | types.CallbackQuery, state: FSMContext):
    await start_offer_funnel(event, state)


@router.message(OfferRequestState.waiting_for_kb_status)
async def process_offer_step1_kb(message: types.Message, state: FSMContext):
    """Шаг 1: Наличие и формат базы знаний / BIM-стандарта."""
    lang = await get_user_language(message.from_user.id)

    if message.voice or message.audio:
        await message.answer(t("offer_voice_not_ready", lang), reply_markup=get_offer_kb_step_keyboard(lang))
        return

    if _is_cancel(message.text):
        await _cancel_offer(message, state, lang)
        return

    if not message.text or not message.text.strip():
        await message.answer(t("offer_step1_kb", lang), reply_markup=get_offer_kb_step_keyboard(lang), parse_mode="HTML")
        return

    await state.update_data(kb_status=message.text.strip())
    await state.set_state(OfferRequestState.waiting_for_scope)
    await message.answer(
        t("offer_step2_scope", lang),
        reply_markup=get_offer_scope_step_keyboard(lang),
        parse_mode="HTML",
    )


@router.message(OfferRequestState.waiting_for_scope)
async def process_offer_step2_scope(message: types.Message, state: FSMContext):
    """Шаг 2: Размер команды, разделы и задачи."""
    lang = await get_user_language(message.from_user.id)

    if message.voice or message.audio:
        await message.answer(t("offer_voice_not_ready", lang), reply_markup=get_offer_scope_step_keyboard(lang))
        return

    if _is_cancel(message.text):
        await _cancel_offer(message, state, lang)
        return

    if not message.text or not message.text.strip():
        await message.answer(
            t("offer_step2_scope", lang),
            reply_markup=get_offer_scope_step_keyboard(lang),
            parse_mode="HTML",
        )
        return

    team_scope = "" if _is_skip(message.text) else message.text.strip()
    await state.update_data(team_scope=team_scope)
    await state.set_state(OfferRequestState.waiting_for_contact)
    await message.answer(
        t("offer_step3_contact", lang),
        reply_markup=get_offer_contact_step_keyboard(lang),
        parse_mode="HTML",
    )


@router.message(OfferRequestState.waiting_for_contact)
async def process_offer_step3_contact(message: types.Message, state: FSMContext):
    """Шаг 3: Компания, роль и контакт + отправка карточки лида в Control Room."""
    user = message.from_user
    lang = await get_user_language(user.id)

    if message.voice or message.audio:
        await message.answer(t("offer_voice_not_ready", lang), reply_markup=get_offer_contact_step_keyboard(lang))
        return

    if _is_cancel(message.text):
        await _cancel_offer(message, state, lang)
        return

    if not message.text or not message.text.strip():
        await message.answer(
            t("offer_step3_contact", lang),
            reply_markup=get_offer_contact_step_keyboard(lang),
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    kb_status = data.get("kb_status", "")
    team_scope = data.get("team_scope", "")
    source = data.get("source", "direct")
    company_contact = message.text.strip()

    req_id = await create_offer_request(
        user_id=user.id,
        source=source,
        kb_status=kb_status,
        team_scope=team_scope,
        company_contact=company_contact,
    )
    await state.clear()

    authorized = await is_authorized(user.id)
    admin = await is_admin(user.id)

    await message.answer(t("offer_submitted", lang), reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    await message.answer(
        t("welcome_title", lang),
        reply_markup=get_main_keyboard(is_authorized=authorized, lang=lang, is_admin=admin),
        parse_mode="HTML",
    )

    username_str = f"@{user.username}" if user.username else "не указан"
    safe_name = html.escape(user.full_name or "")
    safe_kb = html.escape(kb_status or "не указано")
    safe_scope = html.escape(team_scope or "не указано")
    safe_contact = html.escape(company_contact or "не указано")

    lead_card = (
        f"💼 <b>Заявка на внедрение AxonBot (#{req_id})</b>\n\n"
        f"• <b>Клиент:</b> {safe_name} ({username_str})\n"
        f"• <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"• <b>База знаний / стандарт:</b> {safe_kb}\n"
        f"• <b>Команда и задачи:</b> {safe_scope}\n"
        f"• <b>Компания и контакт:</b> {safe_contact}\n"
        f"• <b>Источник:</b> <code>{html.escape(source)}</code>\n"
        f"• <b>Язык:</b> {lang.upper()}\n"
        f"• <b>Время:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    kb_builder = InlineKeyboardBuilder()
    contact_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    kb_builder.row(InlineKeyboardButton(text="💬 Написать клиенту", url=contact_url))

    try:
        await send_ops_event(
            bot=message.bot,
            text=lead_card,
            reply_markup=kb_builder.as_markup(),
            silent=False,
            parse_mode="HTML",
        )
        logging.info(f"Заявка на внедрение #{req_id} от user_id={user.id} отправлена в Control Room.")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление о заявке на внедрение: {e}")
