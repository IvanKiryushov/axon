import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Автоматически находим пути к проекту
BOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BOT_DIR.parent.parent

# Загружаем .env из корня проекта или папки бота
if (PROJECT_ROOT / ".env").exists():
    load_dotenv(PROJECT_ROOT / ".env")
elif (BOT_DIR / ".env").exists():
    load_dotenv(BOT_DIR / ".env")
else:
    load_dotenv()

# Настройки бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
N8N_RAG_URL = os.getenv("N8N_RAG_URL", "http://localhost:5678/webhook/rag")

# Настройки Confluence для авторизованного скачивания вложений в память
CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL", os.getenv("CONFLUENCE_URL", "https://docs.yourcompany.internal"))
CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
CONFLUENCE_SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY", "BIM_KR")
CONFLUENCE_PARENT_PAGE_ID = os.getenv("CONFLUENCE_PARENT_PAGE_ID", "590112")

# Нейтральный корпоративный домен для отображения источников клиентам в Telegram (OpSec)
CONFLUENCE_PUBLIC_URL = os.getenv("CONFLUENCE_PUBLIC_URL", "https://wiki.axonbim.internal")

# ID администратора (для команды /reboot и получения заявок)
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
except ValueError:
    ADMIN_ID = 0

# Официальная инвайт-ссылка на пространство Confluence (задается в .env)
CONFLUENCE_INVITE_LINK = os.getenv("CONFLUENCE_INVITE_LINK", "")

# Версия приложения (SSOT)
BOT_VERSION = os.getenv("BOT_VERSION", "2.1.0")

# Настройки квот и защиты API-бюджета (Demo Quota & Budget Guard)
DEFAULT_DEMO_QUOTA = int(os.getenv("DEFAULT_DEMO_QUOTA", 15))
DEFAULT_AUTH_QUOTA = int(os.getenv("DEFAULT_AUTH_QUOTA", 100))
RATE_LIMIT_COOLDOWN = float(os.getenv("RATE_LIMIT_COOLDOWN", 3.0))
GLOBAL_DAILY_DEMO_LIMIT = int(os.getenv("GLOBAL_DAILY_DEMO_LIMIT", 150))

# Список ID пользователей без ограничений (тестовые аккаунты разработчика)
_exempt_raw = os.getenv("EXEMPT_USER_IDS", "")
EXEMPT_USER_IDS = {int(uid.strip()) for uid in _exempt_raw.split(",") if uid.strip().isdigit()}

if not BOT_TOKEN:
    print("ОШИБКА: Токен бота не найден!")


