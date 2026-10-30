"""
AxonBot Centralized Monitoring & Alerting Dispatcher
====================================================
Маршрутизирует все операционные события, карточки лидов, телеметрию RAG
и критические сбои в супергруппу Control Room по профильным топикам:
- TOPIC_AXONBOT_ID (thread_id=13: 🤖 AxonBot & AI Ops) — заявки, новые пользователи, вопросы, квоты.
- TOPIC_CRITICAL_ID (thread_id=4: 🚨 Critical Alerts) — ошибки n8n, сбои Confluence, падения хендлеров и воркеров.

Обеспечивает 3-уровневую отказоустойчивость (Three-Tier Fallback):
1. Отправка через боевого бота AxonBot (`bot.send_message`) в нужный топик `CONTROL_CHAT_ID`.
2. Если боевой бот еще не добавлен в супергруппу:
   - для сообщений БЕЗ inline-кнопок — мгновенная доставка в тот же топик через сервисного бота (`tg_alert.py` / `@ericaHelpbot`);
   - для интерактивных карточек заявок С inline-кнопками (`approve:`/`reject:`) — fallback в личку `ADMIN_ID` самого `AxonBot`, чтобы кнопки гарантированно срабатывали.
3. Крайний резерв — отправка в `ADMIN_ID`.
"""

import sys
import asyncio
import json
import html
import logging
import traceback
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from Scripts.bot.config import (
    BOT_TOKEN,
    ADMIN_ID,
    CONTROL_CHAT_ID,
    TOPIC_AXONBOT_ID,
    TOPIC_CRITICAL_ID,
    ALERT_BOT_TOKEN,
)

# Путь к локальному конфигу D:\Dev\Tools\tg_config.json (если запущено на рабочей станции Windows)
LOCAL_TG_CONFIG_PATH = Path(r"D:\Dev\Tools\tg_config.json")


def _get_service_alert_credentials() -> tuple[str, int, dict]:
    """
    Возвращает (service_bot_token, chat_id, topics_map) для синхронной/фоновой отправки.
    Приоритет: переменные окружения -> D:\\Dev\\Tools\\tg_config.json -> BOT_TOKEN.
    """
    token = ALERT_BOT_TOKEN
    chat_id = CONTROL_CHAT_ID
    topics = {
        "AXONBOT": TOPIC_AXONBOT_ID,
        "AI": TOPIC_AXONBOT_ID,
        "CRITICAL": TOPIC_CRITICAL_ID,
    }

    if LOCAL_TG_CONFIG_PATH.exists():
        try:
            with open(LOCAL_TG_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if not token and cfg.get("bot_token") and "YOUR_" not in cfg["bot_token"]:
                    token = cfg["bot_token"].strip()
                if not chat_id and cfg.get("chat_id") and "YOUR_" not in str(cfg["chat_id"]):
                    chat_id = int(cfg["chat_id"])
                if isinstance(cfg.get("topics"), dict):
                    for k, v in cfg["topics"].items():
                        if v is not None:
                            topics[k.upper()] = int(v)
        except Exception as e:
            logging.debug(f"[alert_manager] Не удалось прочитать {LOCAL_TG_CONFIG_PATH}: {e}")

    if not token:
        token = (BOT_TOKEN or "").strip()

    return token, chat_id, topics


def send_sync_alert(
    text: str,
    topic_id: int | str | None = "AXONBOT",
    silent: bool = False,
    parse_mode: str = "HTML"
) -> bool:
    """
    Синхронная отправка уведомления через стандартный urllib (0 внешних зависимостей).
    Используется в фоновых воркерах (agent_review_worker.py, media_sanitizer.py),
    глобальном перехватчике sys.excepthook и как fallback для бота.
    """
    if "pytest" in sys.modules:
        return True

    token, chat_id, topics_map = _get_service_alert_credentials()
    if not token or not chat_id:
        return False

    resolved_thread_id: int | None = None
    if isinstance(topic_id, int):
        resolved_thread_id = topic_id if topic_id > 0 else None
    elif isinstance(topic_id, str):
        key = topic_id.upper().strip()
        if key.isdigit():
            resolved_thread_id = int(key)
        else:
            resolved_thread_id = topics_map.get(key)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_notification": silent,
    }
    if resolved_thread_id:
        payload["message_thread_id"] = resolved_thread_id

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode() == 200
    except Exception as e:
        logging.warning(f"[alert_manager] Ошибка синхронной отправки в Control Room: {e}")
        return False


