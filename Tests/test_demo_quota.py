"""
Unit and integration tests for Stage 2.1.0: Demo Quota & Budget Guard.
Tests:
- SQLite auto-migration and atomic quota consumption
- Cutoff at DEFAULT_DEMO_QUOTA (15 queries)
- Quota refund on RAG pipeline failure
- Admin quota modifications (add, set, reset)
- Single-Flight lock and 3.0s cooldown rate limiting in WhitelistMiddleware
- Master-Detail whitelist retrieval and user detail formatting
- /quota balance card display logic
"""

import sys
import os
import time
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aiogram import types
from Scripts.bot.utils import access_db
from Scripts.bot.middlewares.whitelist import WhitelistMiddleware
from Scripts.bot.handlers import base, access
from Scripts.bot.utils.i18n import t
from Scripts.bot.config import DEFAULT_DEMO_QUOTA

@pytest.mark.asyncio
async def test_db_quota_atomic_consumption(tmp_path):
    """Тест атомарного списания квоты, границы лимита и возврата (refund)."""
    test_db = tmp_path / "test_quota_db.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    guest_id = 12345678
    user = await access_db.get_or_create_user(guest_id, "test_guest", "Test Guest")
    assert user["queries_used"] == 0
    assert user["queries_limit"] is None

    # Потребляем ровно 15 запросов
    for i in range(1, DEFAULT_DEMO_QUOTA + 1):
        allowed, used, limit = await access_db.check_and_consume_quota(guest_id, role="guest", default_limit=15)
        assert allowed is True
        assert used == i
        assert limit == 15

    # 16-й запрос должен быть заблокирован
    allowed, used, limit = await access_db.check_and_consume_quota(guest_id, role="guest", default_limit=15)
    assert allowed is False
    assert used == 15
    assert limit == 15

    # Проверяем refund_quota (например, при сбое n8n)
    await access_db.refund_quota(guest_id)
    quota_info = await access_db.get_user_quota(guest_id, default_limit=15)
    assert quota_info["used"] == 14
    assert quota_info["remaining"] == 1

    # Теперь 15-й запрос снова проходит
    allowed, used, limit = await access_db.check_and_consume_quota(guest_id, role="guest", default_limit=15)
    assert allowed is True
    assert used == 15

    # И снова блокировка
    allowed, used, limit = await access_db.check_and_consume_quota(guest_id, role="guest", default_limit=15)
    assert allowed is False

@pytest.mark.asyncio
async def test_admin_and_exempt_quota(tmp_path):
    """Тест того, что администраторы и exempt-пользователи имеют вечный безлимит."""
    test_db = tmp_path / "test_admin_quota.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    # Админ
    allowed, used, limit = await access_db.check_and_consume_quota(admin_id, role="admin", default_limit=15)
    assert allowed is True
    assert used == 1
    assert limit == -1

    quota = await access_db.get_user_quota(admin_id)
    assert quota["is_unlimited"] is True
    assert quota["limit"] == -1

    # Exempt гость
    exempt_guest = 555444333
    await access_db.get_or_create_user(exempt_guest, "exempt_user", "Exempt User")
    allowed, used, limit = await access_db.check_and_consume_quota(exempt_guest, role="guest", is_exempt=True)
    assert allowed is True
    assert limit == -1

@pytest.mark.asyncio
async def test_admin_quota_management(tmp_path):
    """Тест функций административного управления квотами: set_quota, add_quota, reset_quota."""
    test_db = tmp_path / "test_mgmt_db.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    guest_id = 88877766
    await access_db.get_or_create_user(guest_id, "managed_guest", "Managed Guest")

    # Списываем 10 запросов
    for _ in range(10):
        await access_db.check_and_consume_quota(guest_id, default_limit=15)

    q = await access_db.get_user_quota(guest_id, default_limit=15)
    assert q["used"] == 10
    assert q["remaining"] == 5

    # Добавляем +15 запросов (станет лимит 15 + 15 = 30)
    success, new_lim = await access_db.add_user_quota(guest_id, 15, default_base=15)
    assert success is True
    assert new_lim == 30

    q = await access_db.get_user_quota(guest_id)
    assert q["limit"] == 30
    assert q["remaining"] == 20

    # Сбрасываем использованные запросы
    assert await access_db.reset_user_quota(guest_id) is True
    q = await access_db.get_user_quota(guest_id)
    assert q["used"] == 0
    assert q["remaining"] == 30

    # Устанавливаем персональный лимит -1 (безлимит)
    assert await access_db.set_user_quota(guest_id, -1) is True
    q = await access_db.get_user_quota(guest_id)
    assert q["is_unlimited"] is True

