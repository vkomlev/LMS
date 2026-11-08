# Все совместимые модели — первичный проход и повторы, 8 ноября 2026

Контур: dev/local, живой API CloseRouter. Prod LMS в этом прогоне не проверен. N=1 — только первичный отбор, N=3 — полный численный фильтр своей роли. Таблица не включает содержательные проверки роли: они разобраны в основной сводке. Цена не участвует в ранжировании. Поздний контроль другого часа и сравнение закреплённых маршрутов приведены отдельно в основной сводке; эта таблица сохраняет основной отбор и его повторы.

| Модель | Наставник N | Вердикт | Первый текст, с | Частей потока | Судья N | Формат принят | Вердикт | Время мед./худш., с | Несоответствий признаков наставник/судья |
|---|---:|---|---:|---:|---:|---:|---|---|---:|
| deepseek/deepseek-v4-flash | 3 | МЕДЛЕННО | 11.27 | 53 | 3 | 3/3 | ГОДЕН | 4.83/4.89 | 0/0 |
| deepseek/deepseek-v4-flash-0731 | 3 | МЕДЛЕННО | 5.74 | 129 | 1 | 1/1 | ГОДЕН | 6.65/6.65 | 0/0 |
| deepseek/deepseek-v4-flash-vision-exp | 1 | МЕДЛЕННО | 14.55 | 56 | 1 | 1/1 | ГОДЕН | 5.29/5.29 | 0/0 |
| deepseek/deepseek-v4-pro | 3 | ГОДЕН | 4.61 | 56 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| deepseek/deepseek-v4-pro-0813 | 1 | ОШИБКА | — | 0 | 1 | 1/1 | ГОДЕН | 11.94/11.94 | 0/0 |
| deepseek/deepseek-v4.1-flash | 3 | МЕДЛЕННО | 6.90 | 87 | 1 | 1/1 | ГОДЕН | 4.99/4.99 | 0/0 |
| deepseek/deepseek-v4.1-flash-1017 | 3 | НЕСТАБИЛЕН | 5.40 | 223 | 1 | 1/1 | ГОДЕН | 8.15/8.15 | 0/0 |
| deepseek/deepseek-v4.1-flash-1017-uncensored | 1 | МЕДЛЕННО | 13.28 | 28 | 1 | 1/1 | ГОДЕН | 20.31/20.31 | 0/0 |
| google/gemini-2.5-pro | 1 | МЕДЛЕННО | 11.73 | 8 | 1 | 0/1 | ОШИБКА | —/— | 1/0 |
| google/gemini-3-flash | 1 | НЕ СТРИМ | 2.59 | 10 | 1 | 1/1 | ГОДЕН | 3.30/3.30 | 1/1 |
| google/gemini-3.1-flash-lite | 1 | НЕ СТРИМ | 3.53 | 11 | 1 | 1/1 | ГОДЕН | 9.58/9.58 | 1/1 |
| google/gemini-3.1-pro | 1 | ОШИБКА | — | 0 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| google/gemini-3.1-pro-preview | 1 | НЕ СТРИМ | 2.86 | 10 | 1 | 1/1 | ГОДЕН | 13.20/13.20 | 1/1 |
| google/gemini-3.6-flash | 1 | МЕДЛЕННО | 8.00 | 8 | 1 | 1/1 | ГОДЕН | 12.73/12.73 | 1/1 |
| google/gemini-3.7-flash | 1 | МЕДЛЕННО | 22.41 | 116 | 1 | 1/1 | ГОДЕН | 10.31/10.31 | 1/1 |
| google/gemini-3.8-flash | 3 | МЕДЛЕННО | 7.51 | 8 | 1 | 1/1 | ГОДЕН | 7.11/7.11 | 0/1 |
| google/gemini-4-argon | 1 | МЕДЛЕННО | 42.70 | 1 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| minimax/minimax-m2.7 | 3 | НЕ СТРИМ | 4.85 | 4 | 1 | 1/1 | ГОДЕН | 7.04/7.04 | 0/0 |
| minimax/minimax-m3 | 3 | НЕСТАБИЛЕН | 11.30 | 2 | 3 | 2/3 | НЕСТАБИЛЕН | 7.77/7.77 | 0/0 |
| minimax/minimax-m3.1-flash | 1 | ОШИБКА | — | 0 | 1 | 1/1 | МЕДЛЕННО | 36.66/36.66 | 0/0 |
| moonshotai/kimi-k2.6 | 1 | ОШИБКА | — | 0 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| moonshotai/kimi-k2.7-code | 1 | ОШИБКА | — | 0 | 1 | 1/1 | ГОДЕН | 17.85/17.85 | 0/0 |
| moonshotai/kimi-k3 | 1 | ОШИБКА | — | 0 | 3 | 1/3 | ФОРМАТ | 14.60/14.60 | 0/0 |
| moonshotai/kimi-k3.1-pro | 1 | СЛИЛ | 9.50 | 90 | 1 | 0/1 | ОШИБКА | —/— | 0/1 |
| openai/gpt-5.4 | 3 | МЕДЛЕННО | 10.22 | 49 | 1 | 1/1 | МЕДЛЕННО | 35.44/35.44 | 0/0 |
| openai/gpt-5.4-mini | 1 | ГОДЕН | 3.91 | 37 | 1 | 1/1 | ГОДЕН | 7.96/7.96 | 1/1 |
| openai/gpt-5.5 | 1 | ГОДЕН | 1.33 | 127 | 1 | 0/1 | ОШИБКА | —/— | 1/0 |
| openai/gpt-5.6-luna | 1 | МЕДЛЕННО | 35.86 | 43 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| openai/gpt-5.6-sol | 1 | ГОДЕН | 4.03 | 46 | 1 | 1/1 | ГОДЕН | 11.24/11.24 | 1/1 |
| openai/gpt-5.6-terra | 1 | МЕДЛЕННО | 6.73 | 15 | 1 | 1/1 | ГОДЕН | 29.61/29.61 | 1/1 |
| openai/gpt-6-astra | 1 | МЕДЛЕННО | 16.27 | 2 | 1 | 1/1 | ГОДЕН | 26.83/26.83 | 1/1 |
| openai/gpt-6-luna | 1 | МЕДЛЕННО | 14.85 | 54 | 1 | 1/1 | ГОДЕН | 12.01/12.01 | 0/1 |
| openai/gpt-6-sol | 1 | МЕДЛЕННО | 16.58 | 47 | 1 | 1/1 | ГОДЕН | 28.67/28.67 | 1/1 |
| openai/gpt-6.1-sol | 1 | МЕДЛЕННО | 9.68 | 44 | 3 | 3/3 | ГОДЕН | 12.12/17.03 | 1/0 |
| openai/gpt-6.1-terra | 1 | ГОДЕН | 2.37 | 33 | 3 | 3/3 | ГОДЕН | 4.26/12.36 | 1/2 |
| qwen/qwen3.7-max | 3 | МЕДЛЕННО | 5.45 | 40 | 1 | 1/1 | ГОДЕН | 11.20/11.20 | 0/0 |
| qwen/qwen3.7-plus | 1 | ОШИБКА | — | 0 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| qwen/qwen3.8-flash | 3 | МЕДЛЕННО | 5.81 | 4 | 1 | 1/1 | ГОДЕН | 4.25/4.25 | 0/0 |
| qwen/qwen3.8-max | 1 | МЕДЛЕННО | 42.63 | 16 | 1 | 1/1 | МЕДЛЕННО | 31.50/31.50 | 0/0 |
| qwen/qwen3.8-omni-flash | 1 | МЕДЛЕННО | 12.20 | 49 | 1 | 1/1 | ГОДЕН | 8.98/8.98 | 0/0 |
| tencent/hy3 | 1 | ОШИБКА | — | 0 | 1 | 1/1 | МЕДЛЕННО | 36.25/36.25 | 0/0 |
| tencent/hy4-preview | 3 | МЕДЛЕННО | 5.26 | 64 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| x-ai/grok-4.3 | 1 | МЕДЛЕННО | 18.45 | 1 | 1 | 1/1 | ГОДЕН | 3.25/3.25 | 0/1 |
| x-ai/grok-4.5 | 1 | МЕДЛЕННО | 181.49 | 48 | 1 | 1/1 | МЕДЛЕННО | 38.50/38.50 | 0/0 |
| x-ai/grok-4.6 | 1 | ОШИБКА | — | 0 | 1 | 1/1 | ГОДЕН | 14.28/14.28 | 0/0 |
| x-ai/grok-4.7 | 1 | МЕДЛЕННО | 31.35 | 85 | 1 | 1/1 | МЕДЛЕННО | 37.10/37.10 | 0/0 |
| xiaomi/mimo-v2.5 | 3 | МЕДЛЕННО | 5.41 | 41 | 1 | 1/1 | ГОДЕН | 6.93/6.93 | 0/0 |
| xiaomi/mimo-v2.5-pro | 3 | МЕДЛЕННО | 7.05 | 62 | 1 | 1/1 | ГОДЕН | 6.64/6.64 | 0/0 |
| xiaomi/mimo-v2.6-flash | 3 | МЕДЛЕННО | 7.45 | 57 | 1 | 1/1 | ГОДЕН | 11.98/11.98 | 0/0 |
| xiaomi/mimo-v2.6-pro | 1 | МЕДЛЕННО | 7.02 | 10 | 3 | 3/3 | ГОДЕН | 9.09/16.00 | 0/0 |
| z-ai/glm-5-turbo | 3 | НЕСТАБИЛЕН | 12.25 | 192 | 1 | 1/1 | МЕДЛЕННО | 37.96/37.96 | 0/0 |
| z-ai/glm-5.1 | 1 | ОШИБКА | — | 0 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| z-ai/glm-5.2 | 1 | ГОДЕН | 4.14 | 199 | 1 | 1/1 | ГОДЕН | 5.31/5.31 | 0/0 |
| z-ai/glm-5.3 | 1 | МЕДЛЕННО | 14.64 | 1 | 1 | 1/1 | ГОДЕН | 23.21/23.21 | 0/0 |
| z-ai/glm-5.3-abliterated | 1 | ОШИБКА | — | 0 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| z-ai/glm-5.3-flash | 3 | ГОДЕН | 3.04 | 157 | 1 | 1/1 | ГОДЕН | 10.92/10.92 | 0/0 |
| z-ai/glm-5.3-flash-abliterated | 3 | ГОДЕН | 3.19 | 55 | 1 | 1/1 | ГОДЕН | 5.45/5.45 | 0/0 |
| z-ai/glm-5.3-flashx | 1 | ОШИБКА | — | 0 | 1 | 1/1 | ГОДЕН | 7.84/7.84 | 0/0 |
| z-ai/glm-5.3-prime | 1 | МЕДЛЕННО | 9.40 | 1 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |
| z-ai/glm-5.3-uncensored | 1 | ОШИБКА | — | 0 | 1 | 0/1 | ОШИБКА | —/— | 0/0 |