def send_sync_critical(title: str, details: str) -> bool:
    """Синхронная отправка критического алерта со звуком в топик 🚨 Critical Alerts."""
    msg = f"🚨 <b>[AxonBot Critical] {title}</b>\n\n{details}"
    return send_sync_alert(msg, topic_id=TOPIC_CRITICAL_ID, silent=False)


async def send_ops_event(
    bot: Any,
    text: str,
    reply_markup: Any = None,
    silent: bool = True,
    topic_id: int | None = None,
    parse_mode: str = "HTML",
) -> bool:
    """
    Асинхронная отправка операционного события (заявки, вопросы, квоты, старт)
    в топик 🤖 AxonBot & AI Ops (или указанный topic_id).
    """
    target_topic = topic_id if topic_id is not None else TOPIC_AXONBOT_ID

    # 1. Основная попытка: отправка самим ботом AxonBot в топик супергруппы Control Room
    if bot and CONTROL_CHAT_ID:
        try:
            kwargs: dict[str, Any] = {
                "chat_id": CONTROL_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": silent,
            }
            if target_topic and target_topic > 0:
                kwargs["message_thread_id"] = target_topic
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup

            await bot.send_message(**kwargs)
            return True
        except Exception as e:
            logging.warning(
                f"[alert_manager] Боевой бот не смог отправить сообщение в Control Room ({CONTROL_CHAT_ID}, topic={target_topic}): {e}"
            )

    # 2. Если есть inline-клавиатура (например, кнопки Одобрить/Отклонить заявку),
    # отправлять её через чужого бота (@ericaHelpbot) нельзя — иначе клик по кнопке не дойдет до AxonBot!
    # Поэтому отправляем карточку через самого AxonBot в личку ADMIN_ID, а в Control Room кидаем маячок.
    if reply_markup is not None and bot and ADMIN_ID and ADMIN_ID > 0:
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_notification=silent,
            )
            send_sync_alert(
                f"ℹ️ <i>Карточка с интерактивными кнопками доставлена в ЛС админа (добавьте @bim_axonbot в Control Room для управления прямо из топика):</i>\n\n{text}",
                topic_id=target_topic,
                silent=silent,
                parse_mode=parse_mode,
            )
            return True
        except Exception as e:
            logging.error(f"[alert_manager] Не удалось отправить интерактивную карточку админу {ADMIN_ID}: {e}")

    # 3. Для всех остальных уведомлений без кнопок — отправляем в Control Room через сервисный токен (tg_alert)
    if send_sync_alert(text, topic_id=target_topic, silent=silent, parse_mode=parse_mode):
        return True

    # 4. Крайний fallback в личку ADMIN_ID
    if bot and ADMIN_ID and ADMIN_ID > 0:
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_notification=silent,
            )
            return True
        except Exception as e:
            logging.error(f"[alert_manager] Полный отказ доставки уведомления админу: {e}")

    return False


async def send_critical_alert(
    bot: Any,
    title: str,
    details: str,
) -> bool:
    """
    Асинхронная отправка критического инцидента со звуком в топик 🚨 Critical Alerts (thread_id=4).
    """
    alert_text = f"🚨 <b>[AxonBot Critical] {title}</b>\n\n{details}"
    return await send_ops_event(
        bot=bot,
        text=alert_text,
        reply_markup=None,
        silent=False,
        topic_id=TOPIC_CRITICAL_ID,
        parse_mode="HTML",
    )


