# tsk-1247, шаг 2: сценарии тренажёров «Ты — интерпретатор» и «Цикл-машина»

Дата: 2026-10-29. Роль: методист. Код продукта не менялся.

## Контекст
Шаг 2 плана из брифа `docs/briefs/tsk-1247-interactive-trainers.md` (§7): финальные тексты интерпретатора
и сценарий цикл-машины в формате JSON (§D.4 отчёта исследования, формат расширен и описан).

## Что сделано
- `docs/design/trainers/scenarios/interpreter-input.json` — 3 уровня, 3 темы кода × вариант `int`/`noint`, покрыты все 6 пунктов карты ошибок узла 106.
- `docs/design/trainers/scenarios/loop-machine.json` — 3 уровня (while-счётчик, for/range, копилка и отступ), 9 кодов ошибок.
- `docs/design/trainers/scenarios/README.md` — схема `trainer-scenario/1`, карты «ошибка → шаг → разбор», сверка с заданиями 106 и 110, Блум и время, самопроверка.
- `docs/design/trainers/scenarios/check_scenarios.py` — сверка с python3: 7961 проверка, 0 ошибок.
- `.gitignore`: `!docs/design/` (по правилу «новая папка docs — исключение в .gitignore»).

## Ревью
Независимый агент: карта полная, готовых решений нет. 1 блокер (неверное утверждение о склейке `str + int`)
и 4 важных замечания исправлены до коммита. Список — README §5.2.

## Diff
`reviews/2026-10-29-tsk1247-trainer-scenarios.diff`

```diff
+!docs/design/
+!docs/design/**
```