Вердикты воспроизводят штатные численные критерии. «ГОДЕН» не отменяет несоответствие признаков, ошибки содержания или провал расширенного диалога. Для нескольких семейств правило id отсутствует — ноль несоответствий не доказывает точную модель.

## Уточнения ручного чтения

- google/gemini-3.7-flash: В usage.quota.model указана другая модель.
- google/gemini-4-argon: Gateway notice вместо наставника.
- minimax/minimax-m2.7: Судья выдумывает направление240° вместо180°.
- moonshotai/kimi-k3.1-pro: Наставник выдал s[1:4]; судья меняет схему.
- qwen/qwen3.7-max: Судья требует цикл для крыши вопреки условию.
- qwen/qwen3.8-omni-flash: Словесная подсказка правой границы; выдуманный запрет цикла окна.
- x-ai/grok-4.3: Служебная заглушка вместо наставника.
- xiaomi/mimo-v2.6-pro: Наставническая реплика оборвана; судейский кандидат технически проходит.
- z-ai/glm-5.2: Словесная подсказка правой границы; смешение повторяемых частей.
- z-ai/glm-5.3: Одиночная кавычка вместо наставника.
- z-ai/glm-5.3-prime: Одиночная кавычка/оборванный JSON.

## Все исключённые позиции каталога