@pytest.mark.asyncio
async def test_whitelist_pagination_and_detail(tmp_path):
    """Тест выборки пользователей по страницам (Master View) и детальной карточки (Detail View)."""
    test_db = tmp_path / "test_paged_db.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    # Создаем 7 пользователей
    for i in range(1, 8):
        uid = 1000 + i
        await access_db.get_or_create_user(uid, f"user_{i}", f"User {i}")

    # Первая страница (размер 5)
    page0, total = await access_db.get_all_users_paged(page=0, page_size=5)
    assert total == 8  # 1 admin + 7 guests
    assert len(page0) == 5
    # Админ должен идти первым
    assert page0[0]["role"] == "admin"

    # Вторая страница
    page1, total = await access_db.get_all_users_paged(page=1, page_size=5)
    assert len(page1) == 3

    # Детальная карточка
    detail = await access_db.get_user_detail(1001)
    assert detail is not None
    assert detail["user_id"] == 1001
    assert detail["role"] == "guest"

@pytest.mark.asyncio
async def test_middleware_single_flight_and_cooldown(tmp_path):
    """Тест защиты от параллельных запросов (Single-Flight) и кулдауна (3.0s)."""
    test_db = tmp_path / "test_middleware_db.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    user_id = 333222111
    await access_db.get_or_create_user(user_id, "concurrency_user", "Concurrency User")

    middleware = WhitelistMiddleware()

    # 1. Симулируем первое сообщение
    event1 = MagicMock(spec=types.Message)
    event1.voice = None
    event1.audio = None
    event1.video = None
    event1.video_note = None
    event1.contact = None
    event1.from_user = MagicMock(id=user_id, language_code="ru", full_name="Concurrency User", username="concurrency_user")
    event1.answer = AsyncMock()
    event1.text = "Как сопрягать стены?"

    started = asyncio.Event()
    finish = asyncio.Event()

    # Handler, который сигнализирует о старте и ждет команды завершения
    async def slow_handler(event, data):
        started.set()
        await finish.wait()
        return "rag_result"

    # Запускаем первый запрос в фоне
    task1 = asyncio.create_task(middleware(slow_handler, event1, {}))
    await started.wait()  # Гарантированно ждем входа в slow_handler и захвата лока

    event2 = MagicMock(spec=types.Message)
    event2.voice = None
    event2.audio = None
    event2.video = None
    event2.video_note = None
    event2.contact = None
    event2.from_user = event1.from_user
    event2.answer = AsyncMock()
    event2.text = "Второй параллельный запрос"

    async def fast_handler(event, data):
        return "should_not_run"

    # Второй параллельный запрос должен быть отклонен Single-Flight локом
    result2 = await middleware(fast_handler, event2, {})
    assert result2 is None
    assert event2.answer.call_count == 1
    # Проверяем, что в ответе нет эмодзи и есть точное предупреждение
    assert event2.answer.call_args[0][0] == t("single_flight_warning", "ru")

    # Завершаем первый запрос
    finish.set()
    result1 = await task1
    assert result1 == "rag_result"

    # 2. Сразу после первого пробуем отправить еще один — должен сработать 3-секундный кулдаун
    event3 = MagicMock(spec=types.Message)
    event3.voice = None
    event3.audio = None
    event3.video = None
    event3.video_note = None
    event3.contact = None
    event3.from_user = event1.from_user
    event3.answer = AsyncMock()
    event3.text = "Третий запрос сразу"

    result3 = await middleware(fast_handler, event3, {})
    assert result3 is None
    assert event3.answer.call_count == 1
    assert "Пожалуйста, подождите" in event3.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_cmd_quota_display(tmp_path):
    """Тест отображения карточки квоты для гостя, авторизованного пользователя и администратора."""
    test_db = tmp_path / "test_quota_cmd.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    guest_id = 77711122
    await access_db.get_or_create_user(guest_id, "guest_user", "Guest User")

    msg = MagicMock()
    msg.from_user = MagicMock(id=guest_id)
    msg.answer = AsyncMock()

    # Проверяем карточку квоты для гостя (15 запросов)
    await base.cmd_quota(msg)
    assert msg.answer.call_count == 1
    text = msg.answer.call_args[0][0]
    assert "Ознакомительный доступ" in text
    assert "15" in text
    assert "0" in text

    # Авторизуем пользователя (квота 100 запросов)
    req_id = await access_db.create_access_request(guest_id, company="Test BIM")
    await access_db.resolve_access_request(req_id, approve=True, admin_id=admin_id)

    msg_auth = MagicMock()
    msg_auth.from_user = MagicMock(id=guest_id)
    msg_auth.answer = AsyncMock()

    await base.cmd_quota(msg_auth)
    assert msg_auth.answer.call_count == 1
    text_auth = msg_auth.answer.call_args[0][0]
    assert "Полный доступ" in text_auth
    assert "100" in text_auth

    # Проверяем карточку квоты для администратора (вечный безлимит)
    msg_admin = MagicMock()
    msg_admin.from_user = MagicMock(id=admin_id)
    msg_admin.answer = AsyncMock()

    await base.cmd_quota(msg_admin)
    assert msg_admin.answer.call_count == 1
    text_admin = msg_admin.answer.call_args[0][0]
    assert "Безлимит" in text_admin

