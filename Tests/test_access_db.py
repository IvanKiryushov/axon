"""
Unit tests for Scripts.bot.utils.access_db
Tests SQLite initialization, user management, deep-linking tracking,
access request life-cycle, and language settings.
"""

import sys
import os
import pytest
import asyncio
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Scripts.bot.utils import access_db

@pytest.mark.asyncio
async def test_access_db_lifecycle(tmp_path):
    # 1. Setup temporary database
    test_db = tmp_path / "test_axonbot.db"
    access_db.set_db_path(test_db)
    
    admin_id = 9990001
    await access_db.init_db(admin_id=admin_id)
    
    # 2. Check Admin registration
    assert await access_db.is_admin(admin_id) is True
    assert await access_db.is_authorized(admin_id) is True
    
    # 3. Create Guest user with Deep Link source
    guest_id = 1112223
    user = await access_db.get_or_create_user(
        user_id=guest_id,
        username="john_doe",
        full_name="John Doe",
        source="upwork",
        language="en"
    )
    assert user["user_id"] == guest_id
    assert user["role"] == "guest"
    assert user["source"] == "upwork"
    assert user["language"] == "en"
    assert await access_db.is_authorized(guest_id) is False
    assert await access_db.is_admin(guest_id) is False
    
    # 4. Check language switching
    assert await access_db.get_user_language(guest_id) == "en"
    await access_db.set_user_language(guest_id, "ru")
    assert await access_db.get_user_language(guest_id) == "ru"
    
    # 5. Create Access Request with company, email, and goal
    req_id = await access_db.create_access_request(
        user_id=guest_id,
        source="upwork",
        company="SpecProject, BIM Manager",
        email="test@specproject.com",
        contact_phone="+1234567890",
        goal="Company Implementation"
    )
    assert req_id > 0
    
    # Check pending request lookup with new fields
    pending = await access_db.get_pending_request_by_user(guest_id)
    assert pending is not None
    assert pending["id"] == req_id
    assert pending["status"] == "pending"
    assert pending["company"] == "SpecProject, BIM Manager"
    assert pending["email"] == "test@specproject.com"
    assert pending["contact_phone"] == "+1234567890"
    assert pending["goal"] == "Company Implementation"
    
    # Check get_pending_requests list
    pending_list = await access_db.get_pending_requests()
    assert len(pending_list) == 1
    assert pending_list[0]["company"] == "SpecProject, BIM Manager"
    assert pending_list[0]["email"] == "test@specproject.com"
    assert pending_list[0]["goal"] == "Company Implementation"
    
    # Duplicate request returns same ID
    req_id_dup = await access_db.create_access_request(user_id=guest_id, source="upwork")
    assert req_id_dup == req_id
    
    # 6. Admin Approves Request
    res = await access_db.resolve_access_request(request_id=req_id, approve=True, admin_id=admin_id)
    assert res is not None
    target_id, lang, email = res
    assert target_id == guest_id
    assert lang == "ru"
    assert email == "test@specproject.com"
    
    # Guest is now authorized!
    assert await access_db.is_authorized(guest_id) is True
    
    # 7. Check Whitelist Users List
    wl = await access_db.get_whitelist_users()
    uids = [u["user_id"] for u in wl]
    assert admin_id in uids
    assert guest_id in uids
    
    # 8. Check Lead Stats
    stats = await access_db.get_lead_stats()
    assert stats["total_users"] == 2
    assert stats["authorized_users"] == 2
    assert stats["pending_requests"] == 0
    sources_dict = {s["source"]: s["count"] for s in stats["sources"]}
    assert sources_dict.get("upwork") == 1
    assert sources_dict.get("system") == 1

@pytest.mark.asyncio
async def test_access_db_auto_migration(tmp_path):
    """Тест автоматической миграции старой схемы БД до новой структуры."""
    import aiosqlite
    test_db = tmp_path / "legacy_axonbot.db"
    
    # Создаем старую таблицу access_requests без колонок company, email, goal
    async with aiosqlite.connect(test_db) as db:
        await db.execute("""
            CREATE TABLE access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                source TEXT DEFAULT 'direct',
                contact_phone TEXT,
                company_info TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by INTEGER
            );
        """)
        await db.execute("""
            INSERT INTO access_requests (user_id, status, source, contact_phone, company_info, created_at)
            VALUES (444555, 'pending', 'habr', '+79991112233', 'Old Company', '2026-09-01T10:00:00');
        """)
        await db.commit()

    # Запускаем init_db с автомиграцией
    access_db.set_db_path(test_db)
    await access_db.init_db()

    # Проверяем, что колонки company, email, goal успешно добавлены
    async with aiosqlite.connect(test_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("PRAGMA table_info(access_requests);")
        cols = {row["name"] for row in await cursor.fetchall()}
        assert "company" in cols
        assert "email" in cols
        assert "goal" in cols
        
        # Проверяем сохранность старой записи
        cursor = await db.execute("SELECT * FROM access_requests WHERE user_id = 444555;")
        row = await cursor.fetchone()
        assert row["company_info"] == "Old Company"
        assert row["contact_phone"] == "+79991112233"

@pytest.mark.asyncio
async def test_source_overwrite_for_guests(tmp_path):
    """Проверяет перезапись источника для гостей при переходе по внешним каналам (github, upwork, linkedin)."""
    test_db = tmp_path / "test_source_overwrite.db"
    access_db.set_db_path(test_db)
    await access_db.init_db()

    user_id = 7776694188
    # 1. Пользователь впервые зашел по upwork
    user = await access_db.get_or_create_user(user_id=user_id, source="upwork", language="en")
    assert user["source"] == "upwork"

    # 2. Пользователь повторно перешел по ссылке с github
    user_updated = await access_db.get_or_create_user(user_id=user_id, source="github", language="en")
    assert user_updated["source"] == "github"

    # 3. Прямое обновление источника через update_user_source
    await access_db.update_user_source(user_id, "linkedin")
    user_final = await access_db.get_or_create_user(user_id=user_id)
    assert user_final["source"] == "linkedin"

    # 4. Невалидный источник сбрасывается в direct
    await access_db.update_user_source(user_id, "unknown_spam_channel")
    user_direct = await access_db.get_or_create_user(user_id=user_id)
    assert user_direct["source"] == "direct"

if __name__ == "__main__":
    asyncio.run(test_access_db_lifecycle(Path("./.tmp_test_dir")))