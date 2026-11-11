# tsk-1489: terra без provider-28, резерв судьи sol → luna

**Scope.** Снять умерший маршрут `provider-28` с `openai/gpt-6.1-terra` (auto/stable), вывести подменённую `openai/gpt-6.1-sol` из цепочки судьи (код-дефолт + .env.example + боевой .env), обновить тесты.

**План.** Одобрен оператором 11.11 («Применить оба изменения»); исполнение — /executor-pro.

**Файлы.**
- `app/services/llm/providers.py` — `request_provider()`: terra → auto/stable; `_DEFAULT_JUDGE_MODELS = terra,luna`; история в комментарии.
- `tests/test_tsk1453_model_routes.py` — переименования тестов (pin → stable_route), luna без явного маршрута (None), дефолтные цепочки.
- `.env.example` — LLM_JUDGE_MODELS=terra,luna, комментарии.
- `scripts/tsk1489_probe_sol.py` — проба terra через auto/stable.
- Боевой `/opt/lms/.env` (вне git): `LLM_JUDGE_MODELS=openai/gpt-6.1-terra,openai/gpt-5.6-luna`, бэкап `/opt/lms/.env.bak-tsk1489-20261111-144500`, claim clm-1110, ledger `agents/ledger/1101-2026-11-11.md`.

**Валидация.**
- Регрессия: `pytest tests/test_tsk1453_model_routes.py test_tsk572_llm_client.py test_tsk1259_llm_identity.py test_tsk573_bakeoff_defaults.py test_tsk678_llm_chain_check.py test_tsk996_order_g6.py` → **113 passed**.
- Второе мнение (ADR-0006, claude_review sonnet, NEEDS_WORK): 3 находки (P2 имена тестов; P2 дефолт противоречил комментарию; P3 полнота зачистки provider-28) — все подтверждены сверкой с кодом, исправлены; фидбэк `claude_feedback` записан (3 confirmed / 0 rejected).
- Выкат: `deploy.sh` → `Deployed: 770687b`, /health 200.
- Живые проверки: среда процесса lms содержит новую цепочку; тестовый вызов hint_draft на проде → terra, 40 входных токенов, чистый ответ.
- Контрольная проба (второй час): terra 29–42 входных токена; luna 8–21 (смена сигнатуры id chatcmpl-… → resp_…, подмены нет).

**Rollback note.** Код: `git revert 770687b` + deploy.sh. Env: `cp /opt/lms/.env.bak-tsk1489-20261111-144500 /opt/lms/.env` + `systemctl restart lms`. Откатывать целиком не требуется: прежнее состояние (provider-28 мёртв, sol подменена) неработоспособно.

**Наблюдение.** luna сменила апстрим (id `resp_…`, 8–21 токен вместо 29–42) — страж identity.py `resp_` допускает; следующий еженедельный стенд подтвердит устойчивость.
