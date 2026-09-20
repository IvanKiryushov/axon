import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from Scripts.bot.handlers.access import (
    AccessRequestState,
    handle_request_access_start,
    process_step1_company,
    process_step2_details
)
from Scripts.bot.utils import access_db

@pytest.mark.asyncio
async def test_funnel_two_step_flow(tmp_path):
    test_db = tmp_path / "test_axonbot.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    test_user_id = 555666777
    storage = MemoryStorage()
    key = StorageKey(bot_id=123, chat_id=test_user_id, user_id=test_user_id)
    state = FSMContext(storage=storage, key=key)

    # 1. Start funnel
    event = MagicMock()
    event.from_user = MagicMock(id=test_user_id, full_name="Tester", username="tester")
    event.answer = AsyncMock()

    await handle_request_access_start(event, state)
    current_st = await state.get_state()
    assert current_st == AccessRequestState.waiting_for_company.state
    assert event.answer.call_count == 1
    assert "СпецПроект" in event.answer.call_args[0][0]

    # 2. Provide company (Step 1)
    msg_step1 = MagicMock()
    msg_step1.from_user = event.from_user
    msg_step1.text = "BIM-Tech, Lead Structural Engineer"
    msg_step1.answer = AsyncMock()

    await process_step1_company(msg_step1, state)
    current_st = await state.get_state()
    assert current_st == AccessRequestState.waiting_for_details.state
    data = await state.get_data()
    assert data["company"] == "BIM-Tech, Lead Structural Engineer"
    assert msg_step1.answer.call_count == 1

    # 3. Complete Step 2 (Select Goal)
    msg_step2 = MagicMock()
    msg_step2.from_user = event.from_user
    msg_step2.text = "Внедрение в компании"
    msg_step2.contact = None
    msg_step2.answer = AsyncMock()
    msg_step2.bot = MagicMock()
    msg_step2.bot.send_message = AsyncMock()

    await process_step2_details(msg_step2, state)
    current_st = await state.get_state()
    assert current_st is None

    pending = await access_db.get_pending_request_by_user(test_user_id)
    assert pending is not None
    assert pending["company"] == "BIM-Tech, Lead Structural Engineer"
    assert pending["goal"] == "Внедрение в компании"
    assert pending["email"] == ""

@pytest.mark.asyncio
async def test_funnel_custom_goal_text(tmp_path):
    test_db = tmp_path / "test_axonbot_custom.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    test_user_id = 888999111
    storage = MemoryStorage()
    key = StorageKey(bot_id=123, chat_id=test_user_id, user_id=test_user_id)
    state = FSMContext(storage=storage, key=key)

    # Step 1
    await state.set_state(AccessRequestState.waiting_for_company)
    msg_step1 = MagicMock()
    msg_step1.from_user = MagicMock(id=test_user_id, full_name="Custom Tester", username="ctester")
    msg_step1.text = "Independent BIM Consultant"
    msg_step1.answer = AsyncMock()

    await process_step1_company(msg_step1, state)
    assert await state.get_state() == AccessRequestState.waiting_for_details.state

    # Step 2 with custom freeform text
    msg_step2 = MagicMock()
    msg_step2.from_user = msg_step1.from_user
    msg_step2.text = "Хотим интегрировать бота в корпоративный Slack и обучить на своих чертежах"
    msg_step2.answer = AsyncMock()
    msg_step2.bot = MagicMock()
    msg_step2.bot.send_message = AsyncMock()

    await process_step2_details(msg_step2, state)
    assert await state.get_state() is None

    pending = await access_db.get_pending_request_by_user(test_user_id)
    assert pending is not None
    assert pending["company"] == "Independent BIM Consultant"
    assert pending["goal"] == "Хотим интегрировать бота в корпоративный Slack и обучить на своих чертежах"

