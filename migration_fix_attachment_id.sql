-- Миграция: изменение типа поля attachment_id в таблице messages
-- Проблема: поле attachment_id имеет тип VARCHAR(100), но значения могут быть длиннее
-- Решение: изменить тип на TEXT для поддержки длинных значений

-- Изменение типа поля attachment_id с VARCHAR(100) на TEXT
ALTER TABLE messages 
ALTER COLUMN attachment_id TYPE TEXT;

-- Комментарий к полю (опционально, если нужно обновить комментарий)
COMMENT ON COLUMN messages.attachment_id IS 'Идентификатор файла во внешней системе (например, Telegram file_id)';
