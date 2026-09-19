import json
import os
from datetime import datetime

# Переменная для хранения пути текущей сессии
_current_session_path = "Scripts/bot/data/logs/default"

def set_session_path(path: str):
    """
    Устанавливает путь к папке текущей сессии.
    Вызывается при старте бота в main.py.
    """
    global _current_session_path
    _current_session_path = path
    os.makedirs(_current_session_path, exist_ok=True)

def log_conversation(user_id: int, user_name: str, question: str, answer: str, category: str = "general"):
    """
    Записывает диалог в архив (JSONL) текущей сессии.
    """
    log_file = os.path.join(_current_session_path, "conversations.jsonl")

    log_entry = {
        "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "user_id": user_id,
        "user_name": user_name,
        "category": category,
        "question": question,
        "answer": answer
    }

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

def log_reset(user_id: int, user_name: str):
    """
    Фиксирует событие сброса контекста.
    """
    log_conversation(user_id, user_name, "[SYSTEM: RESET CONTEXT]", "Context cleared", "system")