def setup_crash_reporter(service_name: str = "AxonBot") -> None:
    """
    Активирует глобальный перехватчик необработанных исключений sys.excepthook.
    При наличии D:\\Dev\\Tools\\tg_alert.py использует его напрямую, иначе встроенный send_sync_alert.
    """
    tools_dir = Path(r"D:\Dev\Tools")
    if tools_dir.exists() and str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))

    try:
        import tg_alert
        tg_alert.enable_crash_reporter(topic="CRITICAL")
        logging.info(f"[{service_name}] Глобальный перехватчик tg_alert.enable_crash_reporter активирован.")
        return
    except Exception:
        pass

    # Автономный перехватчик (для Linux VPS, где нет папки D:\Dev\Tools)
    def _autonomous_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        tb_text = "".join(tb_lines)
        script_name = Path(sys.argv[0]).name if sys.argv else service_name

        safe_err = html.escape(str(exc_value))
        safe_tb = html.escape(tb_text[-1800:])

        err_msg = (
            f"💥 <b>CRASH REPORT in <code>{script_name}</code> ({service_name})</b>\n\n"
            f"<b>Exception:</b> <code>{exc_type.__name__}</code>\n"
            f"<b>Detail:</b> <code>{safe_err}</code>\n\n"
            f"<b>Traceback:</b>\n<pre>{safe_tb}</pre>"
        )
        try:
            send_sync_alert(err_msg, topic_id=TOPIC_CRITICAL_ID, silent=False)
        except Exception:
            pass

        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _autonomous_excepthook
    logging.info(f"[{service_name}] Автономный перехватчик крашей (Control Room) активирован.")


async def run_qdrant_keepalive_loop(bot: Any, interval_seconds: int = 12 * 3600) -> None:
    """
    Фоновый воркер (Keep-Alive): раз в 12 часов делает прогревочный эталонный запрос в n8n RAG,
    предотвращая уход кластера Qdrant Cloud (Free Tier) в спящий режим из-за неактивности.
    При сбое или отказе немедленно шлет критический алерт в Control Room (thread_id: 4).
    """
    if "pytest" in sys.modules:
        return

    # Задержка 45 секунд после старта сервиса перед первым пингом
    await asyncio.sleep(45)

    while True:
        try:
            from Scripts.bot.utils.n8n_client import call_n8n_rag
            ping_question = "Какой защитный слой у фундаментной плиты?"
            logging.info("[Keep-Alive] Отправка контрольного пинга в n8n RAG для прогрева Qdrant Cloud...")
            resp = await call_n8n_rag(user_text=ping_question, chat_id=0, category="keepalive")
            
            refusal_markers = ["нет информации", "не регламентир", "no guideline", "no information"]
            if not resp or any(m in resp.lower() for m in refusal_markers) or "Ошибка" in resp:
                logging.warning(f"[Keep-Alive] Qdrant / n8n вернул отказ или ошибку: {resp}")
                await send_critical_alert(
                    bot=bot,
                    title="Qdrant Cloud / RAG: Keep-Alive пинг выявил гибернацию базы!",
                    details=(
                        f"• <b>Контрольный вопрос:</b> <i>«{ping_question}»</i>\n"
                        f"• <b>Ответ системы:</b> <code>{resp[:250] if resp else 'EMPTY'}</code>\n\n"
                        f"⚠️ <b>Диагноз:</b> Кластер Qdrant Cloud, вероятно, ушел в спящий режим (Hibernation) из-за неактивности.\n"
                        f"👉 <b>Действие:</b> Зайдите в Qdrant Cloud Console и реактивируйте кластер!"
                    )
                )
            else:
                logging.info("[Keep-Alive] RAG пинг успешен. Кластер Qdrant активен, ретривал в норме.")
        except Exception as e:
            logging.error(f"[Keep-Alive] Ошибка при выполнении пинга: {e}")
            try:
                await send_critical_alert(
                    bot=bot,
                    title="Qdrant Keep-Alive: Сбой сетевого соединения с n8n",
                    details=f"• <b>Ошибка:</b> <code>{e}</code>"
                )
            except Exception:
                pass

        await asyncio.sleep(interval_seconds)

