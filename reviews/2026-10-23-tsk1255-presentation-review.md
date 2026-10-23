# tsk-1255, поставка 2 — презентация по шагам анимации: ревью

**Решение:** ПРИНЯТО.

Объём: `app/services/lesson_import/{pptx_steps,slides,jobs}.py`, `store_bytes` в
`app/services/material_files_storage.py`, ручки `POST/GET …/lesson-imports/presentation` в
`app/api/v1/lesson_imports.py`, `LessonImportJob` в схеме, `Pillow==12.3.0`, тесты
`tests/test_lesson_import_presentation_tsk1255.py`. SPW: поле презентации и опрос в `LessonImport.tsx`,
хуки, `SlideShowBlock.parseStates` с подкаталогами + тест. Сервер — `2026-10-23-tsk1255-libreoffice-server.md`.

Отступление от плана: состояние фоновой задачи — Redis (TTL сутки), не таблица `lesson_imports`:
миграции tsk-1254/tsk-997 в тот же день не закоммичены, своя стала бы третьей головой. Черновик и так у клиента.

Проверено: разбор урока 6 даёт 91 состояние, карта шагов совпадает с `build/urok-06/plan.json` ручного
скрипта; на сервере под app — 91 страница за 25 с, пик 346 МБ. Шаги (абзац, фигура) — синтетический слайд.
Кадры: путь `/api/v1/materials/files/<sha>.webp` целиком в content → штатный ACL. Задача: чужой курс 404,
протухшая «running» → failed, ошибка записывается и замок снимается. PDF-ветка: pdftoppm + WebP.
Таймаут убивает группу процессов (иначе soffice.bin остаётся сиротой).

Без блокировки: путь кадра от корня сайта расширяет набор адресов, которые плеер грузит как img (GET,
пишет только методист); замки пилота и боя независимы при разных Redis. ERRORS.md: не требуется.
