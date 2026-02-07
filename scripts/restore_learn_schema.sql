-- Восстановление структуры БД Learn (PostgreSQL)
-- Использование: создать БД, затем выполнить этот скрипт, затем: alembic upgrade head
-- Триггеры и последующие изменения схемы применяются через миграции Alembic.

BEGIN;

-- ========== ENUM-типы ==========
CREATE TYPE access_level_type AS ENUM (
    'self_guided', 'auto_check', 'manual_check', 'group_sessions', 'personal_teacher'
);

CREATE TYPE content_type AS ENUM (
    'text', 'video', 'link', 'pdf'
);
-- Миграции добавят: audio, image, office_document, script, document

CREATE TYPE access_request_flag AS ENUM (
    'completed', 'rejected', 'not_ready'
);

-- ========== Последовательности ==========
CREATE SEQUENCE IF NOT EXISTS assignments_id_seq;
CREATE SEQUENCE IF NOT EXISTS assignment_results_id_seq;
CREATE SEQUENCE IF NOT EXISTS template_versions_id_seq;
CREATE SEQUENCE IF NOT EXISTS access_requests_id_seq;
CREATE SEQUENCE IF NOT EXISTS attempts_id_seq;

-- ========== Таблицы (порядок с учётом внешних ключей) ==========

-- Роли
CREATE TABLE roles (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL UNIQUE
);
COMMENT ON TABLE roles IS 'Роли пользователей';

-- Пользователи
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    full_name VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tg_id BIGINT
);
COMMENT ON TABLE users IS 'Пользователи системы';

-- Связь пользователей и ролей
CREATE TABLE user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id),
    PRIMARY KEY (user_id, role_id)
);
COMMENT ON TABLE user_roles IS 'Связь пользователей с ролями';

-- Курсы (начальная схема: с parent_course_id для первой миграции; миграция 20260124 переведёт на course_parents)
CREATE TABLE courses (
    id SERIAL PRIMARY KEY,
    title VARCHAR NOT NULL,
    access_level access_level_type NOT NULL,
    description TEXT,
    parent_course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_required BOOLEAN NOT NULL DEFAULT false,
    course_uid VARCHAR UNIQUE
);
CREATE INDEX idx_courses_parent ON courses(parent_course_id);
COMMENT ON TABLE courses IS 'Курсы системы обучения';

-- Зависимости между курсами
CREATE TABLE course_dependencies (
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    required_course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    PRIMARY KEY (course_id, required_course_id)
);
COMMENT ON TABLE course_dependencies IS 'Зависимости между курсами';
-- CHECK check_no_self_dependency добавляется миграцией add_courses_triggers

-- Связь пользователей с курсами (порядок, дата добавления)
CREATE TABLE user_courses (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    order_number SMALLINT,
    PRIMARY KEY (user_id, course_id)
);
COMMENT ON TABLE user_courses IS 'Связь пользователей с курсами';

-- Уровни сложности (таблица difficulties)
CREATE TABLE difficulties (
    id SERIAL PRIMARY KEY,
    code VARCHAR NOT NULL UNIQUE,
    name_ru VARCHAR NOT NULL,
    weight INTEGER NOT NULL
);
COMMENT ON TABLE difficulties IS 'Уровни сложности заданий';

-- Задания
CREATE TABLE tasks (
    id INTEGER NOT NULL DEFAULT nextval('assignments_id_seq'),
    external_uid TEXT UNIQUE,
    max_score INTEGER,
    task_content JSONB NOT NULL,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    difficulty_id INTEGER NOT NULL REFERENCES difficulties(id) ON DELETE RESTRICT,
    solution_rules JSONB,
    CONSTRAINT assignments_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE tasks IS 'Задания курсов';
ALTER SEQUENCE assignments_id_seq OWNED BY tasks.id;

-- Попытки (перед task_results из-за FK attempt_id)
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY DEFAULT nextval('attempts_id_seq'),
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    source_system VARCHAR(50) NOT NULL DEFAULT 'system',
    meta JSONB
);
CREATE INDEX idx_attempts_user ON attempts(user_id);
CREATE INDEX idx_attempts_course ON attempts(course_id);
CREATE INDEX idx_attempts_created_at ON attempts(created_at);
COMMENT ON TABLE attempts IS 'Попытки прохождения заданий/тестов';
ALTER SEQUENCE attempts_id_seq OWNED BY attempts.id;

