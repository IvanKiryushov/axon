import os
import re
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

# Настройки центрального хаба мониторинга (Control Room Supergroup & Topics)
try:
    CONTROL_CHAT_ID = int(os.getenv("CONTROL_CHAT_ID", -1004304196600))
except ValueError:
    CONTROL_CHAT_ID = -1004304196600

try:
    TOPIC_AXONBOT_ID = int(os.getenv("TOPIC_AXONBOT_ID", 13))
except ValueError:
    TOPIC_AXONBOT_ID = 13

try:
    TOPIC_CRITICAL_ID = int(os.getenv("TOPIC_CRITICAL_ID", 4))
except ValueError:
    TOPIC_CRITICAL_ID = 4

# Резервный токен сервисного бота для алертов (если @bim_axonbot не в супергруппе)
ALERT_BOT_TOKEN = os.getenv("ALERT_BOT_TOKEN", "").strip()

# Официальная инвайт-ссылка на пространство Confluence (задается в .env)
CONFLUENCE_INVITE_LINK = os.getenv("CONFLUENCE_INVITE_LINK", "")

# Версия приложения (SSOT: берется из верхнего заголовка CHANGELOG.md)
def _read_changelog_version(default: str = "2.2.7") -> str:
    changelog = PROJECT_ROOT / "CHANGELOG.md"
    if changelog.exists():
        try:
            text = changelog.read_text(encoding="utf-8")
            m = re.search(r"^##\s+\[(\d+\.\d+\.\d+)\]", text, flags=re.MULTILINE)
            if m:
                return m.group(1)
        except OSError:
            pass
    return default


BOT_VERSION = os.getenv("BOT_VERSION") or _read_changelog_version()

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