@pytest.mark.asyncio
async def test_admin_commands_and_master_detail_callbacks(tmp_path):
    """Тест Master-Detail навигации в /whitelist и административных команд квот."""
    test_db = tmp_path / "test_admin_ui.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    target_user_id = 44433322
    await access_db.get_or_create_user(target_user_id, "test_target", "Target User")

    # 1. /whitelist выводит Master View
    msg_wl = MagicMock()
    msg_wl.from_user = MagicMock(id=admin_id)
    msg_wl.answer = AsyncMock()

    with patch("Scripts.bot.handlers.access.ADMIN_ID", admin_id):
        await access.cmd_admin_whitelist(msg_wl)
        assert msg_wl.answer.call_count == 1
        wl_text = msg_wl.answer.call_args[0][0]
        assert "Управление доступом и квотами" in wl_text
        assert "Всего пользователей" in wl_text

        # 2. Callback wlu: открывает Detail View
        cb_detail = MagicMock(spec=types.CallbackQuery)
        cb_detail.from_user = MagicMock(id=admin_id)
        cb_detail.data = f"wlu:{target_user_id}:0"
        cb_detail.message = MagicMock()
        cb_detail.message.edit_text = AsyncMock()
        cb_detail.answer = AsyncMock()

        await access.handle_callback_whitelist_user(cb_detail)
        assert cb_detail.message.edit_text.call_count == 1
        detail_text = cb_detail.message.edit_text.call_args[0][0]
        assert "Target User" in detail_text
        assert str(target_user_id) in detail_text

        # 3. Callback qa: добавляет +15
        cb_add = MagicMock(spec=types.CallbackQuery)
        cb_add.from_user = MagicMock(id=admin_id)
        cb_add.data = f"qa:{target_user_id}:0"
        cb_add.message = MagicMock()
        cb_add.message.edit_text = AsyncMock()
        cb_add.answer = AsyncMock()

        await access.handle_callback_quota_add(cb_add)
        q = await access_db.get_user_quota(target_user_id)
        assert q["limit"] == 30

        # 3.1. Callback qm: отнимает -15
        cb_sub = MagicMock(spec=types.CallbackQuery)
        cb_sub.from_user = MagicMock(id=admin_id)
        cb_sub.data = f"qm:{target_user_id}:0"
        cb_sub.message = MagicMock()
        cb_sub.message.edit_text = AsyncMock()
        cb_sub.answer = AsyncMock()

        await access.handle_callback_quota_minus(cb_sub)
        q = await access_db.get_user_quota(target_user_id)
        assert q["limit"] == 15

        # 3.2. Callback qd: сбрасывает квоту на дефолт 15
        cb_def = MagicMock(spec=types.CallbackQuery)
        cb_def.from_user = MagicMock(id=admin_id)
        cb_def.data = f"qd:{target_user_id}:0"
        cb_def.message = MagicMock()
        cb_def.message.edit_text = AsyncMock()
        cb_def.answer = AsyncMock()

        await access.handle_callback_quota_default(cb_def)
        q = await access_db.get_user_quota(target_user_id)
        assert q["limit"] == 15

        # 4. Команда /set_quota
        msg_sq = MagicMock()
        msg_sq.from_user = MagicMock(id=admin_id)
        msg_sq.text = f"/set_quota {target_user_id} 50"
        msg_sq.answer = AsyncMock()

        await access.cmd_admin_set_quota(msg_sq)
        q = await access_db.get_user_quota(target_user_id)
        assert q["limit"] == 50

        # 5. Команда /stats содержит счетчики квот
        msg_stats = MagicMock()
        msg_stats.from_user = MagicMock(id=admin_id)
        msg_stats.answer = AsyncMock()

        await access.cmd_admin_stats(msg_stats)
        assert msg_stats.answer.call_count == 1
        stats_text = msg_stats.answer.call_args[0][0]
        assert "Всего RAG-запросов выполнено" in stats_text
        assert "Гостей с исчерпанной квотой" in stats_text


