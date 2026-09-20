"""
AxonBot SQLite Access & Leads Database Module
Обеспечивает транзакционное управление правами доступа (Whitelist/Guests),
сбором лидов, отслеживанием реферальных источников (Deep Linking) и мультиязычностью.
"""

import os
import aiosqlite
import logging
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "axonbot.db"

def set_db_path(custom_path: Path | str):
    """Позволяет переопределить путь к БД (например, для тестов)."""
    global DB_PATH
    DB_PATH = Path(custom_path)

@asynccontextmanager
async def get_db():
    """Асинхронный контекстный менеджер для безопасного соединения с базой данных."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL;")
        yield db

async def init_db(admin_id: int = 0):
    """Инициализирует таблицы базы данных и регистрирует администратора."""
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                role TEXT NOT NULL DEFAULT 'guest',
                source TEXT DEFAULT 'direct',
                language TEXT DEFAULT 'ru',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                source TEXT DEFAULT 'direct',
                company TEXT,
                email TEXT,
                contact_phone TEXT,
                goal TEXT,
                company_info TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
        
        # Автомиграция колонок для существующих баз данных (access_requests)
        cursor = await db.execute("PRAGMA table_info(access_requests);")
        existing_cols = {row["name"] for row in await cursor.fetchall()}
        for col_name, col_type in [("company", "TEXT"), ("email", "TEXT"), ("goal", "TEXT")]:
            if col_name not in existing_cols:
                await db.execute(f"ALTER TABLE access_requests ADD COLUMN {col_name} {col_type};")
        
        # Автомиграция колонок для таблицы users (квоты и активность)
        cursor_u = await db.execute("PRAGMA table_info(users);")
        existing_user_cols = {row["name"] for row in await cursor_u.fetchall()}
        for col_name, col_type in [
            ("queries_used", "INTEGER NOT NULL DEFAULT 0"),
            ("queries_limit", "INTEGER DEFAULT NULL"),
            ("last_query_at", "TEXT")
        ]:
            if col_name not in existing_user_cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type};")
        
        await db.commit()
        
        if admin_id and admin_id > 0:
            now = datetime.now().isoformat()
            await db.execute("""
                INSERT INTO users (user_id, username, full_name, role, source, language, created_at, updated_at)
                VALUES (?, 'admin', 'Administrator', 'admin', 'system', 'ru', ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET role='admin', updated_at=excluded.updated_at;
            """, (admin_id, now, now))
            await db.commit()
            logging.info(f"Администратор {admin_id} зарегистрирован в базе данных.")

async def get_or_create_user(
    user_id: int,
    username: str = "",
    full_name: str = "",
    source: str = "direct",
    language: str = "ru"
) -> dict:
    """Получает существующего пользователя или создает нового с указанным источником и языком."""
    now = datetime.now().isoformat()
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            # Для гостей (guest) при явном переходе по внешнему каналу обновляем источник
            new_source = source if (source in ("github", "upwork", "linkedin") and row["role"] == "guest") else row["source"]
            await db.execute("""
                UPDATE users 
                SET username = COALESCE(?, username), 
                    full_name = COALESCE(?, full_name), 
                    source = ?,
                    updated_at = ? 
                WHERE user_id = ?;
            """, (username, full_name, new_source, now, user_id))
            await db.commit()
            
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            updated_row = await cursor.fetchone()
            return dict(updated_row)
        
        # Если пользователя нет — регистрируем как гостя
        clean_source = source if source in ("github", "upwork", "linkedin", "direct", "system") else "direct"
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, role, source, language, created_at, updated_at)
            VALUES (?, ?, ?, 'guest', ?, ?, ?, ?);
        """, (user_id, username, full_name, clean_source, language or "ru", now, now))
        await db.commit()
        
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        new_row = await cursor.fetchone()
        return dict(new_row)

VALID_SOURCES = {"github", "upwork", "linkedin", "direct", "system"}

async def update_user_source(user_id: int, source: str) -> bool:
    """Обновляет источник перехода пользователя."""
    clean_source = source.strip().lower() if source else "direct"
    if clean_source not in VALID_SOURCES:
        clean_source = "direct"
    now = datetime.now().isoformat()
    async with get_db() as db:
        await db.execute("""
            UPDATE users 
            SET source = ?, updated_at = ? 
            WHERE user_id = ?;
        """, (clean_source, now, user_id))
        await db.commit()
        return True

async def is_authorized(user_id: int) -> bool:
    """Проверяет, имеет ли пользователь авторизованный доступ (admin или authorized)."""
    async with get_db() as db:
        cursor = await db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        return row["role"] in ("admin", "authorized")

