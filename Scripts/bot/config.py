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

if not BOT_TOKEN:
    print("ОШИБКА: Токен бота не найден!")

