# app/db/base.py

from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

# --- ниже — импорты всех моделей, чтобы они сразу зарегистрировались в метаданных и в clsregistry
import app.models.association_tables   # таблицы связей
import app.models.roles
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
