# QA Fix: перемещение материала — 422 и MESSAGE_TOO_LONG

**Дата:** 2026-06-05  
**Проекты:** LMS + TG_LMS  
**Коммиты:** LMS `ed76ae6`, TG_LMS `465c8ae`

## Симптомы

- `POST /api/v1/materials/358/move` → 422 `Input should be greater than or equal to 1`
- Затем `TelegramBadRequest: MESSAGE_TOO_LONG` при попытке показать ошибку пользователю

## Корневая причина

**Баг 1 (LMS):** В `MaterialMoveRequest.new_order_position` стояло ограничение `ge=1`, но материал 540 в курсе 140 имеет `order_position = 0`. При перемещении материала 358 (позиция 1) вверх TG_LMS берёт позицию соседа (0) и отправляет её в API → 422.

Триггер `set_material_order_position` корректно обрабатывает позицию 0: сдвигает соседей и переставляет материал. Ограничение `ge=1` — ошибка спецификации, не защитная мера.

**Баг 2 (TG_LMS):** В ветке `api_error` функции `_handle_material_order_swap` передавалась полная строка исключения (`result.error_message`) в `callback.answer(..., show_alert=True)`. Telegram ограничивает alert-текст 200 символами.

## Исправления

| Файл | Правка |
|---|---|
| `LMS/app/schemas/materials.py` | `ge=1` → `ge=0` в `MaterialMoveRequest.new_order_position` |
| `TG_LMS/dialogs/materials.py` | `api_error`: заменить сырую строку ошибки дружелюбным текстом |
| `TG_LMS/common/utils/dialogs.py` | `send_error_message`: обрезать alert-текст до 200 символов защитно |

## Проверка

```bash
# LMS — после перезапуска сервера:
curl -X POST "http://localhost:8000/api/v1/materials/358/move?api_key=bot-key-1" \
  -H "Content-Type: application/json" \
  -d '{"new_order_position": 0}'
# Ожидается: 200, материал 358 перемещается на позицию 0, материал 540 сдвигается на 1
```

Кнопка «↑» для первого материала в списке курса теперь должна работать без ошибки.