async def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    async with get_db() as db:
        cursor = await db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return bool(row and row["role"] == "admin")

async def set_user_language(user_id: int, language: str):
    """Обновляет язык интерфейса пользователя."""
    now = datetime.now().isoformat()
    async with get_db() as db:
        await db.execute("""
            UPDATE users SET language = ?, updated_at = ? WHERE user_id = ?;
        """, (language, now, user_id))
        await db.commit()

async def get_user_language(user_id: int) -> str:
    """Возвращает текущий язык пользователя (по умолчанию 'ru')."""
    async with get_db() as db:
        cursor = await db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row["language"]:
            return row["language"]
        return "ru"

async def create_access_request(
    user_id: int,
    source: str = "direct",
    company: str = "",
    email: str = "",
    contact_phone: str = "",
    goal: str = "",
    company_info: str = ""
) -> int:
    """Создает новую заявку на доступ для пользователя с сохранением компании, email и цели."""
    now = datetime.now().isoformat()
    # Обратная совместимость для полей company и company_info
    resolved_company = company or company_info
    resolved_company_info = company_info or (f"{company} | {goal}".strip(" |") if company or goal else "")
    
    async with get_db() as db:
        # Проверяем, есть ли уже активная заявка
        cursor = await db.execute("""
            SELECT id FROM access_requests 
            WHERE user_id = ? AND status = 'pending';
        """, (user_id,))
        existing = await cursor.fetchone()
        if existing:
            return existing["id"]
        
        cursor = await db.execute("""
            INSERT INTO access_requests (user_id, status, source, company, email, contact_phone, goal, company_info, created_at)
            VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?);
        """, (user_id, source or "direct", resolved_company, email, contact_phone, goal, resolved_company_info, now))
        await db.commit()
        return cursor.lastrowid

async def get_pending_request_by_user(user_id: int) -> dict | None:
    """Проверяет наличие ожидающей заявки от пользователя."""
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT * FROM access_requests 
            WHERE user_id = ? AND status = 'pending' 
            ORDER BY id DESC LIMIT 1;
        """, (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def resolve_access_request(request_id: int, approve: bool, admin_id: int) -> tuple[int, str] | None:
    """
    Одобряет или отклоняет заявку на доступ.
    Возвращает (user_id, user_language) для отправки пуша.
    """
    now = datetime.now().isoformat()
    new_status = "approved" if approve else "rejected"
    new_role = "authorized" if approve else "guest"
    
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT ar.user_id, u.language, ar.email 
            FROM access_requests ar
            JOIN users u ON ar.user_id = u.user_id
            WHERE ar.id = ?;
        """, (request_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        
        target_user_id = row["user_id"]
        user_lang = row["language"] or "ru"
        user_email = row["email"] or ""
        
        await db.execute("""
            UPDATE access_requests 
            SET status = ?, resolved_at = ?, resolved_by = ? 
            WHERE id = ?;
        """, (new_status, now, admin_id, request_id))
        
        if approve:
            await db.execute("""
                UPDATE users 
                SET role = ?, 
                    queries_limit = CASE 
                        WHEN queries_limit IS NULL OR (queries_limit > 0 AND queries_limit < 100) THEN 100
                        ELSE queries_limit
                    END,
                    updated_at = ? 
                WHERE user_id = ?;
            """, (new_role, now, target_user_id))
            
        await db.commit()
        return target_user_id, user_lang, user_email

async def get_whitelist_users() -> list[dict]:
    """Возвращает список всех авторизованных пользователей."""
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT user_id, username, full_name, role, source, language, created_at 
            FROM users 
            WHERE role IN ('admin', 'authorized') 
            ORDER BY created_at DESC;
        """)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def get_pending_requests() -> list[dict]:
    """Возвращает список всех ожидающих заявок."""
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT ar.id, ar.user_id, u.username, u.full_name, ar.source, 
                   ar.company, ar.email, ar.contact_phone, ar.goal, ar.company_info, 
                   ar.created_at, u.language
            FROM access_requests ar
            JOIN users u ON ar.user_id = u.user_id
            WHERE ar.status = 'pending'
            ORDER BY ar.id ASC;
        """)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def get_lead_stats() -> dict:
    """Возвращает сводную аналитику по пользователям, источникам лидов и расходу квот."""
    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) as total FROM users;")
        total_users = (await cursor.fetchone())["total"]
        
        cursor = await db.execute("SELECT COUNT(*) as authorized FROM users WHERE role IN ('admin', 'authorized');")
        authorized_users = (await cursor.fetchone())["authorized"]
        
        cursor = await db.execute("SELECT COUNT(*) as pending FROM access_requests WHERE status = 'pending';")
        pending_requests = (await cursor.fetchone())["pending"]

        cursor = await db.execute("SELECT COALESCE(SUM(queries_used), 0) as total_queries FROM users;")
        total_queries = (await cursor.fetchone())["total_queries"]

        cursor = await db.execute("""
            SELECT COUNT(*) as exhausted 
            FROM users 
            WHERE role = 'guest' 
              AND queries_used >= COALESCE(queries_limit, 15);
        """)
        exhausted_guests = (await cursor.fetchone())["exhausted"]
        
        cursor = await db.execute("""
            SELECT source, COUNT(*) as count 
            FROM users 
            GROUP BY source 
            ORDER BY count DESC;
        """)
        sources = [dict(r) for r in await cursor.fetchall()]
        
        return {
            "total_users": total_users,
            "authorized_users": authorized_users,
            "pending_requests": pending_requests,
            "total_queries": total_queries,
            "exhausted_guests": exhausted_guests,
            "sources": sources
        }

