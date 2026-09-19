"""
AxonBot Localization Module (i18n)
Поддерживает двуязычный интерфейс (RU / EN) для всех сообщений и кнопок.
"""

MESSAGES = {
    "ru": {
        # Приветствие и презентация
        "welcome_title": "<b>AxonBot | Корпоративный BIM-ассистент КР</b>",
        "welcome_desc": (
            "Добро пожаловать, <b>{name}</b>!\n\n"
            "Я — автономный инженерный AI-консультант по стандартам и регламентам <b>Revit КР</b>.\n"
            "В мою базу знаний заложено более <b>100+ корпоративных регламентов</b>, технологических карт, "
            "библиотек специализированных семейств и правил армирования монолитных конструкций.\n\n"
            "<b>Возможности:</b>\n"
            "• Точные ответы с цитатами регламентов и параметров <code>ADSK_...</code>\n"
            "• Наглядные схемы узлов, опалубки и армирования из базы знаний\n"
            "• Проверка на запрещенные инструменты и проектные коллизии\n\n"
            "Выберите готовый кейс из каталога ниже или задайте свой технический вопрос в чат.\n\n"
            "Если вы хотите сверить данные с оригинальными регламентами, перейдите по "
            "<a href=\"{invite_link}\">пригласительной ссылке в пространство Confluence</a>."
        ),
        "guest_welcome_desc": (
            "Здравствуйте, <b>{name}</b>!\n\n"
            "Вы подключились к демонстрационному стенду <b>AxonBot</b> — AI BIM-эксперта по разделу Revit КР (100+ регламентов).\n\n"
            "<b>Режим приватного B2B-тестирования</b>\n"
            "Бот находится в режиме закрытого портфолио-доступа для BIM-менеджеров, руководителей проектных отделов и партнеров.\n\n"
            "Нажмите кнопку ниже, чтобы отправить заявку на предоставление доступа:"
        ),
        
        # Экран блокировки
        "access_denied_title": "<b>Доступ ограничен</b>",
        "access_denied_text": (
            "Этот бот работает в режиме приватного B2B-тестирования.\n"
            "Для отправки произвольных вопросов в RAG-базу знаний требуется подтверждение доступа.\n\n"
            "Подайте заявку, и администратор оперативно откроет вам доступ:"
        ),
        "btn_request_access": "Запросить доступ к базе знаний",
        "btn_share_contact": "📱 Поделиться контактом",
        "btn_skip": "Пропустить ⏭",
        "btn_cancel": "❌ Отмена",
        
        # Пошаговая FSM-воронка заявки на доступ
        "request_step1_company": "Укажите вашу компанию и специализацию (например: СпецПроект, BIM-менеджер / проектировщик КР):",
        "request_step2_details": "Выберите цель запроса из списка кнопок ниже или напишите свой вариант сообщением (опционально):",
        "request_step2_email": (
            "Укажите ваш рабочий Email, если вам потребуется доступ к оригинальной базе знаний Confluence для верификации первоисточников регламентов.\n\n"
            "Если вам достаточно тестирования AI-ассистента в Telegram, нажмите «Пропустить»."
        ),
        "request_step2_email_invalid": "Пожалуйста, проверьте формат Email и введите его повторно (например: name@company.com), либо нажмите «Пропустить».",
        "request_step3_goal": "Укажите цель запроса или поделитесь контактом для оперативной связи (опционально):",
        
        # Кнопки целей для Шага 2 (Цель и контакт)
        "goal_company": "Внедрение в компании",
        "goal_upwork": "Поиск подрядчика на Upwork",
        "goal_testing": "Тестирование RAG",
        "goal_other": "Другое",
        
        # Статусы заявки
        "request_prompt_details": "Укажите вашу компанию, должность или цель тестирования:",
        "request_submitted": (
            "<b>Ваша заявка успешно отправлена.</b>\n\n"
            "Администратор рассмотрит ее в ближайшее время. Как только доступ будет открыт, "
            "вы получите персональное уведомление прямо в этом чате."
        ),
        "request_already_pending": (
            "<b>Ваша заявка уже находится на рассмотрении.</b>\n"
            "Пожалуйста, дождитесь решения администратора."
        ),
        "request_cancelled": "Подача заявки отменена.",
        
        # Решение по заявке (push соискателю)
        "request_approved_push": (
            "<b>Доступ к AxonBot успешно активирован.</b>\n\n"
            "Вам открыт полный доступ к RAG-базе знаний по стандартам Revit КР (100+ регламентов).\n"
            "Вы можете выбрать любой пример из каталога или написать свой инженерный вопрос прямо в чат.\n\n"
            "<b>Ссылка для входа в базу знаний Confluence (первоисточники):</b>\n"
            "{invite_link}"
        ),
        "request_approved_confluence_push": (
            "<b>Доступ к AxonBot успешно активирован.</b>\n\n"
            "Ссылка для авторизации в базе знаний Confluence:\n"
            "{invite_link}\n\n"
            "После перехода по ссылке вы сможете беспрепятственно открывать любые первоисточники регламентов из ответов бота."
        ),
        "request_rejected_push": (
            "<b>Уведомление по заявке на доступ</b>\n\n"
            "К сожалению, ваша заявка на доступ к тестовому стенду отклонена администратором."
        ),
        
        # Кнопки и навигация
        "btn_examples": "Каталог кейсов",
        "btn_clear": "Очистить контекст",
        "btn_lang": "🌐 RU / EN",
        "btn_status": "Статус системы",
        "btn_back": "◀️ Назад в меню",
        "btn_back_categories": "◀️ К рубрикам кейсов",
        
        # Рубрики каталога
        "cat_foundations": "Фундаменты и грунт",
        "cat_rebar": "Армирование конструкций",
        "cat_stairs_balconies": "Лестницы и балконы",
        "cat_standards": "Стандарты, маски и спецификации",
        "catalog_title": "<b>Каталог инженерных кейсов</b>\n\nВыберите категорию для просмотра вопросов:",
        "category_cases_title": "<b>Раздел: {category}</b>\n\nВыберите интересующий вопрос для отправки в базу знаний:",
        
        # Системные ответы
        "memory_cleared": "<b>Память диалога очищена.</b>\nЗадайте следующий вопрос.",
        "status_text": (
            "<b>Статус системы AxonBot</b>\n\n"
            "• Версия пайплайна: <code>v1.9.5 (B2B Production)</code>\n"
            "• База знаний: <code>100+ регламентов Revit КР</code>\n"
            "• Состояние RAG-контура: <b>ONLINE</b>\n"
            "• Режим поиска: Hybrid Dense/Sparse (Qdrant + BM25)\n"
            "• Среднее время отклика: <code>4.36 с</code>\n"
            "• Ваш статус: <b>{role}</b>\n"
            "• Выбранный язык: <b>Русский</b>"
        ),
        "role_admin": "Администратор",
        "role_authorized": "Авторизованный пользователь",
        "role_guest": "Гость",
        "only_text_accepted": "Бот принимает только текстовые сообщения.",
        "searching": "<i>Поиск информации в базе регламентов Revit КР...</i>",
        "query_processing": "Обработка запроса..."
    },
    
    "en": {
        # Welcome & Presentation
        "welcome_title": "<b>AxonBot | Enterprise Structural BIM Assistant</b>",
        "welcome_desc": (
            "Welcome, <b>{name}</b>!\n\n"
            "I am an autonomous engineering AI consultant on <b>Autodesk Revit Structural (KR)</b> enterprise standards.\n"
            "My knowledge base incorporates over <b>100+ corporate guidelines</b>, fabrication specs, "
            "specialized parametric family libraries, and reinforced concrete reinforcement codes.\n\n"
            "<b>Capabilities:</b>\n"
            "• Deterministic citations of standards and exact <code>ADSK_...</code> parameters\n"
            "• Verified visual schemes, formwork layouts, and rebar shop drawings\n"
            "• Strict compliance checking and clash prevention rules\n\n"
            "Select a benchmark case below or type your technical question in chat.\n\n"
            "If you would like to verify answers against original corporate standards, please use the "
            "<a href=\"{invite_link}\">Confluence space invite link</a>."
        ),
        "guest_welcome_desc": (
            "Hello, <b>{name}</b>!\n\n"
            "You have connected to the demo instance of <b>AxonBot</b> — AI BIM Structural Expert (100+ standards).\n\n"
            "<b>Private B2B Demo Mode</b>\n"
            "This bot operates in an exclusive testing mode for BIM managers, engineering directors, and prospective partners.\n\n"
            "Tap the button below to submit an access request:"
        ),
        
        # Access lock screen
        "access_denied_title": "<b>Access Restricted</b>",
        "access_denied_text": (
            "This bot operates in private B2B testing mode.\n"
            "Querying the structural engineering RAG pipeline requires authorized access.\n\n"
            "Please apply for demo access, and the administrator will grant you permissions shortly:"
        ),
        "btn_request_access": "Request Knowledge Base Access",
        "btn_share_contact": "📱 Share Contact",
        "btn_skip": "Skip ⏭",
        "btn_cancel": "❌ Cancel",
        
        # Step-by-step access request funnel
        "request_step1_company": "Specify your company and role (e.g.: SpecProject, BIM Manager / Structural Engineer):",
        "request_step2_details": "Select your request goal from the buttons below or type your own option (optional):",
        "request_step2_email": (
            "Enter your work email if you need direct access to the original Confluence knowledge base to verify source regulations.\n\n"
            "If you only wish to test the AI assistant in Telegram, click 'Skip'."
        ),
        "request_step2_email_invalid": "Please check the email format and enter it again (e.g. name@company.com), or click 'Skip'.",
        "request_step3_goal": "Specify your request goal or share your contact for quick communication (optional):",
        
        # Goal options for Step 2 (Goal & Contact)
        "goal_company": "Company Implementation",
        "goal_upwork": "Hiring Contractor on Upwork",
        "goal_testing": "RAG Benchmark Testing",
        "goal_other": "Other",
        
        # Request statuses
        "request_prompt_details": "Please mention your company, role, or evaluation purpose:",
        "request_submitted": (
            "<b>Your access request has been submitted.</b>\n\n"
            "The administrator will review it promptly. Once approved, you will receive an automatic confirmation in this chat."
        ),
        "request_already_pending": (
            "<b>Your application is already pending review.</b>\n"
            "Please await the administrator's confirmation."
        ),
        "request_cancelled": "Request cancelled.",
        
        # Resolution push
        "request_approved_push": (
            "<b>Access to AxonBot has been activated.</b>\n\n"
            "You now have full access to the Structural Revit standards knowledge base (100+ corporate codes).\n"
            "Feel free to explore the benchmark cases or ask any technical question directly in chat.\n\n"
            "<b>Direct link to Confluence Knowledge Base (source regulations):</b>\n"
            "{invite_link}"
        ),
        "request_approved_confluence_push": (
            "<b>Access to AxonBot has been activated.</b>\n\n"
            "Confluence Knowledge Base invite link:\n"
            "{invite_link}\n\n"
            "Click the link to verify source documentation directly."
        ),
        "request_rejected_push": (
            "<b>Access Request Notification</b>\n\n"
            "Unfortunately, your request for demo access has been declined by the administrator."
        ),
        
        # Buttons & Navigation
        "btn_examples": "Case Catalog",
        "btn_clear": "Clear Memory",
        "btn_lang": "🌐 RU / EN",
        "btn_status": "System Status",
        "btn_back": "◀️ Back to Menu",
        "btn_back_categories": "◀️ Back to Categories",
        
        # Catalog Categories
        "cat_foundations": "Foundations & Earthworks",
        "cat_rebar": "Reinforcement Detailing",
        "cat_stairs_balconies": "Stairs & Balconies",
        "cat_standards": "Standards, Naming & Schedules",
        "catalog_title": "<b>Verified Engineering Benchmark Cases</b>\n\nSelect a domain category to browse:",
        "category_cases_title": "<b>Domain: {category}</b>\n\nSelect an engineering query to submit to the knowledge base:",
        
        # System responses
        "memory_cleared": "<b>Conversation context cleared.</b>\nPlease ask your next question.",
        "status_text": (
            "<b>AxonBot System Status</b>\n\n"
            "• Pipeline version: <code>v1.9.5 (B2B Production)</code>\n"
            "• Knowledge base: <code>100+ Revit KR Corporate Standards</code>\n"
            "• RAG Pipeline State: <b>ONLINE</b>\n"
            "• Search mode: Hybrid Dense/Sparse (Qdrant + BM25)\n"
            "• Average latency: <code>4.36 s</code>\n"
            "• Your role: <b>{role}</b>\n"
            "• Active language: <b>English</b>"
        ),
        "role_admin": "Administrator",
        "role_authorized": "Authorized User",
        "role_guest": "Guest",
        "only_text_accepted": "The bot only accepts text messages.",
        "searching": "<i>Searching structural engineering standards...</i>",
        "query_processing": "Processing query..."
    }
}

class _SafeDict(dict):
    def __missing__(self, key):
        return f"{{{key}}}"

def t(key: str, lang: str = "ru", **kwargs) -> str:
    """Возвращает локализованную строку по ключу, с fallback на русский язык."""
    lang_dict = MESSAGES.get(lang) or MESSAGES["ru"]
    template = lang_dict.get(key) or MESSAGES["ru"].get(key, key)
    if kwargs:
        try:
            return template.format_map(_SafeDict(**kwargs))
        except Exception:
            return template
    return template