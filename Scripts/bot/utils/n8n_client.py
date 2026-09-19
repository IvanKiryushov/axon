import aiohttp
import logging
from Scripts.bot.config import N8N_RAG_URL
from Scripts.bot.utils.query_preprocessor import preprocess_query

user_sessions: dict[int, int] = {}

def get_user_session_id(chat_id: int) -> str:
    """Возвращает актуальный sessionId с учетом эпохи сброса памяти."""
    if chat_id not in user_sessions:
        user_sessions[chat_id] = 1
    return f"{chat_id}_{user_sessions[chat_id]}"

def reset_user_session(chat_id: int):
    """Сбрасывает сессию пользователя, заставляя n8n Memory создать чистый контекст."""
    user_sessions[chat_id] = user_sessions.get(chat_id, 1) + 1
    logging.info(f"Сброшен контекст n8n для chat_id={chat_id}, новая сессия: {user_sessions[chat_id]}")

async def call_n8n_rag(user_text: str, chat_id: int, category: str = "general"):
    """
    Асинхронный вызов вебхука n8n для выполнения RAG поиска.
    """
    session_key = get_user_session_id(chat_id)
    normalized_text = preprocess_query(user_text)
    payload = {
        "text": normalized_text,
        "sessionId": session_key,  # Для ИИ-нод n8n (Memory)
        "chat_id": chat_id,        # Для наших логов и фильтров
        "category": category
    }

    # ФОНАРИК ДЛЯ ДЕБАГА: Выводим в лог, куда именно шлем запрос
    logging.info(f"--- [DEBUG] Отправка запроса в n8n ({session_key}): {N8N_RAG_URL} ---")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(N8N_RAG_URL, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as response:
                if response.status == 200:
                    result = await response.json()
                    output = None
                    if isinstance(result, dict):
                        output = result.get("output") or result.get("text") or result.get("response")
                    elif isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
                        output = result[0].get("output") or result[0].get("text") or result[0].get("response")
                    elif isinstance(result, str):
                        output = result

                    return output if output else "🛑 Ответ от ИИ не получен."
                else:
                    logging.error(f"Сервер n8n ответил кодом {response.status}")
                    return f"⚠️ Ошибка сервера n8n (Код {response.status})"
        except Exception as e:
            logging.error(f"Ошибка при вызове n8n: {e}")
            return "📵 Не удалось связаться с базой знаний. Попробуйте позже."
