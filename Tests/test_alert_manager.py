"""
Unit tests for Stage 2.2.1: Control Room & Topics Alerting Integration.
Tests routing of operational events to TOPIC_AXONBOT_ID (13) and critical errors to TOPIC_CRITICAL_ID (4),
three-tier fallback when bot is not yet in supergroup, and new user / quota exhaustion telemetry.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from Scripts.bot.utils import alert_manager, access_db
from Scripts.bot.config import CONTROL_CHAT_ID, TOPIC_AXONBOT_ID, TOPIC_CRITICAL_ID


@pytest.mark.asyncio
async def test_send_ops_event_primary_topic():
    """Проверка отправки операционного события напрямую в топик 🤖 AxonBot & AI Ops (thread_id=13)."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    ok = await alert_manager.send_ops_event(
        bot=mock_bot,
        text="<b>Тестовое событие</b>",
        silent=True
    )
    assert ok is True
    assert mock_bot.send_message.call_count == 1
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == CONTROL_CHAT_ID
    assert call_kwargs["message_thread_id"] == TOPIC_AXONBOT_ID
    assert call_kwargs["disable_notification"] is True


@pytest.mark.asyncio
async def test_send_critical_alert_routes_to_critical_topic_with_sound():
    """Проверка маршрутизации критических ошибок в топик 🚨 Critical Alerts (thread_id=4) со звуком."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    ok = await alert_manager.send_critical_alert(
        bot=mock_bot,
        title="Сбой RAG-сервера (n8n)",
        details="HTTP 500 Internal Server Error"
    )
    assert ok is True
    assert mock_bot.send_message.call_count == 1
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == CONTROL_CHAT_ID
    assert call_kwargs["message_thread_id"] == TOPIC_CRITICAL_ID
    assert call_kwargs["disable_notification"] is False
    assert "🚨" in call_kwargs["text"]
    assert "Сбой RAG-сервера (n8n)" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_interactive_card_fallback_to_admin_when_bot_not_in_group():
    """
    Если боевой бот @bim_axonbot еще не добавлен в супергруппу Control Room (ошибка send_message в CONTROL_CHAT_ID),
    интерактивная карточка с inline-кнопками должна автоматически уходить в ADMIN_ID того же бота,
    чтобы кнопки [Одобрить]/[Отклонить] продолжали работать.
    """
    mock_bot = MagicMock()

    async def side_effect(**kwargs):
        if kwargs.get("chat_id") == CONTROL_CHAT_ID:
            raise RuntimeError("TelegramBadRequest: Bad Request: chat not found")
        return True

    mock_bot.send_message = AsyncMock(side_effect=side_effect)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить", callback_data="approve:1")]])

    with patch("Scripts.bot.utils.alert_manager.ADMIN_ID", 9990001), \
         patch("Scripts.bot.utils.alert_manager.send_sync_alert", return_value=True) as mock_sync:
        ok = await alert_manager.send_ops_event(
            bot=mock_bot,
            text="<b>Новая заявка</b>",
            reply_markup=kb,
            silent=False
        )
        assert ok is True
        # Первая попытка — в CONTROL_CHAT_ID, вторая — fallback в ADMIN_ID (9990001)
        assert mock_bot.send_message.call_count == 2
        second_call = mock_bot.send_message.call_args_list[1].kwargs
        assert second_call["chat_id"] == 9990001
        assert second_call["reply_markup"] == kb
        assert mock_sync.call_count == 1


@pytest.mark.asyncio
async def test_is_new_user_flag_in_access_db(tmp_path):
    """Проверка флага _is_new при первом входе уникального пользователя и повторном входе."""
    test_db = tmp_path / "test_new_flag.db"
    access_db.set_db_path(test_db)
    await access_db.init_db(admin_id=9990001)

    u1 = await access_db.get_or_create_user(777888, "new_guest", "New Guest", source="hh")
    assert u1["_is_new"] is True

    u2 = await access_db.get_or_create_user(777888, "new_guest", "New Guest", source="hh")
    assert u2["_is_new"] is False


@pytest.mark.asyncio
async def test_benchmark_refusal_triggers_critical_alert():
    """Проверка отправки критического алерта при отказе на эталонный кейс (детекция гибернации Qdrant)."""
    from Scripts.bot.handlers import base

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    with patch("Scripts.bot.handlers.base.call_n8n_rag", new_callable=AsyncMock) as mock_rag, \
         patch("Scripts.bot.handlers.base.send_critical_alert", new_callable=AsyncMock) as mock_alert, \
         patch("Scripts.bot.handlers.base.send_ops_event", new_callable=AsyncMock):
        
        # Симулируем отказ модели из-за пустой выдачи n8n/Qdrant
        mock_rag.return_value = "В базе знаний регламентов КР нет информации по данному вопросу. Уточните запрос или обратитесь к BIM-координатору."

        await base.execute_rag_pipeline(
            bot=mock_bot,
            chat_id=12345,
            user_id=12345,
            user_name="Test User",
            query_text="Какой защитный слой у фундаментной плиты?",
            lang="ru",
            is_benchmark=True
        )

        assert mock_alert.call_count == 1
        alert_args = mock_alert.call_args.kwargs
        assert "Возможная гибернация Qdrant" in alert_args["title"]
        assert "Benchmark Refusal" in alert_args["details"]


@pytest.mark.asyncio
async def test_consecutive_refusals_threshold_triggers_critical_alert():
    """Проверка отправки критического алерта при достижении 3 отказов подряд на обычных вопросах."""
    from Scripts.bot.handlers import base

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    with patch("Scripts.bot.handlers.base.call_n8n_rag", new_callable=AsyncMock) as mock_rag, \
         patch("Scripts.bot.handlers.base.send_critical_alert", new_callable=AsyncMock) as mock_alert, \
         patch("Scripts.bot.handlers.base.send_ops_event", new_callable=AsyncMock):
        
        # Сбрасываем счетчик
        base._consecutive_refusals = 0
        mock_rag.return_value = "В базе знаний нет информации."

        # 1-й отказ
        await base.execute_rag_pipeline(mock_bot, 12345, 12345, "User", "Вопрос 1", "ru", is_benchmark=False)
        assert mock_alert.call_count == 0
        assert base._consecutive_refusals == 1

        # 2-й отказ
        await base.execute_rag_pipeline(mock_bot, 12345, 12345, "User", "Вопрос 2", "ru", is_benchmark=False)
        assert mock_alert.call_count == 0
        assert base._consecutive_refusals == 2

        # 3-й отказ — срабатывает критический алерт
        await base.execute_rag_pipeline(mock_bot, 12345, 12345, "User", "Вопрос 3", "ru", is_benchmark=False)
        assert mock_alert.call_count == 1
        assert "Серия отказов RAG подряд (3+)" in mock_alert.call_args.kwargs["title"]
        assert base._consecutive_refusals == 3

        # Успешный ответ сбрасывает счетчик
        mock_rag.return_value = "Защитный слой составляет 50 мм согласно п. 4.2."
        await base.execute_rag_pipeline(mock_bot, 12345, 12345, "User", "Вопрос 4", "ru", is_benchmark=False)
        assert base._consecutive_refusals == 0
