#!/usr/bin/env python3
"""
Скрипт для подготовки тестовых данных для smoke тестов пользователей.
Проверяет наличие данных и добавляет недостающие.
"""

import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text, select, insert
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import Settings

async def prepare_test_data():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        # Проверяем наличие ролей
        result = await session.execute(text("SELECT id, name FROM roles"))
        roles = result.fetchall()
        role_dict = {r[1].lower(): r[0] for r in roles}
        
        print(f"Found {len(roles)} roles: {[r[1] for r in roles]}")
        
        # Создаем роли, если их нет
        student_role_id = None
        teacher_role_id = None
        
        if "student" not in role_dict:
            print("Creating 'student' role...")
            result = await session.execute(
                text("INSERT INTO roles (name) VALUES ('student') RETURNING id")
            )
            student_role_id = result.scalar()
            await session.commit()
            print(f"  Created 'student' role (id: {student_role_id})")
        else:
            student_role_id = role_dict["student"]
            print(f"  'student' role exists (id: {student_role_id})")
        
        if "teacher" not in role_dict:
            print("Creating 'teacher' role...")
            result = await session.execute(
                text("INSERT INTO roles (name) VALUES ('teacher') RETURNING id")
            )
            teacher_role_id = result.scalar()
            await session.commit()
            print(f"  Created 'teacher' role (id: {teacher_role_id})")
        else:
            teacher_role_id = role_dict["teacher"]
            print(f"  'teacher' role exists (id: {teacher_role_id})")
        
        # Проверяем пользователей
        result = await session.execute(text("SELECT COUNT(*) FROM users"))
        user_count = result.scalar()
        print(f"\nFound {user_count} users in database")
        
        # Проверяем, сколько пользователей имеют роли
        result = await session.execute(
            text("""
                SELECT COUNT(DISTINCT u.id) 
                FROM users u 
                JOIN user_roles ur ON u.id = ur.user_id 
                JOIN roles r ON ur.role_id = r.id 
                WHERE r.name = 'student'
            """)
        )
        student_count = result.scalar() or 0
        
        result = await session.execute(
            text("""
                SELECT COUNT(DISTINCT u.id) 
                FROM users u 
                JOIN user_roles ur ON u.id = ur.user_id 
                JOIN roles r ON ur.role_id = r.id 
                WHERE r.name = 'teacher'
            """)
        )
        teacher_count = result.scalar() or 0
        
        print(f"Users with 'student' role: {student_count}")
        print(f"Users with 'teacher' role: {teacher_count}")
        
        # Если недостаточно пользователей с ролями, добавляем тестовые
        if student_count < 3:
            print(f"\nAdding test users with 'student' role...")
            for i in range(3 - student_count):
                result = await session.execute(
                    text("""
                        INSERT INTO users (email, password_hash, full_name) 
                        VALUES (:email, :password_hash, :full_name)
                        RETURNING id
                    """),
                    {
                        "email": f"test_student_{i+1}@example.com",
                        "password_hash": "test_hash",
                        "full_name": f"Студент Тестовый {i+1}"
                    }
                )
                user_id = result.scalar()
                
                # Добавляем роль student
                await session.execute(
                    text("INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role_id)"),
                    {"user_id": user_id, "role_id": student_role_id}
                )
                await session.commit()
                print(f"  Created test student: Студент Тестовый {i+1} (id: {user_id})")
        
        if teacher_count < 2:
            print(f"\nAdding test users with 'teacher' role...")
            for i in range(2 - teacher_count):
                result = await session.execute(
                    text("""
                        INSERT INTO users (email, password_hash, full_name) 
                        VALUES (:email, :password_hash, :full_name)
                        RETURNING id
                    """),
                    {
                        "email": f"test_teacher_{i+1}@example.com",
                        "password_hash": "test_hash",
                        "full_name": f"Преподаватель Тестовый {i+1}"
                    }
                )
                user_id = result.scalar()
                
                # Добавляем роль teacher
                await session.execute(
                    text("INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role_id)"),
                    {"user_id": user_id, "role_id": teacher_role_id}
                )
                await session.commit()
                print(f"  Created test teacher: Преподаватель Тестовый {i+1} (id: {user_id})")
        
        # Финальная проверка
        result = await session.execute(
            text("""
                SELECT COUNT(DISTINCT u.id) 
                FROM users u 
                JOIN user_roles ur ON u.id = ur.user_id 
                JOIN roles r ON ur.role_id = r.id 
                WHERE r.name = 'student'
            """)
        )
        final_student_count = result.scalar() or 0
        
        result = await session.execute(
            text("""
                SELECT COUNT(DISTINCT u.id) 
                FROM users u 
                JOIN user_roles ur ON u.id = ur.user_id 
                JOIN roles r ON ur.role_id = r.id 
                WHERE r.name = 'teacher'
            """)
        )
        final_teacher_count = result.scalar() or 0
        
        print(f"\nFinal counts:")
        print(f"  Users with 'student' role: {final_student_count}")
        print(f"  Users with 'teacher' role: {final_teacher_count}")
        print("\nTest data preparation completed!")

if __name__ == "__main__":
    asyncio.run(prepare_test_data())
