"""
Smoke тесты для новых методов репозиториев курсов.
Проверяет работу всех новых методов в CoursesRepository и UserCoursesRepository.
"""
import asyncio
import sys
import os
from pathlib import Path

# Настройка кодировки для Windows
if sys.platform == 'win32':
    os.system('chcp 65001 >nul 2>&1')
    sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

# Добавляем корень проекта в путь
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import Settings
# Импортируем все модели для регистрации в SQLAlchemy
from app.db.base import Base  # это импортирует все модели
from app.repos.courses_repo import CoursesRepository
from app.repos.user_courses_repo import UserCoursesRepository

settings = Settings()


async def test_get_children():
    """Тест: get_children - получение прямых детей курса"""
    print("\n=== Тест 1: get_children ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            repo = CoursesRepository()
            
            # Находим курс с детьми или создаем тестовую структуру
            result = await session.execute(text("""
                SELECT id FROM courses 
                WHERE parent_course_id IS NULL 
                LIMIT 1
            """))
            root_course_row = result.first()
            
            if not root_course_row:
                print("[SKIP] Нет корневых курсов в БД для теста")
                return False
            
            root_course_id = root_course_row[0]
            
            # Проверяем, есть ли дети у этого курса
            children = await repo.get_children(session, root_course_id)
            
            print(f"[PASS] Найдено {len(children)} прямых детей для курса {root_course_id}")
            for child in children:
                print(f"  - Курс {child.id}: {child.title} (parent={child.parent_course_id})")
            
            return True
                
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_get_all_children():
    """Тест: get_all_children - получение всех потомков рекурсивно"""
    print("\n=== Тест 2: get_all_children ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            repo = CoursesRepository()
            
            # Находим корневой курс
            result = await session.execute(text("""
                SELECT id FROM courses 
                WHERE parent_course_id IS NULL 
                LIMIT 1
            """))
            root_course_row = result.first()
            
            if not root_course_row:
                print("[SKIP] Нет корневых курсов в БД для теста")
                return False
            
            root_course_id = root_course_row[0]
            
            # Получаем всех потомков
            all_children = await repo.get_all_children(session, root_course_id)
            
            print(f"[PASS] Найдено {len(all_children)} потомков для курса {root_course_id}")
            for child in all_children:
                print(f"  - Курс {child.id}: {child.title} (parent={child.parent_course_id})")
            
            return True
                
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_get_root_courses():
    """Тест: get_root_courses - получение корневых курсов"""
    print("\n=== Тест 3: get_root_courses ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            repo = CoursesRepository()
            
            # Получаем корневые курсы
            root_courses = await repo.get_root_courses(session)
            
            print(f"[PASS] Найдено {len(root_courses)} корневых курсов")
            for course in root_courses:
                print(f"  - Курс {course.id}: {course.title} (parent={course.parent_course_id})")
            
            return True
                
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_get_course_tree():
    """Тест: get_course_tree - получение дерева курса"""
    print("\n=== Тест 4: get_course_tree ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            repo = CoursesRepository()
            
            # Находим корневой курс
            result = await session.execute(text("""
                SELECT id FROM courses 
                WHERE parent_course_id IS NULL 
                LIMIT 1
            """))
            root_course_row = result.first()
            
            if not root_course_row:
                print("[SKIP] Нет корневых курсов в БД для теста")
                return False
            
            root_course_id = root_course_row[0]
            
            # Получаем дерево
            tree = await repo.get_course_tree(session, root_course_id)
            
            if tree:
                print(f"[PASS] Дерево курса {tree.id}: {tree.title}")
                print(f"  Детей: {len(tree.parent_course_reverse)}")
                
                def print_tree(course, level=0):
                    indent = "  " * (level + 1)
                    print(f"{indent}- {course.id}: {course.title}")
                    for child in course.parent_course_reverse:
                        print_tree(child, level + 1)
                
                for child in tree.parent_course_reverse:
                    print_tree(child)
                
                return True
            else:
                print("[FAIL] Дерево не найдено")
                return False
                
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_get_user_courses():
    """Тест: get_user_courses - получение курсов пользователя"""
    print("\n=== Тест 5: get_user_courses ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            repo = UserCoursesRepository()
            
            # Находим пользователя с курсами
            result = await session.execute(text("""
                SELECT user_id FROM user_courses 
                LIMIT 1
            """))
            user_row = result.first()
            
            if not user_row:
                print("[SKIP] Нет пользователей с курсами в БД для теста")
                return False
            
            user_id = user_row[0]
            
            # Получаем курсы пользователя с сортировкой по order_number
            user_courses = await repo.get_user_courses(session, user_id, order_by_order=True)
            
            print(f"[PASS] Найдено {len(user_courses)} курсов для пользователя {user_id}")
            for uc in user_courses:
                print(f"  - Курс {uc.course_id}, order_number={uc.order_number}, added_at={uc.added_at}")
            
            # Получаем курсы с сортировкой по added_at
            user_courses_by_date = await repo.get_user_courses(session, user_id, order_by_order=False)
            print(f"[PASS] Сортировка по дате: {len(user_courses_by_date)} курсов")
            
            return True
                
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False


async def test_bulk_create_user_courses():
    """Тест: bulk_create_user_courses - массовая привязка курсов"""
    print("\n=== Тест 6: bulk_create_user_courses ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            repo = UserCoursesRepository()
            
            # Находим пользователя и курсы
            result = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_row = result.first()
            if not user_row:
                print("[SKIP] Нет пользователей в БД для теста")
                return False
            user_id = user_row[0]
            
            result = await session.execute(text("SELECT id FROM courses LIMIT 3"))
            course_rows = result.fetchall()
            if len(course_rows) < 2:
                print("[SKIP] Нужно минимум 2 курса в БД для теста")
                return False
            
            course_ids = [row[0] for row in course_rows[:2]]
            
            # Удаляем существующие связи для чистоты теста
            # Используем правильный синтаксис для IN с кортежем
            placeholders = ",".join([f":course_id_{i}" for i in range(len(course_ids))])
            params = {"user_id": user_id}
            params.update({f"course_id_{i}": cid for i, cid in enumerate(course_ids)})
            await session.execute(
                text(f"DELETE FROM user_courses WHERE user_id = :user_id AND course_id IN ({placeholders})"),
                params
            )
            await session.commit()
            
            # Массовая привязка
            created = await repo.bulk_create_user_courses(session, user_id, course_ids)
            
            print(f"[PASS] Привязано {len(created)} курсов к пользователю {user_id}")
            for uc in created:
                print(f"  - Курс {uc.course_id}, order_number={uc.order_number}")
            
            # Проверяем, что при повторной привязке не создаются дубликаты
            created_again = await repo.bulk_create_user_courses(session, user_id, course_ids)
            print(f"[PASS] Повторная привязка: возвращено {len(created_again)} существующих записей (дубликаты не созданы)")
            
            # Очистка
            placeholders = ",".join([f":course_id_{i}" for i in range(len(course_ids))])
            params = {"user_id": user_id}
            params.update({f"course_id_{i}": cid for i, cid in enumerate(course_ids)})
            await session.execute(
                text(f"DELETE FROM user_courses WHERE user_id = :user_id AND course_id IN ({placeholders})"),
                params
            )
            await session.commit()
            
            return True
                
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            await session.rollback()
            return False


async def test_reorder_user_courses():
    """Тест: reorder_user_courses - переупорядочивание курсов"""
    print("\n=== Тест 7: reorder_user_courses ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            repo = UserCoursesRepository()
            
            # Находим пользователя с курсами
            result = await session.execute(text("""
                SELECT user_id, course_id FROM user_courses 
                WHERE order_number IS NOT NULL
                LIMIT 2
            """))
            rows = result.fetchall()
            
            if len(rows) < 2:
                print("[SKIP] Нужно минимум 2 курса с order_number для теста")
                return False
            
            user_id = rows[0][0]
            course_ids = [row[1] for row in rows[:2]]
            
            # Сохраняем исходные значения
            placeholders = ",".join([f":course_id_{i}" for i in range(len(course_ids))])
            params = {"user_id": user_id}
            params.update({f"course_id_{i}": cid for i, cid in enumerate(course_ids)})
            result = await session.execute(
                text(f"SELECT course_id, order_number FROM user_courses WHERE user_id = :user_id AND course_id IN ({placeholders})"),
                params
            )
            original_orders = {row[0]: row[1] for row in result.fetchall()}
            
            # Переупорядочиваем (меняем местами)
            course_orders = [
                {"course_id": course_ids[0], "order_number": original_orders[course_ids[1]]},
                {"course_id": course_ids[1], "order_number": original_orders[course_ids[0]]}
            ]
            
            updated = await repo.reorder_user_courses(session, user_id, course_orders)
            
            print(f"[PASS] Переупорядочено {len(updated)} курсов")
            for uc in updated:
                print(f"  - Курс {uc.course_id}, новый order_number={uc.order_number}")
            
            # Восстанавливаем исходный порядок
            restore_orders = [
                {"course_id": course_ids[0], "order_number": original_orders[course_ids[0]]},
                {"course_id": course_ids[1], "order_number": original_orders[course_ids[1]]}
            ]
            await repo.reorder_user_courses(session, user_id, restore_orders)
            print("[PASS] Исходный порядок восстановлен")
            
            return True
                
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            await session.rollback()
            return False


async def main():
    """Запуск всех тестов"""
    print("=" * 60)
    print("Smoke тесты для новых методов репозиториев")
    print("=" * 60)
    
    results = []
    
    # Тесты CoursesRepository
    results.append(await test_get_children())
    results.append(await test_get_all_children())
    results.append(await test_get_root_courses())
    results.append(await test_get_course_tree())
    
    # Тесты UserCoursesRepository
    results.append(await test_get_user_courses())
    results.append(await test_bulk_create_user_courses())
    results.append(await test_reorder_user_courses())
    
    # Итоги
    print("\n" + "=" * 60)
    print("ИТОГИ:")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Пройдено: {passed}/{total}")
    
    if passed == total:
        print("✅ Все тесты пройдены успешно!")
        return 0
    else:
        print(f"❌ Провалено тестов: {total - passed}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
