"""
AxonBot Telegram Service Main Entrypoint
Инициализирует Dispatcher aiogram 3, регистрирует WhitelistMiddleware,
нативное меню команд bot.set_my_commands (RU / EN), роутеры access и base.
"""

import sys
import os
from pathlib import Path

# Автоматически добавляем пути к проекту в sys.path
BOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BOT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BOT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(BOT_DIR.parent))

import asyncio
import logging
import argparse
import ctypes
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeDefault
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from Scripts.bot.config import BOT_TOKEN, ADMIN_ID
from Scripts.bot.handlers import base, access
from Scripts.bot.middlewares.whitelist import WhitelistMiddleware
from Scripts.bot.utils.access_db import init_db
from Scripts.bot.utils.conversation_logger import set_session_path

PID_FILE = BOT_DIR / "data" / "axonbot.pid"

def kill_previous_instances():
    """Завершает предыдущие экземпляры бота во избежание конфликтов TelegramConflictError."""
    current_pid = os.getpid()
    if sys.platform == "win32":
        try:
            import subprocess
            cmd = f'powershell -Command "Get-CimInstance Win32_Process | Where-Object {{ ($_.Name -eq \'python.exe\' -or $_.Name -eq \'pythonw.exe\') -and $_.ProcessId -ne {current_pid} -and $_.CommandLine -like \'*Scripts.bot.main*\' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"'
            subprocess.run(cmd, shell=True, capture_output=True)
        except Exception:
            pass
    elif PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            if old_pid != current_pid:
                os.kill(old_pid, 9)
        except Exception:
            pass

def setup_process_identity():
    """Устанавливает понятное имя консольного окна/процесса и регистрирует PID."""
    kill_previous_instances()
    pid = os.getpid()
    title = f"AxonBot_Telegram_Service [PID: {pid}]"
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")
    return pid, title

DESC_RU = (
    "AxonBot — корпоративный AI BIM-ассистент по Revit КР (железобетон).\n\n"
    "База знаний: 100+ регламентов, технологических карт, параметров ADSK и стандартов армирования.\n\n"
    "Возможности:\n"
    "• Правила сопряжения монолита, опалубки и армирования.\n"
    "• Точные цитаты параметров ADSK и семейств.\n"
    "• Наглядные схемы узлов, чертежи и медиа-инструкции.\n"
    "• Проверка решений на запрещенные инструменты и коллизии.\n\n"
    "Разработчик: Иван Кирюшов (@bimivan)\n\n"
    "Приватное B2B-демо. Нажмите Start для заявки на доступ."
)

DESC_EN = (
    "AxonBot is an enterprise AI BIM Consultant for Revit Structural (KR).\n\n"
    "Knowledge Base: 100+ corporate codes, ADSK parameters, and rebar standards.\n\n"
    "Capabilities:\n"
    "• Monolithic jointing, formwork offsets, and rebar detailing.\n"
    "• Verbatim citations of ADSK parameters and families.\n"
    "• Visual node diagrams, shop drawings, and video guides.\n"
    "• Deterministic compliance checks against forbidden tools.\n\n"
    "Developer: Ivan Kiryushov (@bimivan)\n\n"
    "Private B2B demo. Press Start to request access."
)

ABOUT_RU = "AI BIM-эксперт по Revit КР (100+ регламентов). Hybrid RAG, схемы узлов и параметры ADSK в 1 клик."
ABOUT_EN = "AI BIM Consultant for Revit Structural (100+ codes). Hybrid RAG, rebar detailing & CAD OCR."

