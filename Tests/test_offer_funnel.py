import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import types
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from Scripts.bot.handlers.offer import (
    OfferRequestState,
    start_offer_funnel,
    process_offer_step1_kb,
    process_offer_step2_scope,
    process_offer_step3_contact,
)
from Scripts.bot.handlers.base import cmd_start
from Scripts.bot.middlewares.whitelist import WhitelistMiddleware
from Scripts.bot.utils import access_db


def _make_state(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=123, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


@pytest.mark.asyncio
async def test_offer_funnel_full_flow(tmp_path):
    access_db.set_db_path(tmp_path / "test_offer.db")
    await access_db.init_db(admin_id=9990001)

    uid = 4040401
    state = _make_state(uid)
    user = MagicMock(id=uid, full_name="Ivan Petrov", username="ipetrov", language_code="ru")

    # Старт анкеты
    msg0 = MagicMock(from_user=user, answer=AsyncMock())
    await start_offer_funnel(msg0, state, source_override="offer")
    assert await state.get_state() == OfferRequestState.waiting_for_kb_status.state

    # Шаг 1: База знаний
    msg1 = MagicMock(from_user=user, text="Confluence / Notion", voice=None, audio=None, answer=AsyncMock())
    await process_offer_step1_kb(msg1, state)
    assert await state.get_state() == OfferRequestState.waiting_for_scope.state

    # Шаг 2: Команда и задачи (произвольный текст)
    msg2 = MagicMock(from_user=user, text="25 человек, КР и АР", voice=None, audio=None, answer=AsyncMock())
    await process_offer_step2_scope(msg2, state)
    assert await state.get_state() == OfferRequestState.waiting_for_contact.state

    # Шаг 3: Компания и контакт
    msg3 = MagicMock(
        from_user=user,
        text="ПроектБюро, BIM-лид, пишите сюда",
        voice=None,
        audio=None,
        answer=AsyncMock(),
        bot=MagicMock(),
    )
    with patch("Scripts.bot.handlers.offer.send_ops_event", new_callable=AsyncMock) as mock_ops:
        await process_offer_step3_contact(msg3, state)
        assert mock_ops.await_count == 1
        card_text = mock_ops.await_args.kwargs["text"]
        assert "Confluence / Notion" in card_text
        assert "25 человек, КР и АР" in card_text
        assert "ПроектБюро" in card_text

    assert await state.get_state() is None
    reqs = await access_db.get_offer_requests_by_user(uid)
    assert len(reqs) == 1
    assert reqs[0]["source"] == "offer"
    assert reqs[0]["kb_status"] == "Confluence / Notion"
    assert reqs[0]["team_scope"] == "25 человек, КР и АР"
    assert reqs[0]["company_contact"] == "ПроектБюро, BIM-лид, пишите сюда"


@pytest.mark.asyncio
async def test_offer_funnel_cancel_and_voice_hint(tmp_path):
    access_db.set_db_path(tmp_path / "test_offer_cancel.db")
    await access_db.init_db(admin_id=9990001)

    uid = 4040402
    state = _make_state(uid)
    user = MagicMock(id=uid, full_name="Anna", username="anna", language_code="ru")
    await start_offer_funnel(MagicMock(from_user=user, answer=AsyncMock()), state)

    # Голосовое сообщение в анкете — вежливая подсказка, состояние сохраняется
    voice_msg = MagicMock(from_user=user, text=None, voice=MagicMock(), audio=None, answer=AsyncMock())
    await process_offer_step1_kb(voice_msg, state)
    assert await state.get_state() == OfferRequestState.waiting_for_kb_status.state
    assert "Голосовой ввод" in voice_msg.answer.await_args.args[0]

    # Отмена
    cancel_msg = MagicMock(from_user=user, text="❌ Отмена", voice=None, audio=None, answer=AsyncMock())
    await process_offer_step1_kb(cancel_msg, state)
    assert await state.get_state() is None
    assert await access_db.get_offer_requests_by_user(uid) == []


@pytest.mark.asyncio
async def test_start_offer_deeplink_launches_funnel(tmp_path):
    access_db.set_db_path(tmp_path / "test_offer_dl.db")
    await access_db.init_db(admin_id=9990001)

    uid = 4040403
    state = _make_state(uid)
    user = MagicMock(id=uid, full_name="Client", username="client", language_code="ru")
    msg = MagicMock(from_user=user, answer=AsyncMock())
    cmd = CommandObject(prefix="/", command="start", args="offer")

    await cmd_start(msg, command=cmd, state=state)
    assert await state.get_state() == OfferRequestState.waiting_for_kb_status.state


@pytest.mark.asyncio
async def test_whitelist_bypasses_rag_quota_during_offer_fsm(tmp_path):
    """Ответы внутри OfferRequestState не должны списывать демо-квоту RAG."""
    access_db.set_db_path(tmp_path / "test_offer_mw.db")
    await access_db.init_db(admin_id=9990001)

    uid = 4040404
    state = _make_state(uid)
    await state.set_state(OfferRequestState.waiting_for_kb_status)

    mw = WhitelistMiddleware()
    event = MagicMock(spec=types.Message)
    event.from_user = MagicMock(id=uid, full_name="Lead", username="lead", language_code="ru")
    event.text = "У нас Confluence и PDF"
    event.voice = None
    event.audio = None
    event.video = None
    event.video_note = None
    event.contact = None
    event.bot = MagicMock()

    next_handler = AsyncMock(return_value="ok")
    data = {"event_from_user": event.from_user, "state": state, "bot": event.bot}

    with patch("Scripts.bot.middlewares.whitelist.send_ops_event", new_callable=AsyncMock):
        res = await mw(next_handler, event, data)

    assert res == "ok"
    assert next_handler.await_count == 1
    assert "quota_info" not in data
    quota = await access_db.get_user_quota(uid)
    assert quota["used"] == 0
