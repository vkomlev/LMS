#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Получение тестовых курсов для тестов режимов replace_parents
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

async def get_test_courses():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        # Находим курсы без родителей
        result = await session.execute(text("""
            SELECT c.id, c.title
            FROM courses c
            WHERE NOT EXISTS (
                SELECT 1 FROM course_parents cp WHERE cp.course_id = c.id
            )
            ORDER BY c.id
            LIMIT 3
        """))
        courses_without_parents = result.fetchall()
        
        # Находим курс с родителями
        result = await session.execute(text("""
            SELECT DISTINCT c.id, c.title
            FROM courses c
            WHERE EXISTS (
                SELECT 1 FROM course_parents cp WHERE cp.course_id = c.id
            )
            ORDER BY c.id
            LIMIT 1
        """))
        course_with_parent = result.fetchone()
        
        print(f"COURSE_WITHOUT_PARENT_1={courses_without_parents[0][0] if len(courses_without_parents) > 0 else 'NONE'}")
        print(f"COURSE_WITHOUT_PARENT_2={courses_without_parents[1][0] if len(courses_without_parents) > 1 else 'NONE'}")
        print(f"COURSE_WITHOUT_PARENT_3={courses_without_parents[2][0] if len(courses_without_parents) > 2 else 'NONE'}")
        print(f"COURSE_WITH_PARENT={course_with_parent[0] if course_with_parent else 'NONE'}")
        
        # Находим родителя для курса с родителями
        if course_with_parent:
            result = await session.execute(text("""
                SELECT parent_course_id
                FROM course_parents
                WHERE course_id = :course_id
                LIMIT 1
            """), {"course_id": course_with_parent[0]})
            parent = result.fetchone()
            print(f"PARENT_OF_COURSE_WITH_PARENT={parent[0] if parent else 'NONE'}")

if __name__ == "__main__":
    asyncio.run(get_test_courses())
