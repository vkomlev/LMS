# Формат Claude Code session JSONL

## Расположение

`~/.claude/projects/<project-slug>/<session-uuid>.jsonl`

### Правило project-slug
Абсолютный путь проекта с заменой разделителей:
- `d:\Work\ContentFactory` → `D--Work-ContentFactory` (диск в upper case, `\` и `:` → `-`)
- `d:\Work\IT_Businessman` → `D--Work-IT-Businessman` (подчёркивание → дефис)
- `C:\Users\user\.openclaw` → `C--Users-user--openclaw` (точка → дефис, двойной дефис для dot-folder)

Для поиска папки по пути — Glob `~/.claude/projects/*` и матч по нормализованному имени.

## Структура файла

Один JSONL = одна сессия (один чат). Каждая строка — отдельное JSON-событие.
Файлы могут быть очень большими (десятки МБ) — читать с лимитами.

## Типы событий (`type`)

### `queue-operation`
Служебные: enqueue/dequeue входных сообщений. Для дайджеста НЕ нужны.

```json
{"type":"queue-operation","operation":"enqueue","timestamp":"...","sessionId":"...","content":"/skill-name ..."}
```

### `user`
Сообщение пользователя.
```json
{"parentUuid":null,"type":"user","message":{"role":"user","content":"..."}}
```
`content` может быть:
- строкой (простое сообщение)
- массивом объектов с `type`: `text`, `tool_result`, `image`

### `assistant`
Ответ Claude. Содержит `message.content` — массив блоков:
- `{"type":"text","text":"..."}` — обычный текст
- `{"type":"tool_use","name":"Edit","input":{...}}` — вызов инструмента
- `{"type":"thinking","thinking":"..."}` — reasoning (если включён)

### `attachment` / `deferred_tools_delta`
Служебные события о подключении инструментов. Для дайджеста НЕ нужны.

## Что извлекать для дайджеста

### Полезный сигнал
1. **user-сообщения** — что именно попросили
   - Фильтр: только `type:user` где `message.content` — строка или массив с `type:text`
   - Игнорировать `tool_result` блоки внутри user — это возвраты tool, не задачи
   - Игнорировать system-reminder и командные метаданные (`<command-message>`, `<command-name>`)

2. **assistant text-блоки** — что Claude отвечал (суть решения)
   - Брать `message.content[].text` где `type:text`
   - Первые 2-3 предложения — обычно резюме того, что сделал

3. **tool_use names + input summary** — что физически делал
   - `Edit` / `Write` с `file_path` — что меняли/создавали
   - `Bash` с `description` — что выполняли
   - `WebSearch` / `WebFetch` — что исследовали
   - Skill calls — какие skills запускали

### Шум (игнорировать)
- `queue-operation`
- `<system-reminder>` блоки
- Длинные tool_result выводы (оставить только факт вызова и имя)
- Повторяющиеся TodoWrite updates
- `deferred_tools_delta`
- Сырые коды/HTML дампы

## Временные поля

- `timestamp` — ISO 8601 в UTC. Для дневной суммаризации фильтровать по дате локальной зоны владельца (МСК по умолчанию для Виктора).
- `sessionId` совпадает с именем файла `{uuid}.jsonl`

## Примерный парсинг (псевдокод)

```python
import json
from pathlib import Path
from datetime import datetime, timezone

def iter_events(jsonl_path, start_date, end_date):
    """Выдаёт события в диапазоне дат."""
    with open(jsonl_path, encoding='utf-8') as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = ev.get('timestamp')
            if not ts:
                continue
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            if start_date <= dt.date() <= end_date:
                yield ev

def extract_user_prompts(events):
    """Извлекает сообщения пользователя."""
    for ev in events:
        if ev.get('type') != 'user':
            continue
        content = ev.get('message', {}).get('content')
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for block in content:
                if block.get('type') == 'text':
                    yield block.get('text', '')

def extract_tool_calls(events):
    """Какие инструменты вызывались."""
    for ev in events:
        if ev.get('type') != 'assistant':
            continue
        for block in ev.get('message', {}).get('content', []):
            if block.get('type') == 'tool_use':
                yield {
                    'tool': block.get('name'),
                    'input': block.get('input', {})
                }
```

## Ограничения и риски

- **Объём:** одна сессия может быть 10-100 МБ. Читать с контролем памяти, не весь файл в переменную
- **Privacy:** JSONL содержит весь ввод пользователя — не публиковать сырые данные
- **Текст может быть огрызками:** длинные сообщения иногда обрезаются на границах событий
- **Side-chain сессии:** `isSidechain:true` означает суб-агента (Task tool). Для дайджеста Виктора обычно интересен основной поток — фильтровать по `isSidechain:false` или учитывать отдельно
- **Секреты:** никогда не выдавать сырые системные напоминания, ключи API, токены в дайджесте
