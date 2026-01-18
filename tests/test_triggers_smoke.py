"""
Smoke тесты для триггеров курсов.
Проверяет работу всех созданных триггеров и ограничений.
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

settings = Settings()


async def test_order_number_auto_increment():
    """Тест 1: Автоматическая нумерация order_number при INSERT без указания номера"""
    print("\n=== Тест 1: Автоматическая нумерация order_number ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Находим существующего пользователя или создаем тестового
            result = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_row = result.first()
            if not user_row:
                print("[SKIP] Нет пользователей в БД для теста")
                return False
            test_user_id = user_row[0]
            
            # Находим существующий курс
            result = await session.execute(text("SELECT id FROM courses LIMIT 1"))
            course_row = result.first()
            if not course_row:
                print("[SKIP] Нет курсов в БД для теста")
                return False
            test_course_id = course_row[0]
            
            # Удаляем тестовую запись если существует
            await session.execute(
                text("DELETE FROM user_courses WHERE user_id = :user_id AND course_id = :course_id"),
                {"user_id": test_user_id, "course_id": test_course_id}
            )
            await session.commit()
            
            # Проверяем текущий максимальный order_number для пользователя (ПЕРЕД вставкой)
            result = await session.execute(
                text("SELECT COALESCE(MAX(order_number), 0) FROM user_courses WHERE user_id = :user_id"),
                {"user_id": test_user_id}
            )
            max_order_before = result.scalar() or 0
            expected_order = max_order_before + 1
            
            # Вставляем запись БЕЗ указания order_number
            await session.execute(
                text("""
                    INSERT INTO user_courses (user_id, course_id, order_number)
                    VALUES (:user_id, :course_id, NULL)
                """),
                {"user_id": test_user_id, "course_id": test_course_id}
            )
            await session.commit()
            
            # Проверяем, что order_number установлен автоматически
            result = await session.execute(
                text("SELECT order_number FROM user_courses WHERE user_id = :user_id AND course_id = :course_id"),
                {"user_id": test_user_id, "course_id": test_course_id}
            )
            actual_order = result.scalar()
            
            if actual_order == expected_order:
                print(f"[PASS] order_number автоматически установлен в {actual_order}")
                # Удаляем тестовую запись
                await session.execute(
                    text("DELETE FROM user_courses WHERE user_id = :user_id AND course_id = :course_id"),
                    {"user_id": test_user_id, "course_id": test_course_id}
                )
                await session.commit()
                return True
            else:
                print(f"[FAIL] ожидался order_number={expected_order}, получен {actual_order}")
                return False
                
        except Exception as e:
            print(f"[ERROR] {e}")
            await session.rollback()
            return False
        finally:
            await engine.dispose()


async def test_order_number_explicit_insert():
    """Тест 2: Сдвиг существующих курсов при INSERT с явным order_number"""
    print("\n=== Тест 2: Сдвиг курсов при INSERT с явным order_number ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Находим пользователя
            result = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_row = result.first()
            if not user_row:
                print("[SKIP] Нет пользователей в БД для теста")
                return False
            test_user_id = user_row[0]
            
            # Находим существующие курсы или создаем тестовые
            result = await session.execute(text("SELECT id FROM courses LIMIT 3"))
            existing_courses = [row[0] for row in result.fetchall()]
            
            # Если курсов меньше 3, создаем недостающие
            courses_to_create = 3 - len(existing_courses)
            created_courses = []
            
            for i in range(courses_to_create):
                result = await session.execute(
                    text("""
                        INSERT INTO courses (title, access_level, description)
                        VALUES (:title, 'self_guided', 'Test course')
                        RETURNING id
                    """),
                    {"title": f"Test Course {i+1}"}
                )
                new_course_id = result.scalar()
                created_courses.append(new_course_id)
                await session.commit()
            
            course1, course2, course3 = (existing_courses + created_courses)[:3]
            
            # Очищаем тестовые данные
            await session.execute(
                text("DELETE FROM user_courses WHERE user_id = :user_id AND course_id IN (:c1, :c2, :c3)"),
                {"user_id": test_user_id, "c1": course1, "c2": course2, "c3": course3}
            )
            await session.commit()
            
            # Добавляем первый курс (получит order_number=1)
            await session.execute(
                text("INSERT INTO user_courses (user_id, course_id, order_number) VALUES (:uid, :cid, NULL)"),
                {"uid": test_user_id, "cid": course1}
            )
            await session.commit()
            
            # Добавляем второй курс (получит order_number=2)
            await session.execute(
                text("INSERT INTO user_courses (user_id, course_id, order_number) VALUES (:uid, :cid, NULL)"),
                {"uid": test_user_id, "cid": course2}
            )
            await session.commit()
            
            # Добавляем третий курс с явным order_number=1 (должен сдвинуть остальные)
            await session.execute(
                text("INSERT INTO user_courses (user_id, course_id, order_number) VALUES (:uid, :cid, 1)"),
                {"uid": test_user_id, "cid": course3}
            )
            await session.commit()
            
            # Проверяем порядковые номера
            result = await session.execute(
                text("""
                    SELECT course_id, order_number 
                    FROM user_courses 
                    WHERE user_id = :uid AND course_id IN (:c1, :c2, :c3)
                    ORDER BY order_number
                """),
                {"uid": test_user_id, "c1": course1, "c2": course2, "c3": course3}
            )
            rows = result.fetchall()
            
            # Ожидаемый порядок: course3=1, course1=2, course2=3
            expected = {course3: 1, course1: 2, course2: 3}
            actual = {row[0]: row[1] for row in rows}
            
            if actual == expected:
                print(f"[PASS] Курсы правильно переупорядочены: {actual}")
                # Очистка
                await session.execute(
                    text("DELETE FROM user_courses WHERE user_id = :uid AND course_id IN (:c1, :c2, :c3)"),
                    {"uid": test_user_id, "c1": course1, "c2": course2, "c3": course3}
                )
                # Удаляем созданные тестовые курсы
                if created_courses:
                    await session.execute(
                        text("DELETE FROM courses WHERE id IN :ids"),
                        {"ids": tuple(created_courses)}
                    )
                await session.commit()
                return True
            else:
                print(f"❌ FAIL: ожидалось {expected}, получено {actual}")
                return False
                
        except Exception as e:
            print(f"❌ ERROR: {e}")
            await session.rollback()
            return False
        finally:
            await engine.dispose()


async def test_order_number_update():
    """Тест 3: Пересчет order_number при UPDATE"""
    print("\n=== Тест 3: Пересчет order_number при UPDATE ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Находим пользователя
            result = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_row = result.first()
            if not user_row:
                print("[SKIP] Нет пользователей в БД для теста")
                return False
            test_user_id = user_row[0]
            
            # Находим 3 разных курса
            result = await session.execute(text("SELECT id FROM courses LIMIT 3"))
            courses = [row[0] for row in result.fetchall()]
            if len(courses) < 3:
                print("[SKIP] Недостаточно курсов в БД для теста")
                return False
            
            course1, course2, course3 = courses[0], courses[1], courses[2]
            
            # Очищаем тестовые данные
            await session.execute(
                text("DELETE FROM user_courses WHERE user_id = :uid AND course_id IN (:c1, :c2, :c3)"),
                {"uid": test_user_id, "c1": course1, "c2": course2, "c3": course3}
            )
            await session.commit()
            
            # Добавляем 3 курса последовательно
            for i, cid in enumerate([course1, course2, course3], 1):
                await session.execute(
                    text("INSERT INTO user_courses (user_id, course_id, order_number) VALUES (:uid, :cid, NULL)"),
                    {"uid": test_user_id, "cid": cid}
                )
                await session.commit()
            
            # Обновляем order_number курса 3 с 3 на 1 (должен сдвинуть остальные)
            await session.execute(
                text("UPDATE user_courses SET order_number = 1 WHERE user_id = :uid AND course_id = :cid"),
                {"uid": test_user_id, "cid": course3}
            )
            await session.commit()
            
            # Проверяем порядковые номера
            result = await session.execute(
                text("""
                    SELECT course_id, order_number 
                    FROM user_courses 
                    WHERE user_id = :uid AND course_id IN (:c1, :c2, :c3)
                    ORDER BY order_number
                """),
                {"uid": test_user_id, "c1": course1, "c2": course2, "c3": course3}
            )
            rows = result.fetchall()
            
            # Ожидаемый порядок: course3=1, course1=2, course2=3
            expected = {course3: 1, course1: 2, course2: 3}
            actual = {row[0]: row[1] for row in rows}
            
            if actual == expected:
                print(f"[PASS] Курсы правильно переупорядочены после UPDATE: {actual}")
                # Очистка
                await session.execute(
                    text("DELETE FROM user_courses WHERE user_id = :uid AND course_id IN (:c1, :c2, :c3)"),
                    {"uid": test_user_id, "c1": course1, "c2": course2, "c3": course3}
                )
                # Удаляем созданные тестовые курсы
                if 'created_courses' in locals() and created_courses:
                    await session.execute(
                        text("DELETE FROM courses WHERE id = ANY(:ids)"),
                        {"ids": created_courses}
                    )
                await session.commit()
                return True
            else:
                print(f"[FAIL] ожидалось {expected}, получено {actual}")
                # Очистка при ошибке
                await session.execute(
                    text("DELETE FROM user_courses WHERE user_id = :uid AND course_id IN (:c1, :c2, :c3)"),
                    {"uid": test_user_id, "c1": course1, "c2": course2, "c3": course3}
                )
                if 'created_courses' in locals() and created_courses:
                    await session.execute(
                        text("DELETE FROM courses WHERE id = ANY(:ids)"),
                        {"ids": created_courses}
                    )
                await session.commit()
                return False
                
        except Exception as e:
            print(f"[ERROR] {e}")
            # Очистка при исключении
            try:
                if 'created_courses' in locals() and created_courses:
                    await session.execute(
                        text("DELETE FROM courses WHERE id = ANY(:ids)"),
                        {"ids": created_courses}
                    )
                    await session.commit()
            except:
                pass
            await session.rollback()
            return False
        finally:
            await engine.dispose()


async def test_order_number_delete():
    """Тест 4: Пересчет order_number при DELETE"""
    print("\n=== Тест 4: Пересчет order_number при DELETE ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Находим пользователя
            result = await session.execute(text("SELECT id FROM users LIMIT 1"))
            user_row = result.first()
            if not user_row:
                print("[SKIP] Нет пользователей в БД для теста")
                return False
            test_user_id = user_row[0]
            
            # Находим 3 разных курса
            result = await session.execute(text("SELECT id FROM courses LIMIT 3"))
            courses = [row[0] for row in result.fetchall()]
            if len(courses) < 3:
                print("[SKIP] Недостаточно курсов в БД для теста")
                return False
            
            course1, course2, course3 = courses[0], courses[1], courses[2]
            
            # Очищаем тестовые данные
            await session.execute(
                text("DELETE FROM user_courses WHERE user_id = :uid AND course_id IN (:c1, :c2, :c3)"),
                {"uid": test_user_id, "c1": course1, "c2": course2, "c3": course3}
            )
            await session.commit()
            
            # Добавляем 3 курса последовательно
            for i, cid in enumerate([course1, course2, course3], 1):
                await session.execute(
                    text("INSERT INTO user_courses (user_id, course_id, order_number) VALUES (:uid, :cid, NULL)"),
                    {"uid": test_user_id, "cid": cid}
                )
                await session.commit()
            
            # Удаляем курс с order_number=2 (course2)
            await session.execute(
                text("DELETE FROM user_courses WHERE user_id = :uid AND course_id = :cid"),
                {"uid": test_user_id, "cid": course2}
            )
            await session.commit()
            
            # Проверяем порядковые номера оставшихся курсов
            result = await session.execute(
                text("""
                    SELECT course_id, order_number 
                    FROM user_courses 
                    WHERE user_id = :uid AND course_id IN (:c1, :c3)
                    ORDER BY order_number
                """),
                {"uid": test_user_id, "c1": course1, "c3": course3}
            )
            rows = result.fetchall()
            
            # Ожидаемый порядок: course1=1, course3=2 (course2 удален, course3 сдвинулся)
            expected = {course1: 1, course3: 2}
            actual = {row[0]: row[1] for row in rows}
            
            if actual == expected:
                print(f"[PASS] Порядковые номера правильно пересчитаны после DELETE: {actual}")
                # Очистка
                await session.execute(
                    text("DELETE FROM user_courses WHERE user_id = :uid AND course_id IN (:c1, :c3)"),
                    {"uid": test_user_id, "c1": course1, "c3": course3}
                )
                await session.commit()
                return True
            else:
                print(f"❌ FAIL: ожидалось {expected}, получено {actual}")
                return False
                
        except Exception as e:
            print(f"❌ ERROR: {e}")
            await session.rollback()
            return False
        finally:
            await engine.dispose()


async def test_hierarchy_self_reference():
    """Тест 5: Проверка предотвращения самоссылки в иерархии курсов"""
    print("\n=== Тест 5: Предотвращение самоссылки в иерархии ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Находим существующий курс
            result = await session.execute(text("SELECT id FROM courses LIMIT 1"))
            course_row = result.first()
            if not course_row:
                print("[SKIP] Нет курсов в БД для теста")
                return False
            test_course_id = course_row[0]
            
            # Пытаемся установить parent_course_id = id (самоссылка)
            try:
                await session.execute(
                    text("UPDATE courses SET parent_course_id = :cid WHERE id = :cid"),
                    {"cid": test_course_id}
                )
                await session.commit()
                print("[FAIL] Самоссылка не была предотвращена")
                return False
            except Exception as e:
                error_msg = str(e)
                if "cannot be its own parent" in error_msg or "check_course_hierarchy_cycle" in error_msg:
                    print(f"[PASS] Самоссылка предотвращена триггером: {error_msg[:100]}")
                    await session.rollback()
                    return True
                else:
                    print(f"❌ FAIL: Неожиданная ошибка: {error_msg[:200]}")
                    await session.rollback()
                    return False
                    
        except Exception as e:
            print(f"❌ ERROR: {e}")
            await session.rollback()
            return False
        finally:
            await engine.dispose()


async def test_hierarchy_cycle():
    """Тест 6: Проверка предотвращения циклов в иерархии"""
    print("\n=== Тест 6: Предотвращение циклов в иерархии ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Находим 2 существующих курса
            result = await session.execute(text("SELECT id FROM courses LIMIT 2"))
            courses = [row[0] for row in result.fetchall()]
            if len(courses) < 2:
                print("❌ Недостаточно курсов в БД для теста (нужно минимум 2)")
                return False
            
            course1_id, course2_id = courses[0], courses[1]
            
            # Сохраняем текущие parent_course_id
            result = await session.execute(
                text("SELECT parent_course_id FROM courses WHERE id = :cid"),
                {"cid": course1_id}
            )
            old_parent1 = result.scalar()
            
            result = await session.execute(
                text("SELECT parent_course_id FROM courses WHERE id = :cid"),
                {"cid": course2_id}
            )
            old_parent2 = result.scalar()
            
            # Устанавливаем course1.parent = course2
            await session.execute(
                text("UPDATE courses SET parent_course_id = :pid WHERE id = :cid"),
                {"pid": course2_id, "cid": course1_id}
            )
            await session.commit()
            
            # Пытаемся установить course2.parent = course1 (создаст цикл)
            try:
                await session.execute(
                    text("UPDATE courses SET parent_course_id = :pid WHERE id = :cid"),
                    {"pid": course1_id, "cid": course2_id}
                )
                await session.commit()
                print("[FAIL] Цикл не был предотвращен")
                # Восстанавливаем исходное состояние
                await session.execute(
                    text("UPDATE courses SET parent_course_id = :pid WHERE id = :cid"),
                    {"pid": old_parent1, "cid": course1_id}
                )
                await session.execute(
                    text("UPDATE courses SET parent_course_id = :pid WHERE id = :cid"),
                    {"pid": old_parent2, "cid": course2_id}
                )
                await session.commit()
                return False
            except Exception as e:
                error_msg = str(e)
                if "Circular reference" in error_msg or "check_course_hierarchy_cycle" in error_msg:
                    print(f"[PASS] Цикл предотвращен триггером: {error_msg[:100]}")
                    await session.rollback()
                    # Восстанавливаем исходное состояние
                    await session.execute(
                        text("UPDATE courses SET parent_course_id = :pid WHERE id = :cid"),
                        {"pid": old_parent1, "cid": course1_id}
                    )
                    await session.commit()
                    return True
                else:
                    print(f"❌ FAIL: Неожиданная ошибка: {error_msg[:200]}")
                    await session.rollback()
                    return False
                    
        except Exception as e:
            print(f"❌ ERROR: {e}")
            await session.rollback()
            return False
        finally:
            await engine.dispose()


async def test_dependency_self_reference():
    """Тест 7: Проверка предотвращения самоссылки в зависимостях"""
    print("\n=== Тест 7: Предотвращение самоссылки в зависимостях ===")
    
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Находим существующий курс
            result = await session.execute(text("SELECT id FROM courses LIMIT 1"))
            course_row = result.first()
            if not course_row:
                print("[SKIP] Нет курсов в БД для теста")
                return False
            test_course_id = course_row[0]
            
            # Удаляем существующую зависимость если есть
            await session.execute(
                text("DELETE FROM course_dependencies WHERE course_id = :cid AND required_course_id = :cid"),
                {"cid": test_course_id}
            )
            await session.commit()
            
            # Пытаемся создать самоссылку в зависимостях
            try:
                await session.execute(
                    text("INSERT INTO course_dependencies (course_id, required_course_id) VALUES (:cid, :cid)"),
                    {"cid": test_course_id}
                )
                await session.commit()
                print("[FAIL] Самоссылка в зависимостях не была предотвращена")
                # Удаляем тестовую запись
                await session.execute(
                    text("DELETE FROM course_dependencies WHERE course_id = :cid AND required_course_id = :cid"),
                    {"cid": test_course_id}
                )
                await session.commit()
                return False
            except Exception as e:
                error_msg = str(e)
                if "check_no_self_dependency" in error_msg or "constraint" in error_msg.lower():
                    print(f"[PASS] Самоссылка в зависимостях предотвращена: {error_msg[:100]}")
                    await session.rollback()
                    return True
                else:
                    print(f"❌ FAIL: Неожиданная ошибка: {error_msg[:200]}")
                    await session.rollback()
                    return False
                    
        except Exception as e:
            print(f"❌ ERROR: {e}")
            await session.rollback()
            return False
        finally:
            await engine.dispose()


async def main():
    """Запуск всех smoke тестов"""
    print("=" * 60)
    print("SMOKE ТЕСТЫ ТРИГГЕРОВ И ОГРАНИЧЕНИЙ КУРСОВ")
    print("=" * 60)
    
    tests = [
        ("Автоматическая нумерация order_number", test_order_number_auto_increment),
        ("Сдвиг курсов при INSERT с явным order_number", test_order_number_explicit_insert),
        ("Пересчет order_number при UPDATE", test_order_number_update),
        ("Пересчет order_number при DELETE", test_order_number_delete),
        ("Предотвращение самоссылки в иерархии", test_hierarchy_self_reference),
        ("Предотвращение циклов в иерархии", test_hierarchy_cycle),
        ("Предотвращение самоссылки в зависимостях", test_dependency_self_reference),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"[CRITICAL ERROR] в тесте '{test_name}': {e}")
            results.append((test_name, False))
    
    # Итоговая статистика
    print("\n" + "=" * 60)
    print("ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"{status}: {test_name}")
    
    print(f"\nВсего тестов: {total}")
    print(f"Пройдено: {passed}")
    print(f"Провалено: {total - passed}")
    print(f"Процент успеха: {passed * 100 // total if total > 0 else 0}%")
    
    if passed == total:
        print("\n[SUCCESS] ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        return 0
    else:
        print(f"\n[WARNING] ПРОВАЛЕНО ТЕСТОВ: {total - passed}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