async def check_and_consume_quota(
    user_id: int, 
    role: str = "guest", 
    default_limit: int = 15,
    is_exempt: bool = False
) -> tuple[bool, int, int]:
    """
    Атомарно проверяет и списывает 1 запрос из квоты пользователя.
    Возвращает: (разрешено: bool, использовано: int, лимит: int).
    Лимит = -1 означает вечный безлимит.
    """
    now = datetime.now().isoformat()
    async with get_db() as db:
        # Если пользователь освобожден от квот (Admin или в списке EXEMPT_USER_IDS)
        if is_exempt or role == "admin":
            await db.execute("""
                UPDATE users 
                SET queries_used = queries_used + 1, last_query_at = ?, updated_at = ?
                WHERE user_id = ?;
            """, (now, now, user_id))
            await db.commit()
            
            cursor = await db.execute("SELECT queries_used FROM users WHERE user_id = ?;", (user_id,))
            row = await cursor.fetchone()
            used = row["queries_used"] if row else 1
            return True, used, -1

        # Атомарное обновление со строгой проверкой лимита
        # queries_limit == -1: безлимит
        # queries_limit IS NULL: используется default_limit
        # queries_limit > 0: используется персональный queries_limit
        cursor = await db.execute("""
            UPDATE users 
            SET queries_used = queries_used + 1,
                last_query_at = ?,
                updated_at = ?
            WHERE user_id = ? 
              AND (
                queries_limit = -1
                OR queries_used < COALESCE(queries_limit, ?)
              );
        """, (now, now, user_id, default_limit))
        await db.commit()
        
        success = (cursor.rowcount == 1)
        
        cursor = await db.execute("""
            SELECT queries_used, queries_limit FROM users WHERE user_id = ?;
        """, (user_id,))
        row = await cursor.fetchone()
        if row:
            used = row["queries_used"]
            lim = row["queries_limit"] if row["queries_limit"] is not None else default_limit
            return success, used, lim
        return success, 0, default_limit

async def refund_quota(user_id: int) -> None:
    """Возвращает списанную квоту пользователю в случае сбоя генерации RAG."""
    now = datetime.now().isoformat()
    async with get_db() as db:
        await db.execute("""
            UPDATE users 
            SET queries_used = MAX(0, queries_used - 1), updated_at = ?
            WHERE user_id = ?;
        """, (now, user_id))
        await db.commit()

