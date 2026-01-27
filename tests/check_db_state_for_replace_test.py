#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверка состояния БД после тестов replace_parents
"""

import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import Settings

async def check_db(course_with_parent_id, test_course_id):
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        # Проверяем курс с родителями
        result = await session.execute(text("""
            SELECT cp.parent_course_id, cp.order_number
            FROM course_parents cp
            WHERE cp.course_id = :course_id
            ORDER BY cp.parent_course_id
        """), {"course_id": course_with_parent_id})
        parents = result.fetchall()
        
        print(f"Course {course_with_parent_id} has parents:")
        for parent_id, order_num in parents:
            print(f"  - Parent ID: {parent_id}, order_number: {order_num}")
        
        # Проверяем тестовый курс
        result = await session.execute(text("""
            SELECT cp.parent_course_id, cp.order_number
            FROM course_parents cp
            WHERE cp.course_id = :course_id
            ORDER BY cp.parent_course_id
        """), {"course_id": test_course_id})
        parents_test = result.fetchall()
        
        print(f"\nCourse {test_course_id} has parents:")
        for parent_id, order_num in parents_test:
            print(f"  - Parent ID: {parent_id}, order_number: {order_num}")

if __name__ == "__main__":
    import sys
    course_with_parent_id = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    test_course_id = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    asyncio.run(check_db(course_with_parent_id, test_course_id))
