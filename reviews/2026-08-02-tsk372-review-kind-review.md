# /review-gate — tsk-372 (review_kind в GET /teacher/reviews/pending)

**Решение: ПРИНЯТО**

Коммиты: LMS `e29bd18`, SPW `a6402d5`, cross-project docs ContentBackbone `ce3afe6`.
Уже задеплоено на прод (LMS + SPW) и проверено живым прогоном.

## Проверка по измерениям

1. **Соответствие целям** — все пункты декомпозиции tsk-372 закрыты: `review_kind`
   в LMS-эндпоинте (аддитивно, default не изменился), UI-переключатель SPW,
   claim-next/pending-count/workload сознательно не тронуты (обоснование в
   плане совпадает с решением, принятым в декомпозиции — «свериться с ботом»).
   DRIFT не найден.
2. **Корректность** — edge cases покрыты тестами: default=mandatory не видит
   optional; optional не видит mandatory и не видит SA_COM без вердикта
   (`is_correct IS NULL`, гипотетический промежуточный статус); all — union.
   `claim_review_by_id` уже не различал mandatory/optional (проверено чтением
   кода, не только по памяти) — правок не потребовалось, регресса нет.
3. **БД/миграции** — не применимо, read-only query-параметр.
4. **Безопасность** — identity-гейт (`current_user.id == teacher_id`) не
   затронут; IDOR-поверхность не расширилась (тот же ACL `REVIEW_ACL_SQL`).
5. **Тесты** — LMS 4 новых (mandatory unchanged, optional, all, edge-case),
   SPW 8 новых (переключатель, URL, badge). Полный прогон: LMS 1519/11 (было
   1515/11), SPW 902 passed + 1 pre-existing flake (`prism-highlight`,
   подтверждён проходящим в изоляции). **Живой прогон сделан** —
   `live-browse.mjs` на `/teacher` (mandatory=0, optional=511, badge-текст
   «Авто: неверно»/«Авто: верно» подтверждён на реальных данных) и
   `/methodist/reviews?kind=all` (511 = 0+511, union верна). aria-snapshot
   подтвердил `aria-pressed`/`[pressed]` на активной кнопке фильтра.
6. **Docs drift** — cross-project контракт (`ContentBackbone/docs/cross-project/`)
   обновлён в отдельном коммите (LMS-правило, endpoint изменился).
7. **Phase integrity** — без TODO/заглушек; staged-набор каждого коммита ⊆
   scope tsk-372 (payment-файлы параллельного чипа tsk-010 исключены из
   pathspec-коммитов явно).
8-10. Не применимо (нет данных/классификаторов/date-time логики в изменении).
11. **Cross-project memory sync** — выполнено: `contracts/lms-api.md` +
    `CHANGELOG.md` обновлены и закоммичены в ContentBackbone (`ce3afe6`).
    `STATE.md` не тронут осознанно — фаза/версия не менялись (не milestone).
12. **Public API Contract Sync** — `docs/openapi.json` обновлён (пре-коммит
    хук); hardcoded prod URL не добавлялись; путей не переименовывали.

## Operator handoff
Категория А целиком: коммит+пуш (durable-авторизация), деплой на прод + живая
проверка в этой же сессии (по правилу LMS `~/.claude/CLAUDE.md`). Ручных шагов
для оператора не осталось.

## Улучшения без блокировки
- `WorkloadSummary` («На проверке») остаётся mandatory-only — сознательный
  выбор, задокументирован; если оператор захочет иначе — отдельная задача.