async def get_user_quota(user_id: int, default_limit: int = 15) -> dict:
    """Возвращает информацию о квоте пользователя."""
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT role, queries_used, queries_limit, last_query_at FROM users WHERE user_id = ?;
        """, (user_id,))
        row = await cursor.fetchone()
        if not row:
            return {"used": 0, "limit": default_limit, "remaining": default_limit, "is_unlimited": False}
        
        role = row["role"]
        used = row["queries_used"] or 0
        raw_limit = row["queries_limit"]
        
        if role == "admin" or raw_limit == -1:
            return {"used": used, "limit": -1, "remaining": None, "is_unlimited": True}
        
        limit = raw_limit if raw_limit is not None else default_limit
        remaining = max(0, limit - used)
        return {"used": used, "limit": limit, "remaining": remaining, "is_unlimited": False}

async def set_user_quota(user_id: int, new_limit: int) -> bool:
    """Устанавливает персональный лимит пользователю (-1 = безлимит)."""
    now = datetime.now().isoformat()
    async with get_db() as db:
        cursor = await db.execute("""
            UPDATE users 
            SET queries_limit = ?, updated_at = ?
            WHERE user_id = ?;
        """, (new_limit, now, user_id))
        await db.commit()
        return cursor.rowcount > 0

async def add_user_quota(user_id: int, additional: int, default_base: int = 15) -> tuple[bool, int]:
    """Добавляет или убавляет запросы к текущему лимиту пользователя."""
    now = datetime.now().isoformat()
    async with get_db() as db:
        cursor = await db.execute("SELECT role, queries_limit FROM users WHERE user_id = ?;", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return False, 0
        
        current_limit = row["queries_limit"]
        role = row["role"]
        
        if current_limit == -1 or role == "admin":
            return True, -1
        
        if current_limit is not None:
            base = current_limit
        else:
            base = 100 if role == "authorized" else default_base
        
        new_limit = max(0, base + additional)
        
        await db.execute("""
            UPDATE users 
            SET queries_limit = ?, updated_at = ?
            WHERE user_id = ?;
        """, (new_limit, now, user_id))
        await db.commit()
        return True, new_limit

async def reset_user_quota(user_id: int) -> bool:
    """Обнуляет количество использованных запросов пользователя."""
    now = datetime.now().isoformat()
    async with get_db() as db:
        cursor = await db.execute("""
            UPDATE users 
            SET queries_used = 0, updated_at = ?
            WHERE user_id = ?;
        """, (now, user_id))
        await db.commit()
        return cursor.rowcount > 0

async def get_global_daily_demo_count() -> int:
    """Подсчитывает суммарное количество демо-запросов, совершенных гостями за сегодня (UTC)."""
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT COUNT(*) as count 
            FROM users 
            WHERE role = 'guest' 
              AND last_query_at IS NOT NULL 
              AND substr(last_query_at, 1, 10) = substr(datetime('now'), 1, 10);
        """)
        row = await cursor.fetchone()
        return row["count"] if row else 0

async def get_all_users_paged(page: int = 0, page_size: int = 5) -> tuple[list[dict], int]:
    """
    Возвращает страницу пользователей (для Master View в /whitelist) и общее количество.
    Сортировка: сначала администраторы, затем по дате последней активности или созданию.
    """
    offset = page * page_size
    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) as total FROM users;")
        total = (await cursor.fetchone())["total"]
        
        cursor = await db.execute("""
            SELECT user_id, username, full_name, role, source, language, 
                   queries_used, queries_limit, last_query_at, created_at
            FROM users
            ORDER BY 
                CASE WHEN role = 'admin' THEN 0 ELSE 1 END,
                COALESCE(last_query_at, created_at) DESC
            LIMIT ? OFFSET ?;
        """, (page_size, offset))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows], total

async def get_user_detail(user_id: int) -> dict | None:
    """Возвращает детальный профиль пользователя вместе со статусом последней заявки (для Detail View)."""
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT u.*, 
                   ar.status as request_status,
                   ar.company as request_company,
                   ar.goal as request_goal,
                   ar.created_at as request_created_at
            FROM users u
            LEFT JOIN access_requests ar ON u.user_id = ar.user_id
            WHERE u.user_id = ?
            ORDER BY ar.id DESC LIMIT 1;
        """, (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def revoke_user_access(user_id: int, admin_id: int) -> bool:
    """
    Сбрасывает роль пользователя на 'guest' и переводит его заявки в статус 'revoked'.
    Защищает администратора от случайного отзыва прав.
    """
    if user_id == admin_id:
        return False
        
    now = datetime.now().isoformat()
    async with get_db() as db:
        await db.execute("""
            UPDATE users SET role = 'guest', queries_limit = 15, updated_at = ? WHERE user_id = ?;
        """, (now, user_id))
        
        await db.execute("""
            UPDATE access_requests SET status = 'revoked', resolved_at = ?, resolved_by = ?
            WHERE user_id = ? AND status = 'approved';
        """, (now, admin_id, user_id))
        
        await db.commit()
        return True

async def reset_user_completely(user_id: int, admin_id: int) -> bool:
    """
    Полностью удаляет пользователя и его заявки из базы данных для повторного тестирования воронки 'с нуля'.
    """
    if user_id == admin_id:
        return False
        
    async with get_db() as db:
        await db.execute("DELETE FROM access_requests WHERE user_id = ?;", (user_id,))
        await db.execute("DELETE FROM users WHERE user_id = ?;", (user_id,))
        await db.commit()
        return True