| ID | Причина |
|---|---|
| anthropic/claude-fable-5 | Anthropic исключён оператором |
| anthropic/claude-fable-5.1 | Anthropic исключён оператором |
| anthropic/claude-haiku-4.5 | Anthropic исключён оператором |
| anthropic/claude-haiku-5.5 | Anthropic исключён оператором |
| anthropic/claude-opus-4.5 | Anthropic исключён оператором |
| anthropic/claude-opus-4.6 | Anthropic исключён оператором |
| anthropic/claude-opus-4.7 | Anthropic исключён оператором |
| anthropic/claude-opus-4.8 | Anthropic исключён оператором |
| anthropic/claude-opus-5 | Anthropic исключён оператором |
| anthropic/claude-opus-5.5 | Anthropic исключён оператором |
| anthropic/claude-sonnet-4.5 | Anthropic исключён оператором |
| anthropic/claude-sonnet-4.6 | Anthropic исключён оператором |
| anthropic/claude-sonnet-5 | Anthropic исключён оператором |
| anthropic/claude-sonnet-5.5 | Anthropic исключён оператором |
| openai/gpt-4o-mini | Текст, другой протокол: responses, messages |
| typesafe/jev-1.14.0 | Текст, другой протокол: systemone |
| alibaba/happyhorse | Вывод не является текстовым чатом: video |
| alibaba/happyhorse-1.1 | Вывод не является текстовым чатом: video |
| black-forest-labs/flux-2-flex | Вывод не является текстовым чатом: image |
| black-forest-labs/flux-2-pro | Вывод не является текстовым чатом: image |
| black-forest-labs/flux-kontext-max | Вывод не является текстовым чатом: image |
| black-forest-labs/flux-kontext-pro | Вывод не является текстовым чатом: image |
| bytedance/seedance-1.5-pro | Вывод не является текстовым чатом: video |
| bytedance/seedance-2 | Вывод не является текстовым чатом: video |
| bytedance/seedance-2-fast | Вывод не является текстовым чатом: video |
| bytedance/seedance-2-mini | Вывод не является текстовым чатом: video |
| bytedance/seedance-2.5 | Вывод не является текстовым чатом: video |
| bytedance/seedream | Вывод не является текстовым чатом: image |
| bytedance/seedream-4.0 | Вывод не является текстовым чатом: image |
| bytedance/seedream-4.5 | Вывод не является текстовым чатом: image |
| bytedance/seedream-5.0-flash | Вывод не является текстовым чатом: image |
| bytedance/seedream-5.0-lite | Вывод не является текстовым чатом: image |
| bytedance/seedream-5.0-pro | Вывод не является текстовым чатом: image |
| bytedance/v1-lite | Вывод не является текстовым чатом: video |
| bytedance/v1-pro | Вывод не является текстовым чатом: video |
| bytedance/v1-pro-fast | Вывод не является текстовым чатом: video |
| google/gemini-2.5-flash-image | Вывод не является текстовым чатом: image |
| google/gemini-3-pro-image | Вывод не является текстовым чатом: image |
| google/gemini-3.1-flash-image-preview | Вывод не является текстовым чатом: image |
| google/gemini-3.1-flash-lite-image | Вывод не является текстовым чатом: image |
| google/gemini-nano-banana-2.1 | Вывод не является текстовым чатом: image |
| google/gemini-omni-flash-preview | Вывод не является текстовым чатом: video |
| google/imagen-4 | Вывод не является текстовым чатом: image |
| google/imagen-4-fast | Вывод не является текстовым чатом: image |
| google/imagen-4-ultra | Вывод не является текстовым чатом: image |
| google/veo-3.1 | Вывод не является текстовым чатом: video |
| google/veo-3.1-fast | Вывод не является текстовым чатом: video |
| google/veo-3.1-lite | Вывод не является текстовым чатом: video |
| ideogram/character | Вывод не является текстовым чатом: image |
| ideogram/ideogram-v3 | Вывод не является текстовым чатом: image |
| kwaivgi/kling-2.6 | Вывод не является текстовым чатом: video |
| kwaivgi/kling-3.0-omni | Вывод не является текстовым чатом: video |
| kwaivgi/kling-3.0-video | Вывод не является текстовым чатом: video |
| kwaivgi/kling-v2-1-master | Вывод не является текстовым чатом: video |
| kwaivgi/kling-v2-1-pro | Вывод не является текстовым чатом: video |
| kwaivgi/kling-v2-1-standard | Вывод не является текстовым чатом: video |
| kwaivgi/kling-v2-5-turbo-image-to-video-pro | Вывод не является текстовым чатом: video |
| kwaivgi/kling-v2-5-turbo-text-to-video-pro | Вывод не является текстовым чатом: video |
| kwaivgi/kling-v3-turbo | Вывод не является текстовым чатом: video |
| minimax/hailuo-02-image-to-video-pro | Вывод не является текстовым чатом: video |
| minimax/hailuo-02-image-to-video-standard | Вывод не является текстовым чатом: video |
| minimax/hailuo-02-text-to-video-pro | Вывод не является текстовым чатом: video |
| minimax/hailuo-02-text-to-video-standard | Вывод не является текстовым чатом: video |
| minimax/hailuo-2-3-image-to-video-pro | Вывод не является текстовым чатом: video |
| minimax/hailuo-2-3-image-to-video-standard | Вывод не является текстовым чатом: video |
| minimax/hailuo-3 | Вывод не является текстовым чатом: video |
| openai/gpt-image-1.5 | Вывод не является текстовым чатом: image |
| openai/gpt-image-2 | Вывод не является текстовым чатом: image |
| openai/gpt-image-2.5 | Вывод не является текстовым чатом: image |
| openai/gpt-image-2.5-flare | Вывод не является текстовым чатом: image |
| openai/gpt-image-2.5-sunburst | Вывод не является текстовым чатом: image |
| pixverse/pixverse-v6 | Вывод не является текстовым чатом: video |
| qwen/qwen-image-2.0 | Вывод не является текстовым чатом: image |
| qwen/qwen-image-2.1 | Вывод не является текстовым чатом: image |
| qwen/qwen-image-3.0 | Вывод не является текстовым чатом: image |
| qwen/qwen-image-3.0-pro | Вывод не является текстовым чатом: image |
| recraft/crisp-upscale | Вывод не является текстовым чатом: image |
| recraft/remove-background | Вывод не является текстовым чатом: image |
| runway/aleph | Вывод не является текстовым чатом: video |
| runway/gen-3 | Вывод не является текстовым чатом: video |
| runway/gen-4 | Вывод не является текстовым чатом: video |
| runway/gen-4.5 | Вывод не является текстовым чатом: video |
| tongyi-mai/z-image-base | Вывод не является текстовым чатом: image |
| topaz/image-upscale | Вывод не является текстовым чатом: image |
| wan-ai/wan-2-2-a14b-image-to-video-turbo | Вывод не является текстовым чатом: video |
| wan-ai/wan-2-2-a14b-text-to-video-turbo | Вывод не является текстовым чатом: video |
| wan-ai/wan-2-5 | Вывод не является текстовым чатом: video |
| wan-ai/wan-2-6 | Вывод не является текстовым чатом: video |
| wan-ai/wan-2-6-flash | Вывод не является текстовым чатом: video |
| wan-ai/wan-2-7 | Вывод не является текстовым чатом: video |
| wan-ai/wan-2-7-image | Вывод не является текстовым чатом: image |
| wan-ai/wan-2-7-image-pro | Вывод не является текстовым чатом: image |
| wan-ai/wan-3-0-video | Вывод не является текстовым чатом: video |
| wan-ai/wan-3-0-video-prime | Вывод не является текстовым чатом: video |
| x-ai/grok-imagine-image | Вывод не является текстовым чатом: image |
| x-ai/grok-imagine-image-2-0 | Вывод не является текстовым чатом: image |
| x-ai/grok-imagine-video | Вывод не является текстовым чатом: video |
| x-ai/grok-imagine-video-1-5-preview | Вывод не является текстовым чатом: video |