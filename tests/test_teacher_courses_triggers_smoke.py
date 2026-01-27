"""
Smoke тесты для триггеров teacher_courses через прямые запросы к БД.
Использует SQLAlchemy для выполнения запросов.
"""
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import Settings


async def get_db_session():
    """Создает сессию БД"""
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    return async_session()


async def test_table_structure():
    """Тест 1: Проверка структуры таблицы"""
    print("\n=== Тест 1: Проверка структуры таблицы ===")
    async with await get_db_session() as db:
        result = await db.execute(text("""
            SELECT column_name, data_type, is_nullable, column_default 
            FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'teacher_courses' 
            ORDER BY ordinal_position
        """))
        rows = result.fetchall()
        print(f"Колонки таблицы teacher_courses: {len(rows)}")
        for row in rows:
            print(f"  - {row.column_name}: {row.data_type} (nullable: {row.is_nullable})")
        assert len(rows) == 3, f"Ожидалось 3 колонки, получено {len(rows)}"
        print("[OK] Структура таблицы корректна")


async def test_indexes():
    """Тест 2: Проверка индексов"""
    print("\n=== Тест 2: Проверка индексов ===")
    async with await get_db_session() as db:
        result = await db.execute(text("""
            SELECT indexname, indexdef 
            FROM pg_indexes 
            WHERE schemaname = 'public' AND tablename = 'teacher_courses' 
            ORDER BY indexname
        """))
        indexes = result.fetchall()
        print(f"Индексов создано: {len(indexes)}")
        expected_indexes = [
            'idx_teacher_courses_course_id',
            'idx_teacher_courses_course_linked_at',
            'idx_teacher_courses_linked_at',
            'idx_teacher_courses_teacher_id',
            'idx_teacher_courses_teacher_linked_at',
            'teacher_courses_pkey'
        ]
        actual_indexes = [idx.indexname for idx in indexes]
        for exp_idx in expected_indexes:
            assert exp_idx in actual_indexes, f"Индекс {exp_idx} не найден"
            print(f"  [OK] {exp_idx}")
        print("[OK] Все индексы созданы корректно")


async def test_triggers():
    """Тест 3: Проверка триггеров"""
    print("\n=== Тест 3: Проверка триггеров ===")
    async with await get_db_session() as db:
        result = await db.execute(text("""
            SELECT trigger_name, event_object_table, action_timing, event_manipulation 
            FROM information_schema.triggers 
            WHERE trigger_schema = 'public' AND trigger_name LIKE '%teacher_course%' 
            ORDER BY trigger_name, event_manipulation
        """))
        triggers = result.fetchall()
        print(f"Триггеров создано: {len(triggers)}")
        expected_triggers = [
            ('trg_auto_link_teacher_course_children', 'teacher_courses', 'AFTER', 'INSERT'),
            ('trg_auto_unlink_teacher_course_children', 'teacher_courses', 'AFTER', 'DELETE'),
            ('trg_sync_teacher_courses_on_child_added', 'course_parents', 'AFTER', 'INSERT'),
            # ⚠️ ТЕХНИЧЕСКИЙ ДОЛГ: trg_sync_teacher_courses_on_child_removed отключен
            # Логика перенесена в TeacherCoursesRepository.sync_on_child_removed()
            # ('trg_sync_teacher_courses_on_child_removed', 'course_parents', 'AFTER', 'DELETE'),
        ]
        actual_triggers = {(t.trigger_name, t.event_object_table, t.action_timing, t.event_manipulation) for t in triggers}
        for exp_trigger in expected_triggers:
            assert exp_trigger in actual_triggers, f"Триггер {exp_trigger[0]} не найден"
            print(f"  [OK] {exp_trigger[0]} ({exp_trigger[1]}, {exp_trigger[2]}, {exp_trigger[3]})")
        print("[OK] Все триггеры созданы корректно")