async def setup_bot_profile(bot: Bot):
    """
    Регистрирует нативное меню команд и мультиязычные описания в Telegram API.
    Пользователи с русской локализацией Telegram видят русский текст, остальные — английский.
    """
    commands_en = [
        BotCommand(command="start", description="Main Menu & Presentation"),
        BotCommand(command="examples", description="Benchmark Case Catalog"),
        BotCommand(command="lang", description="Switch Language (RU/EN)"),
        BotCommand(command="clear", description="Clear Conversation Memory"),
        BotCommand(command="request_access", description="Request Demo Access")
    ]
    
    commands_ru = [
        BotCommand(command="start", description="Главное меню и презентация"),
        BotCommand(command="examples", description="Каталог кейсов"),
        BotCommand(command="lang", description="Сменить язык интерфейса (RU/EN)"),
        BotCommand(command="clear", description="Очистить контекст диалога"),
        BotCommand(command="request_access", description="Запросить доступ к базе")
    ]
    
    try:
        # 1. Регистрация команд для пользователей (дефолт + RU)
        await bot.set_my_commands(commands_en, scope=BotCommandScopeDefault())
        await bot.set_my_commands(commands_ru, scope=BotCommandScopeDefault(), language_code="ru")

        # 2. Регистрация расширенного меню команд для администратора
        if ADMIN_ID and ADMIN_ID > 0:
            from aiogram.types import BotCommandScopeChat
            admin_commands = [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="examples", description="Каталог кейсов"),
                BotCommand(command="lang", description="Сменить язык (RU/EN)"),
                BotCommand(command="clear", description="Очистить контекст"),
                BotCommand(command="status", description="Статус RAG-системы"),
                BotCommand(command="whitelist", description="Управление доступом"),
                BotCommand(command="stats", description="Аналитика лидов")
            ]
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

        # 3. Регистрация описания до нажатия Start (дефолт + RU)
        await bot.set_my_description(description=DESC_EN)
        await bot.set_my_description(description=DESC_RU, language_code="ru")

        # 4. Регистрация инфо-блока профиля About (дефолт + RU)
        await bot.set_my_short_description(short_description=ABOUT_EN)
        await bot.set_my_short_description(short_description=ABOUT_RU, language_code="ru")

        logging.info("Мультиязычный профиль и меню команд успешно синхронизированы с серверами Telegram.")
    except Exception as e:
        logging.error(f"Не удалось настроить профиль в Telegram: {e}")

logging.basicConfig(level=logging.INFO)

async def main():
    """Основная точка входа в сервис AxonBot."""
    if not BOT_TOKEN:
        print("Ошибка: BOT_TOKEN не найден в конфиге!")
        return

    pid, process_title = setup_process_identity()

    # 1. Папка логов сессии
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = str(BOT_DIR / "data" / "logs" / f"session_{session_id}")
    os.makedirs(session_dir, exist_ok=True)
    
    file_handler = logging.FileHandler(os.path.join(session_dir, "system.log"), encoding="utf-8")
    stdout_handler = logging.StreamHandler()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[file_handler, stdout_handler],
        force=True
    )
    
    set_session_path(session_dir)

    # 2. Инициализация SQLite базы доступа и заявок
    await init_db(admin_id=ADMIN_ID)
    logging.info("SQLite база данных (axonbot.db) инициализирована.")

    # 3. Парсим аргументы командной строки
    parser = argparse.ArgumentParser()
    parser.add_argument("--reboot", type=int, help="ID чата для отчета о рестарте")
    args = parser.parse_args()

    # 4. Инициализация бота и диспетчера (с дефолтным HTML parse_mode)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # 5. Регистрация WhitelistMiddleware (проверка доступа и мультиязычности)
    whitelist_mw = WhitelistMiddleware()
    dp.message.middleware(whitelist_mw)
    dp.callback_query.middleware(whitelist_mw)

    # 6. Подключение роутеров
    dp.include_router(access.router)
    dp.include_router(base.router)

    # 7. Настройка нативного меню команд и профиля бота в Telegram
    await setup_bot_profile(bot)

    # 8. Очистка вебхуков
    await bot.delete_webhook(drop_pending_updates=True)

    # 9. Отчет о перезагрузке (если был флаг /reboot)
    if args.reboot:
        try:
            await bot.send_message(args.reboot, "<b>AxonBot успешно перезагружен и готов к работе.</b>", parse_mode="HTML")
            logging.info(f"Отправлен отчет о перезагрузке в чат {args.reboot}")
        except Exception as e:
            logging.error(f"Не удалось отправить отчет о перезагрузке: {e}")

    # 10. Уведомление администратора о старте сервиса
    if ADMIN_ID and ADMIN_ID > 0 and not args.reboot:
        try:
            startup_msg = (
                f"<b>AxonBot v1.9.5 запущен</b>\n\n"
                f"• PID: <code>{pid}</code>\n"
                f"• Режим: <code>B2B Production / Whitelist</code>\n"
                f"• База доступа: <code>axonbot.db (ONLINE)</code>\n"
                f"• Время старта: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            )
            await bot.send_message(ADMIN_ID, startup_msg, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление о запуске админу: {e}")

    # 11. Запуск Long Polling
    print(f"AxonBot запущен! Сессия: {session_id}, PID: {pid}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен пользователем.")