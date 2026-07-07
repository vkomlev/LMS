"""baseline: восстанови 18 таблиц, созданных до начала трекинга Alembic

Первая реально трекаемая миграция (`add_courses_triggers`) создаёт только
триггеры/функции на `user_courses`/`courses`/`course_dependencies` — сами
таблицы никогда не создавались через Alembic (исходная схема была поднята
через `Base.metadata.create_all()` до того, как проект начал версионировать
миграции). Из-за этого `alembic upgrade head` на пустой БД падает с самого
начала (`UndefinedTableError`). См. `TODOS.md` LMS.

Метод получения точного DDL (простая рефлексия текущей БД не подошла бы —
многие из этих 18 таблиц с тех пор изменялись более поздними миграциями,
которые добавляли колонки/индексы через `add_column`/`create_index`, и
если бы baseline создавал их заново — эти миграции упали бы на "column
already exists"):

1. Schema-only копия dev-БД (структура без данных — реальные данные ломают
   некоторые исторические `downgrade()` на CHECK-ограничениях, которые были
   позже ослаблены, а старые строки более раннему ограничению уже не
   удовлетворяют; для получения DDL данные не нужны).
2. `alembic downgrade base` на этой копии — прогоняет ВСЕ настоящие
   `downgrade()` всех 34 миграций в обратном порядке до полного нуля.
   Важно: `downgrade add_courses_triggers` (а не `base`) была ПЕРВОЙ
   попыткой и оказалась на один шаг позже нужного — `add_courses_triggers`
   сама добавляет `check_no_self_dependency` на `course_dependencies` и
   `idx_user_courses_user_order`, так что цель `add_courses_triggers`
   оставляет их применёнными, и baseline с ними же внутри дублировал их
   при последующем прогоне `add_courses_triggers.upgrade()` — падение на
   "constraint already exists" на живом прогоне `alembic upgrade head`
   с нуля выявило и исправило эту ошибку до коммита.
3. Рефлексия результата (`achievements`/`courses`/.../`task_results`,
   18 таблиц) — это и есть точный DDL для baseline.

Это вскрыло, что несколько таблиц СИЛЬНО отличаются от текущего состояния:
`materials` и `tasks` изначально были гораздо проще (без title/description/
caption/is_active/external_uid/created_at/updated_at/requirement_level у
materials; без time_limit_sec/max_attempts/order_position/is_active/
requirement_level у tasks) — все эти поля добавлены `add_column` в более
поздних миграциях. Аналогично `notifications` изначально не имел user_id/
kind/title/payload/read_at (добавлены M8), `difficulties` не имел `uid`
(добавлен add_difficulties_uid), `users.email`/`password_hash` были
`NOT NULL` (ослаблено до nullable в M1), `courses.is_public_demo` не
существовал (добавлен M11), `attempts` не имел time_expired/cancelled_at/
cancel_reason, `user_courses` не имел is_active, `task_results` не имел
review_claim_*/scale_scores. `courses.parent_course_id` — обратный случай:
СУЩЕСТВОВАЛ изначально, удалён позже миграцией перехода на
many-to-many иерархию курсов (`course_parents`).

Полный чистый прогон `alembic upgrade head` с нуля (все 35 миграций подряд)
на пустой тестовой БД подтверждён рабочим после этой правки.

Каждый `op.execute()` содержит РОВНО одно SQL-выражение — asyncpg (драйвер
Alembic в этом проекте) не поддерживает несколько statement'ов в одном
`execute()`, в отличие от psycopg2, которым проверялась DDL изначально.

Легаси-имена sequence (tasks/notifications/task_results когда-то назывались
assignments/template_versions/assignment_results — сам факт переименования
таблиц тоже предшествует Alembic, только sequence/constraint имена не были
переименованы вместе с таблицей) воспроизведены явно через ALTER SEQUENCE.

После этой миграции `add_courses_triggers.down_revision` указывает сюда
вместо `None` — эта миграция стала новым настоящим корнем цепочки.
Существующие dev/prod БД (уже находятся на более поздних ревизиях) эта
правка не затрагивает: Alembic не перевыполняет уже пройденные шаги.

Revision ID: baseline_pre_alembic_schema
Revises:
Create Date: 2026-07-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "baseline_pre_alembic_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE access_level_type AS ENUM ('self_guided', 'auto_check', 'manual_check', 'group_sessions', 'personal_teacher');
    """)
    op.execute("""
        CREATE TYPE access_request_flag AS ENUM ('completed', 'rejected', 'not_ready');
    """)
    op.execute("""
        CREATE TYPE content_type AS ENUM ('text', 'video', 'link', 'pdf', 'audio', 'image', 'office_document', 'script', 'document');
    """)
    op.execute("""
        CREATE TABLE public.achievements (
        	id SERIAL NOT NULL,
        	name VARCHAR NOT NULL,
        	condition JSONB NOT NULL,
        	description TEXT,
        	badge_image_url VARCHAR(512),
        	reward_points INTEGER DEFAULT 0 NOT NULL,
        	is_recurring BOOLEAN DEFAULT false NOT NULL,
        	CONSTRAINT achievements_pkey PRIMARY KEY (id),
        	CONSTRAINT achievements_name_key UNIQUE NULLS DISTINCT (name)
        );
    """)
    op.execute("""
        CREATE TABLE public.courses (
        	id SERIAL NOT NULL,
        	title VARCHAR NOT NULL,
        	access_level access_level_type NOT NULL,
        	description TEXT,
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	is_required BOOLEAN DEFAULT false NOT NULL,
        	course_uid VARCHAR,
        	parent_course_id INTEGER,
        	CONSTRAINT courses_pkey PRIMARY KEY (id),
        	CONSTRAINT courses_parent_course_id_fkey FOREIGN KEY(parent_course_id) REFERENCES public.courses (id) ON DELETE SET NULL,
        	CONSTRAINT courses_course_uid_key UNIQUE NULLS DISTINCT (course_uid)
        );
    """)
    op.execute("""
        CREATE INDEX idx_courses_parent ON public.courses (parent_course_id);
    """)
    op.execute("""
        CREATE TABLE public.difficulties (
        	id SERIAL NOT NULL,
        	code VARCHAR NOT NULL,
        	name_ru VARCHAR NOT NULL,
        	weight INTEGER NOT NULL,
        	CONSTRAINT difficulties_pkey PRIMARY KEY (id),
        	CONSTRAINT difficulties_code_key UNIQUE NULLS DISTINCT (code)
        );
    """)
    op.execute("""
        CREATE TABLE public.roles (
        	id INTEGER NOT NULL,
        	name VARCHAR NOT NULL,
        	CONSTRAINT roles_pkey PRIMARY KEY (id),
        	CONSTRAINT roles_name_key UNIQUE NULLS DISTINCT (name)
        );
    """)
    op.execute("""
        CREATE TABLE public.users (
        	id SERIAL NOT NULL,
        	email VARCHAR NOT NULL,
        	password_hash VARCHAR NOT NULL,
        	full_name VARCHAR,
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	tg_id BIGINT,
        	CONSTRAINT users_pkey PRIMARY KEY (id),
        	CONSTRAINT users_email_key UNIQUE NULLS DISTINCT (email)
        );
    """)
    op.execute("""
        CREATE TABLE public.access_requests (
        	id SERIAL NOT NULL,
        	user_id INTEGER NOT NULL,
        	role_id INTEGER NOT NULL,
        	requested_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	flag access_request_flag DEFAULT 'not_ready'::access_request_flag NOT NULL,
        	CONSTRAINT access_requests_pkey PRIMARY KEY (id),
        	CONSTRAINT access_requests_user_id_fkey FOREIGN KEY(user_id) REFERENCES public.users (id) ON DELETE CASCADE,
        	CONSTRAINT access_requests_role_id_fkey FOREIGN KEY(role_id) REFERENCES public.roles (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE TABLE public.attempts (
        	id SERIAL NOT NULL,
        	user_id INTEGER NOT NULL,
        	course_id INTEGER,
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	finished_at TIMESTAMP WITH TIME ZONE,
        	source_system VARCHAR(50) DEFAULT 'system'::character varying NOT NULL,
        	meta JSONB,
        	CONSTRAINT attempts_pkey PRIMARY KEY (id),
        	CONSTRAINT attempts_course_id_fkey FOREIGN KEY(course_id) REFERENCES public.courses (id) ON DELETE SET NULL,
        	CONSTRAINT attempts_user_id_fkey FOREIGN KEY(user_id) REFERENCES public.users (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE INDEX idx_attempts_course ON public.attempts (course_id);
    """)
    op.execute("""
        CREATE INDEX idx_attempts_created_at ON public.attempts (created_at);
    """)
    op.execute("""
        CREATE INDEX idx_attempts_user ON public.attempts (user_id);
    """)
    op.execute("""
        CREATE TABLE public.course_dependencies (
        	course_id INTEGER NOT NULL,
        	required_course_id INTEGER NOT NULL,
        	CONSTRAINT course_dependencies_pkey PRIMARY KEY (course_id, required_course_id),
        	CONSTRAINT course_dependencies_course_id_fkey FOREIGN KEY(course_id) REFERENCES public.courses (id) ON DELETE CASCADE,
        	CONSTRAINT course_dependencies_required_course_id_fkey FOREIGN KEY(required_course_id) REFERENCES public.courses (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE TABLE public.materials (
        	id SERIAL NOT NULL,
        	course_id INTEGER NOT NULL,
        	type content_type NOT NULL,
        	content JSONB NOT NULL,
        	order_position INTEGER NOT NULL,
        	CONSTRAINT materials_pkey PRIMARY KEY (id),
        	CONSTRAINT materials_course_id_fkey FOREIGN KEY(course_id) REFERENCES public.courses (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE TABLE public.messages (
        	id SERIAL NOT NULL,
        	message_type VARCHAR NOT NULL,
        	content JSONB NOT NULL,
        	sender_id INTEGER,
        	recipient_id INTEGER NOT NULL,
        	reply_to_id INTEGER,
        	thread_id INTEGER,
        	forwarded_from_id INTEGER,
        	attachment_url TEXT,
        	attachment_id TEXT,
        	sent_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	is_read BOOLEAN DEFAULT false NOT NULL,
        	source_system VARCHAR(50) DEFAULT 'system'::character varying NOT NULL,
        	CONSTRAINT messages_pkey PRIMARY KEY (id),
        	CONSTRAINT messages_reply_to_id_fkey FOREIGN KEY(reply_to_id) REFERENCES public.messages (id) ON DELETE SET NULL,
        	CONSTRAINT messages_forwarded_from_id_fkey FOREIGN KEY(forwarded_from_id) REFERENCES public.messages (id) ON DELETE SET NULL,
        	CONSTRAINT messages_recipient_id_fkey FOREIGN KEY(recipient_id) REFERENCES public.users (id) ON DELETE CASCADE,
        	CONSTRAINT messages_thread_id_fkey FOREIGN KEY(thread_id) REFERENCES public.messages (id) ON DELETE SET NULL,
        	CONSTRAINT messages_sender_id_fkey FOREIGN KEY(sender_id) REFERENCES public.users (id) ON DELETE SET NULL
        );
    """)
    op.execute("""
        CREATE INDEX idx_messages_recipient ON public.messages (recipient_id);
    """)
    op.execute("""
        CREATE TABLE public.notifications (
        	id SERIAL NOT NULL,
        	content TEXT NOT NULL,
        	modified_by INTEGER,
        	modified_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	CONSTRAINT template_versions_pkey PRIMARY KEY (id),
        	CONSTRAINT template_versions_modified_by_fkey FOREIGN KEY(modified_by) REFERENCES public.users (id)
        );
    """)
    op.execute("""
        CREATE TABLE public.social_posts (
        	id SERIAL NOT NULL,
        	user_id INTEGER NOT NULL,
        	content TEXT NOT NULL,
        	post_date TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	course_id INTEGER,
        	CONSTRAINT social_posts_pkey PRIMARY KEY (id),
        	CONSTRAINT social_posts_course_id_fkey FOREIGN KEY(course_id) REFERENCES public.courses (id),
        	CONSTRAINT social_posts_user_id_fkey FOREIGN KEY(user_id) REFERENCES public.users (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE INDEX idx_social_posts_user ON public.social_posts (user_id, post_date);
    """)
    op.execute("""
        CREATE TABLE public.student_teacher_links (
        	student_id INTEGER NOT NULL,
        	teacher_id INTEGER NOT NULL,
        	linked_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	CONSTRAINT student_teacher_links_pkey PRIMARY KEY (student_id, teacher_id),
        	CONSTRAINT student_teacher_links_teacher_id_fkey FOREIGN KEY(teacher_id) REFERENCES public.users (id) ON DELETE CASCADE,
        	CONSTRAINT student_teacher_links_student_id_fkey FOREIGN KEY(student_id) REFERENCES public.users (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE TABLE public.tasks (
        	id SERIAL NOT NULL,
        	external_uid TEXT,
        	max_score INTEGER,
        	task_content JSONB NOT NULL,
        	course_id INTEGER NOT NULL,
        	difficulty_id INTEGER NOT NULL,
        	solution_rules JSONB,
        	CONSTRAINT assignments_pkey PRIMARY KEY (id),
        	CONSTRAINT tasks_course_id_fkey FOREIGN KEY(course_id) REFERENCES public.courses (id) ON DELETE CASCADE,
        	CONSTRAINT tasks_external_uid_key UNIQUE NULLS DISTINCT (external_uid),
        	CONSTRAINT tasks_difficulty_id_fkey FOREIGN KEY(difficulty_id) REFERENCES public.difficulties (id) ON DELETE RESTRICT
        );
    """)
    op.execute("""
        CREATE TABLE public.user_achievements (
        	user_id INTEGER NOT NULL,
        	achievement_id INTEGER NOT NULL,
        	earned_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	progress JSONB,
        	CONSTRAINT user_achievements_pkey PRIMARY KEY (user_id, achievement_id),
        	CONSTRAINT user_achievements_user_id_fkey FOREIGN KEY(user_id) REFERENCES public.users (id) ON DELETE CASCADE,
        	CONSTRAINT user_achievements_achievement_id_fkey FOREIGN KEY(achievement_id) REFERENCES public.achievements (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE INDEX idx_user_achievements ON public.user_achievements (user_id, earned_at);
    """)
    op.execute("""
        CREATE TABLE public.user_courses (
        	user_id INTEGER NOT NULL,
        	course_id INTEGER NOT NULL,
        	added_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	order_number SMALLINT,
        	CONSTRAINT user_courses_pkey PRIMARY KEY (user_id, course_id),
        	CONSTRAINT user_courses_course_id_fkey FOREIGN KEY(course_id) REFERENCES public.courses (id) ON DELETE CASCADE,
        	CONSTRAINT user_courses_user_id_fkey FOREIGN KEY(user_id) REFERENCES public.users (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE TABLE public.user_roles (
        	user_id INTEGER NOT NULL,
        	role_id INTEGER NOT NULL,
        	CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_id),
        	CONSTRAINT user_roles_user_id_fkey FOREIGN KEY(user_id) REFERENCES public.users (id) ON DELETE CASCADE,
        	CONSTRAINT user_roles_role_id_fkey FOREIGN KEY(role_id) REFERENCES public.roles (id)
        );
    """)
    op.execute("""
        CREATE TABLE public.task_results (
        	id SERIAL NOT NULL,
        	score INTEGER NOT NULL,
        	user_id INTEGER NOT NULL,
        	task_id INTEGER NOT NULL,
        	submitted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	metrics JSONB,
        	count_retry SMALLINT DEFAULT 0 NOT NULL,
        	received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        	attempt_id INTEGER,
        	answer_json JSONB,
        	max_score INTEGER,
        	is_correct BOOLEAN,
        	checked_at TIMESTAMP WITH TIME ZONE,
        	checked_by INTEGER,
        	source_system VARCHAR(50) DEFAULT 'system'::character varying NOT NULL,
        	CONSTRAINT assignment_results_pkey PRIMARY KEY (id),
        	CONSTRAINT task_results_user_id_fkey FOREIGN KEY(user_id) REFERENCES public.users (id) ON DELETE CASCADE,
        	CONSTRAINT task_results_task_id_fkey FOREIGN KEY(task_id) REFERENCES public.tasks (id) ON DELETE CASCADE,
        	CONSTRAINT task_results_attempt_id_fkey FOREIGN KEY(attempt_id) REFERENCES public.attempts (id) ON DELETE CASCADE
        );
    """)
    op.execute("""
        CREATE INDEX idx_assignment_results ON public.task_results (user_id, task_id);
    """)

    # Легаси-имена sequence сохранены как в реальной истории (notifications = бывш.
    # template_versions, tasks = бывш. assignments, task_results = бывш.
    # assignment_results) — переименования таблиц произошли до начала трекинга
    # Alembic, но Postgres не переименовывает sequence вместе с таблицей — старые
    # имена sequence уже фактически используются в dev/prod.
    op.execute("""
        ALTER SEQUENCE notifications_id_seq RENAME TO template_versions_id_seq;
    """)
    op.execute("""
        ALTER SEQUENCE tasks_id_seq RENAME TO assignments_id_seq;
    """)
    op.execute("""
        ALTER SEQUENCE task_results_id_seq RENAME TO assignment_results_id_seq;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.task_results CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.user_roles CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.user_courses CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.user_achievements CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.tasks CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.student_teacher_links CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.social_posts CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.notifications CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.messages CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.materials CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.course_dependencies CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.attempts CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.access_requests CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.users CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.roles CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.difficulties CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.courses CASCADE;")
    op.execute("DROP TABLE IF EXISTS public.achievements CASCADE;")
    op.execute("DROP TYPE IF EXISTS content_type;")
    op.execute("DROP TYPE IF EXISTS access_request_flag;")
    op.execute("DROP TYPE IF EXISTS access_level_type;")