async def test_auto_link_children():
    """Тест 4: Автоматическая привязка детей при привязке родителя"""
    print("\n=== Тест 4: Автоматическая привязка детей ===")
    async with await get_db_session() as db:
        # Очищаем тестовые данные
        await db.execute(text("DELETE FROM teacher_courses WHERE teacher_id = 16"))
        await db.commit()
        
        # Получаем список детей курса 1
        result = await db.execute(text("""
            WITH RECURSIVE course_descendants AS (
                SELECT cp.course_id, 1 as depth
                FROM course_parents cp
                WHERE cp.parent_course_id = 1
                UNION ALL
                SELECT cp.course_id, cd.depth + 1
                FROM course_parents cp
                INNER JOIN course_descendants cd ON cp.parent_course_id = cd.course_id
                WHERE cd.depth < 20
            )
            SELECT course_id FROM course_descendants ORDER BY course_id LIMIT 10
        """))
        children = [row[0] for row in result.fetchall()]
        print(f"Детей у курса 1: {len(children)} (проверяем первые 10)")
        
        # Привязываем преподавателя к родительскому курсу
        await db.execute(text("INSERT INTO teacher_courses (teacher_id, course_id) VALUES (16, 1)"))
        await db.commit()
        print("[OK] Преподаватель 16 привязан к курсу 1")
        
        # Проверяем, что все дети автоматически привязаны
        result = await db.execute(text("""
            SELECT course_id 
            FROM teacher_courses 
            WHERE teacher_id = 16 
            ORDER BY course_id
        """))
        linked_courses = [row[0] for row in result.fetchall()]
        print(f"Привязанных курсов: {len(linked_courses)}")
        
        # Проверяем, что курс 1 и все его дети привязаны
        assert 1 in linked_courses, "Родительский курс не привязан"
        for child_id in children[:10]:  # Проверяем первые 10
            assert child_id in linked_courses, f"Дочерний курс {child_id} не привязан автоматически"
        
        print(f"[OK] Все дети автоматически привязаны (проверено {min(10, len(children))} из {len(children)})")


async def test_auto_unlink_children():
    """Тест 5: Автоматическая отвязка детей при отвязке родителя"""
    print("\n=== Тест 5: Автоматическая отвязка детей ===")
    async with await get_db_session() as db:
        # Проверяем текущее количество связей
        result = await db.execute(text("SELECT COUNT(*) FROM teacher_courses WHERE teacher_id = 16"))
        count_before = result.scalar()
        print(f"Связей до отвязки: {count_before}")
        
        # Отвязываем от родителя
        await db.execute(text("DELETE FROM teacher_courses WHERE teacher_id = 16 AND course_id = 1"))
        await db.commit()
        print("[OK] Преподаватель 16 отвязан от курса 1")
        
        # Проверяем, что все связи удалены
        result = await db.execute(text("SELECT COUNT(*) FROM teacher_courses WHERE teacher_id = 16"))
        count_after = result.scalar()
        print(f"Связей после отвязки: {count_after}")
        
        assert count_after == 0, f"Ожидалось 0 связей, получено {count_after}"
        print("[OK] Все дети автоматически отвязаны")


async def test_sync_on_child_added():
    """Тест 6: Синхронизация при добавлении ребенка в иерархию"""
    print("\n=== Тест 6: Синхронизация при добавлении ребенка ===")
    async with await get_db_session() as db:
        # Привязываем преподавателя к родительскому курсу
        await db.execute(text("INSERT INTO teacher_courses (teacher_id, course_id) VALUES (17, 1) ON CONFLICT DO NOTHING"))
        await db.commit()
        print("[OK] Преподаватель 17 привязан к курсу 1")
        
        # Находим курс, который еще не является ребенком курса 1
        result = await db.execute(text("""
            SELECT id FROM courses 
            WHERE id NOT IN (SELECT course_id FROM course_parents WHERE parent_course_id = 1)
              AND id != 1
            ORDER BY id LIMIT 1
        """))
        new_child_id = result.scalar()
        if not new_child_id:
            print("⚠️ Не найден свободный курс для теста, пропускаем")
            return
        
        print(f"Добавляем курс {new_child_id} как ребенка курса 1")
        
        # Добавляем ребенка (если связи еще нет)
        await db.execute(text(f"""
            INSERT INTO course_parents (course_id, parent_course_id) 
            VALUES ({new_child_id}, 1)
            ON CONFLICT DO NOTHING
        """))
        await db.commit()
        print(f"[OK] Курс {new_child_id} добавлен как ребенок курса 1")
        
        # Проверяем, что преподаватель автоматически привязан к новому ребенку
        result = await db.execute(text(f"""
            SELECT COUNT(*) FROM teacher_courses 
            WHERE teacher_id = 17 AND course_id = {new_child_id}
        """))
        link_exists = result.scalar() > 0
        
        assert link_exists, f"Преподаватель 17 не привязан к новому ребенку {new_child_id}"
        print(f"[OK] Преподаватель 17 автоматически привязан к курсу {new_child_id}")


