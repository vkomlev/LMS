# app/db/base.py

from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

# --- ниже — импорты всех моделей, чтобы они сразу зарегистрировались в метаданных и в clsregistry
import app.models.association_tables   # таблицы связей
import app.models.roles
import app.models.access_requests  # ДО users, так как users ссылается на AccessRequests
import app.models.attempts
import app.models.users
import app.models.messages
import app.models.notifications
import app.models.achievements
import app.models.courses
import app.models.difficulty_levels
import app.models.materials
import app.models.social_posts
import app.models.tasks
import app.models.user_achievements
import app.models.user_courses
import app.models.task_results
import app.models.student_task_progress
import app.models.help_requests
import app.models.help_request_replies
import app.models.identity_link
import app.models.user_session
import app.models.magic_link
import app.models.audit_event
import app.models.guest_session
import app.models.guest_attempt
import app.models.assignment_rule
import app.models.assignment_event
import app.models.operating_hours
import app.models.lesson_slot
import app.models.lesson_slot_student
import app.models.lesson_slot_teacher
import app.models.lesson_occurrence
import app.models.lesson_occurrence_participant
import app.models.lesson_occurrence_teacher
import app.models.attendance_event
import app.models.parent_access_link