@pytest.mark.asyncio
async def test_funnel_direct_cannot_skip_step2(tmp_path):
    """Тест: пользователь без реферальной метки (direct) не может пропустить Шаг 2 (цель обязательна)."""
    test_db = tmp_path / "test_direct_funnel.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    test_user_id = 111333555
    await access_db.get_or_create_user(test_user_id, source="direct")

    storage = MemoryStorage()
    key = StorageKey(bot_id=123, chat_id=test_user_id, user_id=test_user_id)
    state = FSMContext(storage=storage, key=key)

    # Step 1
    await state.set_state(AccessRequestState.waiting_for_company)
    msg_step1 = MagicMock()
    msg_step1.from_user = MagicMock(id=test_user_id, full_name="Direct Tester", username="direct_tester")
    msg_step1.text = "Direct BIM Bureau"
    msg_step1.answer = AsyncMock()

    await process_step1_company(msg_step1, state)
    assert await state.get_state() == AccessRequestState.waiting_for_details.state
    # Проверяем, что в сообщении нет слова 'опционально'
    step1_reply = msg_step1.answer.call_args[0][0]
    assert "опционально" not in step1_reply.lower()

    # Попытка нажать/отправить 'Пропустить ⏭'
    msg_skip = MagicMock()
    msg_skip.from_user = msg_step1.from_user
    msg_skip.text = "Пропустить ⏭"
    msg_skip.answer = AsyncMock()

    await process_step2_details(msg_skip, state)
    # Состояние НЕ должно сброситься, заявка НЕ должна быть создана
    assert await state.get_state() == AccessRequestState.waiting_for_details.state
    assert await access_db.get_pending_request_by_user(test_user_id) is None
    assert msg_skip.answer.call_count == 1

    # Теперь выбираем цель
    msg_goal = MagicMock()
    msg_goal.from_user = msg_step1.from_user
    msg_goal.text = "Внедрение в компании"
    msg_goal.answer = AsyncMock()
    msg_goal.bot = MagicMock()
    msg_goal.bot.send_message = AsyncMock()

    await process_step2_details(msg_goal, state)
    assert await state.get_state() is None

    req = await access_db.get_pending_request_by_user(test_user_id)
    assert req is not None
    assert req["company"] == "Direct BIM Bureau"
    assert req["goal"] == "Внедрение в компании"

@pytest.mark.asyncio
async def test_funnel_referral_can_skip_step2(tmp_path):
    """Тест: пользователь с внешнего канала (upwork) может пропустить Шаг 2."""
    test_db = tmp_path / "test_ref_funnel.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    test_user_id = 222444666
    await access_db.get_or_create_user(test_user_id, source="upwork")

    storage = MemoryStorage()
    key = StorageKey(bot_id=123, chat_id=test_user_id, user_id=test_user_id)
    state = FSMContext(storage=storage, key=key)

    # Step 1
    await state.set_state(AccessRequestState.waiting_for_company)
    msg_step1 = MagicMock()
    msg_step1.from_user = MagicMock(id=test_user_id, full_name="Upwork Client", username="upwork_client")
    msg_step1.text = "US Structural Engineering Firm"
    msg_step1.answer = AsyncMock()

    await process_step1_company(msg_step1, state)
    assert await state.get_state() == AccessRequestState.waiting_for_details.state
    # Для рефералов разрешено и помечено как опционально
    step1_reply = msg_step1.answer.call_args[0][0]
    assert "опционально" in step1_reply.lower()

    # Нажатие 'Skip ⏭'
    msg_skip = MagicMock()
    msg_skip.from_user = msg_step1.from_user
    msg_skip.text = "Skip ⏭"
    msg_skip.answer = AsyncMock()
    msg_skip.bot = MagicMock()
    msg_skip.bot.send_message = AsyncMock()

    await process_step2_details(msg_skip, state)
    assert await state.get_state() is None

    req = await access_db.get_pending_request_by_user(test_user_id)
    assert req is not None
    assert req["company"] == "US Structural Engineering Firm"
    assert req["goal"] == ""