@pytest.mark.asyncio
async def test_authorized_user_quota_math_and_revocation(tmp_path):
    """Тест точного расчета квоты для авторизованного пользователя: 100 + 15 = 115 (а не 30)."""
    test_db = tmp_path / "test_auth_math_db.db"
    access_db.set_db_path(test_db)
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)

    user_id = 11223344
    await access_db.get_or_create_user(user_id, "lead_candidate", "Lead Candidate")

    # Пользователь подает заявку
    req_id = await access_db.create_access_request(
        user_id, 
        source="direct", 
        company="BIM Enterprise", 
        email="bim@example.com", 
        contact_phone="+79991234567", 
        goal="Оценка внедрения для компании"
    )

    # Администратор одобряет заявку -> квота должна стать 100
    res = await access_db.resolve_access_request(req_id, approve=True, admin_id=admin_id)
    assert res is not None
    assert res[0] == user_id

    q_approved = await access_db.get_user_quota(user_id)
    assert q_approved["limit"] == 100

    # Нажимаем +15 (qa:) -> должно стать 115, а НЕ 30!
    with patch("Scripts.bot.handlers.access.ADMIN_ID", admin_id):
        cb_add = MagicMock(spec=types.CallbackQuery)
        cb_add.from_user = MagicMock(id=admin_id)
        cb_add.data = f"qa:{user_id}:0"
        cb_add.message = MagicMock()
        cb_add.message.edit_text = AsyncMock()
        cb_add.answer = AsyncMock()

        await access.handle_callback_quota_add(cb_add)
        q_after_add = await access_db.get_user_quota(user_id)
        assert q_after_add["limit"] == 115

        # Нажимаем -15 (qm:) -> должно стать 100
        cb_sub = MagicMock(spec=types.CallbackQuery)
        cb_sub.from_user = MagicMock(id=admin_id)
        cb_sub.data = f"qm:{user_id}:0"
        cb_sub.message = MagicMock()
        cb_sub.message.edit_text = AsyncMock()
        cb_sub.answer = AsyncMock()

        await access.handle_callback_quota_minus(cb_sub)
        q_after_sub = await access_db.get_user_quota(user_id)
        assert q_after_sub["limit"] == 100

        # Нажимаем Дефолт 15 (qd:) -> должно стать 15
        cb_def = MagicMock(spec=types.CallbackQuery)
        cb_def.from_user = MagicMock(id=admin_id)
        cb_def.data = f"qd:{user_id}:0"
        cb_def.message = MagicMock()
        cb_def.message.edit_text = AsyncMock()
        cb_def.answer = AsyncMock()

        await access.handle_callback_quota_default(cb_def)
        q_after_def = await access_db.get_user_quota(user_id)
        assert q_after_def["limit"] == 15

    # Отзываем доступ -> роль guest, лимит сбрасывается на 15
    revoked = await access_db.revoke_user_access(user_id, admin_id)
    assert revoked is True
    u = await access_db.get_user_detail(user_id)
    assert u["role"] == "guest"
    assert u["queries_limit"] == 15