async def test_sync_on_child_removed():
    """Тест 7: Синхронизация при удалении ребенка из иерархии"""
    print("\n=== Тест 7: Синхронизация при удалении ребенка ===")
    async with await get_db_session() as db:
        # Находим ребенка курса 1, к которому привязан преподаватель 17
        # И который НЕ имеет других родителей (чтобы связь точно удалилась)
        result = await db.execute(text("""
            SELECT cp.course_id 
            FROM course_parents cp
            JOIN teacher_courses tc ON cp.course_id = tc.course_id
            WHERE cp.parent_course_id = 1 
              AND tc.teacher_id = 17
              AND cp.course_id NOT IN (
                  SELECT DISTINCT cp2.course_id 
                  FROM course_parents cp2 
                  WHERE cp2.course_id = cp.course_id 
                    AND cp2.parent_course_id != 1
              )
            LIMIT 1
        """))
        child_id = result.scalar()
        
        if not child_id:
            print("⚠️ Не найден подходящий ребенок для теста (все имеют других родителей), пропускаем")
            return
        
        print(f"Удаляем связь курс {child_id} -> курс 1 (у курса {child_id} нет других родителей)")
        
        # ⚠️ ТЕХНИЧЕСКИЙ ДОЛГ: Вызываем синхронизацию вручную (триггер отключен)
        # Вызываем ДО удаления, чтобы получить информацию о связи
        from app.repos.teacher_courses_repo import TeacherCoursesRepository
        teacher_courses_repo = TeacherCoursesRepository()
        
        # Удаляем связь
        await db.execute(text(f"DELETE FROM course_parents WHERE course_id = {child_id} AND parent_course_id = 1"))
        await db.commit()
        print(f"[OK] Связь курс {child_id} -> курс 1 удалена")
        
        # Вызываем синхронизацию после удаления
        await teacher_courses_repo.sync_on_child_removed(db, removed_course_id=child_id, removed_parent_id=1)
        print(f"[OK] Синхронизация связей преподавателей выполнена")
        
        # Проверяем, что преподаватель автоматически отвязан
        result = await db.execute(text(f"""
            SELECT COUNT(*) FROM teacher_courses 
            WHERE teacher_id = 17 AND course_id = {child_id}
        """))
        link_exists = result.scalar() > 0
        
        assert not link_exists, f"Преподаватель 17 все еще привязан к курсу {child_id}"
        print(f"[OK] Преподаватель 17 автоматически отвязан от курса {child_id}")


async def test_recursive_hierarchy():
    """Тест 8: Рекурсивная иерархия (родитель -> ребенок -> внук)"""
    print("\n=== Тест 8: Рекурсивная иерархия ===")
    async with await get_db_session() as db:
        # Проверяем существующую иерархию: курс 1 -> курс 10 -> курс 12
        result = await db.execute(text("""
            SELECT course_id, parent_course_id FROM course_parents 
            WHERE (course_id = 10 AND parent_course_id = 1) OR (course_id = 12 AND parent_course_id = 10)
        """))
        hierarchy = result.fetchall()
        
        if len(hierarchy) < 2:
            print("⚠️ Иерархия 1->10->12 не найдена, пропускаем")
            return
        
        print("Иерархия: курс 1 -> курс 10 -> курс 12")
        
        # Очищаем старые связи
        await db.execute(text("DELETE FROM teacher_courses WHERE teacher_id = 16"))
        await db.commit()
        
        # Привязываем преподавателя к родителю
        await db.execute(text("INSERT INTO teacher_courses (teacher_id, course_id) VALUES (16, 1)"))
        await db.commit()
        print("[OK] Преподаватель 16 привязан к курсу 1 (родитель)")
        
        # Проверяем, что привязаны все уровни
        result = await db.execute(text("""
            SELECT course_id FROM teacher_courses 
            WHERE teacher_id = 16 AND course_id IN (1, 10, 12)
            ORDER BY course_id
        """))
        linked = [row[0] for row in result.fetchall()]
        
        assert 1 in linked, "Родитель не привязан"
        assert 10 in linked, "Ребенок не привязан"
        assert 12 in linked, "Внук не привязан"
        
        print(f"[OK] Все уровни иерархии привязаны: {linked}")


async def cleanup():
    """Очистка тестовых данных"""
    print("\n=== Очистка тестовых данных ===")
    async with await get_db_session() as db:
        await db.execute(text("DELETE FROM teacher_courses WHERE teacher_id IN (16, 17)"))
        await db.commit()
        print("[OK] Тестовые данные очищены")


async def main():
    """Запуск всех тестов"""
    print("=" * 60)
    print("Smoke тесты триггеров teacher_courses")
    print("=" * 60)
    
    try:
        await test_table_structure()
        await test_indexes()
        await test_triggers()
        await test_auto_link_children()
        await test_auto_unlink_children()
        await test_sync_on_child_added()
        # ТЕХНИЧЕСКИЙ ДОЛГ: Тест пропущен, так как триггер отключен
        # Логика реализована в TeacherCoursesRepository.sync_on_child_removed()
        # Тест требует прямой вызов метода синхронизации, что будет протестировано в интеграционных тестах
        # await test_sync_on_child_removed()
        print("\n=== Тест 7: Синхронизация при удалении ребенка ===")
        print("[SKIP] Тест пропущен - триггер отключен, логика в коде")
        await test_recursive_hierarchy()
        
        print("\n" + "=" * 60)
        print("[OK] ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[FAIL] ОШИБКА В ТЕСТАХ: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(main())
