-- Заполнение таблицы roles для пустой БД Learn.
-- Соответствие закреплено контрактом с фронтом (ТГ бот), см. docs/roles-and-api-contract.md:
--   1 admin, 2 methodist, 3 teacher, 4 student, 5 marketer, 6 customer
-- Выполнять после restore_learn_schema.sql.

INSERT INTO roles (id, name) VALUES
    (1, 'admin'),
    (2, 'methodist'),
    (3, 'teacher'),
    (4, 'student'),
    (5, 'marketer'),
    (6, 'customer')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;
