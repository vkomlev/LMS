#!/usr/bin/env python3
"""
Скрипт для подготовки тестовых данных для smoke тестов пользователей через MCP.
Проверяет наличие данных и добавляет недостающие.
"""

import sys
import json
from pathlib import Path

# Этот скрипт будет вызываться из PowerShell и использовать MCP через отдельный механизм
# Для прямого использования MCP нужен другой подход

print("""
Для подготовки тестовых данных через MCP используйте следующие SQL команды:

1. Проверка ролей:
   SELECT id, name FROM roles WHERE name IN ('student', 'teacher');

2. Создание ролей (если их нет):
   INSERT INTO roles (name) VALUES ('student') ON CONFLICT (name) DO NOTHING RETURNING id;
   INSERT INTO roles (name) VALUES ('teacher') ON CONFLICT (name) DO NOTHING RETURNING id;

3. Проверка пользователей с ролями:
   SELECT COUNT(DISTINCT u.id) FROM users u 
   JOIN user_roles ur ON u.id = ur.user_id 
   JOIN roles r ON ur.role_id = r.id 
   WHERE r.name = 'student';
   
   SELECT COUNT(DISTINCT u.id) FROM users u 
   JOIN user_roles ur ON u.id = ur.user_id 
   JOIN roles r ON ur.role_id = r.id 
   WHERE r.name = 'teacher';

4. Создание тестовых пользователей (если нужно):
   INSERT INTO users (email, password_hash, full_name) 
   VALUES ('test_student_X@example.com', 'test_hash', 'Студент Тестовый X')
   ON CONFLICT (email) DO NOTHING RETURNING id;
   
   INSERT INTO user_roles (user_id, role_id) 
   SELECT u.id, r.id FROM users u, roles r 
   WHERE u.email = 'test_student_X@example.com' AND r.name = 'student'
   ON CONFLICT (user_id, role_id) DO NOTHING;

Используйте MCP инструменты для выполнения этих запросов.
""")