-- Результаты заданий
CREATE TABLE task_results (
    id INTEGER NOT NULL DEFAULT nextval('assignment_results_id_seq'),
    score INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metrics JSONB,
    count_retry SMALLINT NOT NULL DEFAULT 0,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempt_id INTEGER REFERENCES attempts(id) ON DELETE CASCADE,
    answer_json JSONB,
    max_score INTEGER,
    is_correct BOOLEAN,
    checked_at TIMESTAMPTZ,
    checked_by INTEGER,
    source_system VARCHAR(50) NOT NULL DEFAULT 'system',
    CONSTRAINT assignment_results_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_assignment_results ON task_results(user_id, task_id);
COMMENT ON TABLE task_results IS 'Результаты выполнения заданий';
ALTER SEQUENCE assignment_results_id_seq OWNED BY task_results.id;

-- Материалы (начальная схема; миграция materials_structure_triggers добавит поля и триггеры)
CREATE TABLE materials (
    id SERIAL PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    type content_type NOT NULL,
    content JSONB NOT NULL,
    order_position INTEGER NOT NULL
);
COMMENT ON TABLE materials IS 'Учебные материалы';

-- Сообщения
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    message_type VARCHAR NOT NULL,
    content JSONB NOT NULL,
    sender_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reply_to_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    thread_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    forwarded_from_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    attachment_url TEXT,
    attachment_id TEXT,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_read BOOLEAN NOT NULL DEFAULT false,
    source_system VARCHAR(50) NOT NULL DEFAULT 'system'
);
CREATE INDEX idx_messages_recipient ON messages(recipient_id);
COMMENT ON TABLE messages IS 'Сообщения между пользователями и преподавателями';

-- Уведомления (шаблоны)
CREATE TABLE notifications (
    id INTEGER NOT NULL DEFAULT nextval('template_versions_id_seq'),
    content TEXT NOT NULL,
    modified_by INTEGER CONSTRAINT template_versions_modified_by_fkey REFERENCES users(id),
    modified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT template_versions_pkey PRIMARY KEY (id)
);
ALTER SEQUENCE template_versions_id_seq OWNED BY notifications.id;
COMMENT ON TABLE notifications IS 'Версии шаблонов уведомлений';

-- Посты в соцленте
CREATE TABLE social_posts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    post_date TIMESTAMPTZ NOT NULL DEFAULT now(),
    course_id INTEGER REFERENCES courses(id)
);
CREATE INDEX idx_social_posts_user ON social_posts(user_id, post_date);
COMMENT ON TABLE social_posts IS 'Посты пользователей в социальной ленте';

-- Достижения
CREATE TABLE achievements (
    id SERIAL PRIMARY KEY,
    name VARCHAR NOT NULL UNIQUE,
    condition JSONB NOT NULL,
    description TEXT,
    badge_image_url VARCHAR(512),
    reward_points INTEGER NOT NULL DEFAULT 0,
    is_recurring BOOLEAN NOT NULL DEFAULT false
);
COMMENT ON TABLE achievements IS 'Достижения пользователей';

-- Связь пользователей и достижений
CREATE TABLE user_achievements (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    achievement_id INTEGER NOT NULL REFERENCES achievements(id) ON DELETE CASCADE,
    earned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    progress JSONB,
    PRIMARY KEY (user_id, achievement_id)
);
CREATE INDEX idx_user_achievements ON user_achievements(user_id, earned_at);
COMMENT ON TABLE user_achievements IS 'Связь пользователей с достижениями';

-- Запросы на доступ/роли
CREATE TABLE access_requests (
    id INTEGER PRIMARY KEY DEFAULT nextval('access_requests_id_seq'),
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    flag access_request_flag NOT NULL DEFAULT 'not_ready'
);
ALTER SEQUENCE access_requests_id_seq OWNED BY access_requests.id;
COMMENT ON TABLE access_requests IS 'Запросы пользователей на доступ/роли';

-- Связь студент — преподаватель
CREATE TABLE student_teacher_links (
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (student_id, teacher_id)
);
COMMENT ON TABLE student_teacher_links IS 'Привязка студентов к преподавателям';

COMMIT;

-- После выполнения этого скрипта выполните:
--   alembic upgrade head
-- Миграции создадут: course_parents, teacher_courses, все триггеры и ограничения из docs/database-triggers-contract.md,
-- а также обновят таблицу materials (поля title, description, caption, is_active, external_uid, created_at, updated_at и триггеры).
