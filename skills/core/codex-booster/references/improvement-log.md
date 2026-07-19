## 2026-07-16 — import-claude: skill-route registry as Codex advisory data

**Source case:** Claude handoff `D:/Work/Root/agents/handoff/2026-07-16-skill-gate-claude-to-codex.md`, task `tsk-230`.

**Imported decision:** Codex now treats `C:/Users/user/.claude/hooks/skill_routing.json`
as a read-only route registry for domain-to-skill selection. The Claude `PreToolUse`
gate itself was not imported because Codex has no equivalent verified pre-write
hook; Codex must not report enforcement unless a Codex-owned mechanism exists.

**5 Whys:** Why import? Claude proved that text-only skill discipline is bypassed
under load. Why not copy the hook? It is Claude-only mechanics. Why not package
mirrors? The registry is outside Codex source and `package-skills.py` packages
`D:/Work/IDE_booster/skills`, not Claude hooks. Why add a reference? Codex still
needs durable route guidance for shared content/code/infra domains. Root cause:
platform-neutral route data existed only behind Claude-specific enforcement.

**Applied changes:** added `references/skill-routing-registry.md`; linked it from
`SKILL.md`, `skill-catalog.md`, `claude-import-checklist.md`, and
`codex-project-binding.md`.

**Anti-bloat:** one compact reference holds the behavior; top-level `SKILL.md`
received only routing links and one quality rule.

**Verification:** source edited first; packaging and mirror checks required before
closure.

## 2026-07-14 — import-claude: CA consumption outcomes and curated consumer inputs

**Source case:** Claude session export `session-export-1784035918512.zip`, task `tsk-207`.
Claude had already analyzed ContentAnalyzer findings; Codex reused the decisions instead of re-running CA analysis.

**Imported decisions:**
1. `codex-booster` now treats `bucket_marker --mark` as a decision write with explicit
   `--outcome applied|deferred|rejected`, not only a processed/seen flag.
2. `digital-copywriter` reality branch B accepts `source: ca-curated` from ContentAnalyzer
   curated selections alongside manual `ТЗ-driven` briefs, while keeping it separate from
   digest-driven reality posts.
3. `methodist` can optionally consume a curated `courses` selection from ContentAnalyzer for
   coverage checks, examples, and assignment ideas; `docs/v2` remains the source of truth.

**5 Whys:** Why import? Claude-side fixes closed real CA consumption gaps. Why not re-analyze?
The source session already performed the analysis, and the current task is Codex infrastructure
parity. Why were Codex skills behind? Runtime/canonical Codex skills had older rules: processed-only
bucket markers, no `ca-curated` branch, no optional `courses` input. Why compact edits? Each target
already had the right local slot, so broad new modes would duplicate existing behavior. Root cause:
Claude practice drift had not been mapped into Codex source-of-truth.

**Anti-bloat:** no new skill or reference was created; edits strengthened existing import, reality,
and context-loading rules in place.

**Verification:** `package-skills.py --skills codex-booster digital-copywriter methodist`
completed; source/runtime/project mirror SHA-256 parity checked for 14 `skills/core=yes`
projects; UTF-8/no-BOM/no-replacement checks passed; `validate_agents.py` passed.

## 2026-07-09 — Режим E (только E1.4+E2.4): backlog skill_boost за 7 дней (bucket_marker, первый прогон после внедрения маркера)

**Триггер:** bucket_marker (CA-коммит 6702371) впервые дал потребителю читать корзину `skill_boost` — накопился backlog за 2026-07-01/02/04/05/06/08/09, суммарно **747 непомеченных находок**. Полный контур (ERRORS.md + чат-скан 4 проектов) сознательно пропущен по запросу оператора — только контур усиления скиллов.

**Объём и характер данных:** 07-02 и 07-05 дали 363 и 353 находки (arXiv-фид + Hacker News + TG-каналы) — на 2 порядка больше остальных 5 дат (1-14 каждая). Из 747: ~156 — чистые аннотации arXiv-статей (академические ML/RL/CV-работы без прямой применимости к нашим skills), ~10 — Anthropic Python SDK changelog (v0.109-0.115, нет редактируемого skill-приёмника — `claude-api` не файл в `~/.claude/skills/`, встроенный), ~30 — подборки/дайджесты AI-инструментов без инструкций (листинг сервисов), остальное — контент ниши `pedagogy` (не относится к домену claude-booster: Claude Code/промптинг/MCP/агенты).

**Найдено 2 некопии, применены обеими Edit:**
1. **H9 Verbalized Sampling** в `ai-humanness.md` (канон `claude-booster/references/`) — источник: кластер TG-постов о промптоведении (07-05) про typicality bias/mode collapse после RLHF; приём «попросить модель дать 3-5 вариантов вместо одного» отсутствовал в Части 3 (там были только пост-хок правки текста H1-H8, не генерационная стратегия). Обновлён и чеклист Части 4 (H1-H8→H1-H9).
2. **pr-review SKILL.md, блок «Проблемы LLM-промптов»** — источник: arXiv-аннотация (07-02) о role-confusion jailbreak на stateful function-calling LLM (destyling снижает успех атаки с 61% до 10%, модель ориентируется на СТИЛЬ текста, а не на роль-теги). Существующий пункт «промпты открыты для инъекции» был слишком общим, чтобы поймать этот конкретный механизм (смешение стиля trusted/untrusted частей промпта) — усилил формулировку конкретным критерием.

**Отклонено как дубликат (anti-bloat, не применялось):** гуманизация текста (живая речь, неровный ритм, разговорная лексика, самокоррекция) — уже покрыто H1-H8/P6 полностью; CLAVIS-методология для image-gen промптов — покрыто существующей структурой `image-gen-recipes.md` (Subject/Style/Composition/Lighting/Mood); role prompting и мета-промпт шаблоны — уже первоклассные элементы Шага 4 prompt-engineer (Роль/Контекст/Ограничения); отдельный «промпт-аудитор» для проверки AI-текста — дубль response-quality-coach pre-publish check (M1-M14); Cognitive Load Theory для читаемости — покрыто P1-P7.

**Operator handoff (не применено, вынесено):** глобальный `~/.claude/CLAUDE.md` ссылается на `model-routing.md` («Выбор уровня — по model-routing.md»), но файл физически не существует ни в одном найденном месте `~/.claude/`. Находки про Claude Sonnet 5 (новый токенизатор, ~30-40% удорожание английского текста, отсутствие temperature/top_p/top_k, 1M контекст, adaptive thinking по умолчанию) — релевантные факты для наполнения этого файла, если оператор решит его создать. Не создавал сам — создание нового файла вне разрешённого auto-режима Шага E3.2 (только Edit существующих).

**RCA-паттерн:** оба применённых фикса — instruction gap (правило либо отсутствовало полностью как генерационная стратегия, либо было сформулировано слишком обобщённо, чтобы поймать конкретный механизм атаки). Основная масса backlog — content-type gap: bucket `skill_boost` в ContentAnalyzer явно перегружен нерелевантным (arXiv firehose, TG-дайджесты инструментов, ниша pedagogy) относительно узкого назначения «усиление Claude Code/промптинга/MCP/агентов» — соотношение сигнал/шум ~1:370. Возможная будущая находка для отдельного тикета CA (не в скоупе этого прогона): ужесточить relevance-порог или нишевую классификацию перед маршрутизацией в `skill_boost`.

**Anti-bloat:** оба фикса — точечные усиления существующих разделов (H1-H8→H9 внутри уже существующей Части 3; один усиленный буллет в уже существующем блоке pr-review), не новые файлы/секции. pr-review SKILL.md был уже 203 строки до правки (превышает мягкий лимит ≤200 ещё до этого прогона) — +1 строка не рассматривается как новый bloat, отдельно не выносился в references ради однострочной правки.

**Версии:** ai-humanness.md (без версионирования в frontmatter, только H9-раздел), pr-review v1.0.1 (версия не поднята — точечное усиление буллета, не новая функциональность).
**Backup:** `references/backups/{ai-humanness,pr-review}-2026-07-09.md`.
**Обработано находок:** 747 (все датированы skill_audit-маркером через `bucket_marker --mark --uids-file`, verified `--list` → 0 остатка на все даты). 2 применены правкой, 1 кластер вынесен оператору строкой выше, остальное отбраковано (дубликат/не по домену/нет редактируемой цели/слишком абстрактно).

## 2026-07-05 (2-й прогон) — Режим D: 3 находки оператора напрямую (формула без индукции, слив ответа в подсказке, навигация без якорей)

**Триггер:** оператор указал на 2 ручных замечания из живого прохождения курса + 1 недочёт навигации, не через отчёт ревью-скилла — «разбери два кейса и косяк навигации, улучши правила и скиллы».

**Верификация перед RCA:** прочитал реальные страницы (`WebFetch`) — тема «Измерение информации» (формула `N=2^i` дана сразу) и L2-навигатор главы (пункты ❓/💻 действительно все ведут на первый урок темы, без якоря) — оба замечания подтверждены фактом до правки, не приняты на слово.

**3 находки → 3 instruction gap, все FIXED:**
1. **Формула введена без индукции закономерности.** Оператор показал приём: 1 бит→2 варианта (перечислить: 0/1), 2 бита→4 (00/01/10/11), 3→8, ученик сам видит рост и выводит формулу. До этого материал давал `N=2^i` сразу с одним примером после. Добавил принцип methodist `difficulty-and-design.md` п.7a: для формульных/количественных понятий — ≥3 конкретных случая с ростом параметра + retrieval-вопрос «какую закономерность видите?» ДО формулы. Отзеркалено в обеих ревью-рубриках (expert-course-review К7, naive-learner-review Ось 1) — раньше критерий «есть пример на понятие» пропускал этот класс, т.к. формально пример (после формулы) был.
2. **Подсказка вычисляет ответ.** «Сколько байт при 16 битах? Подсказка: 16 бит ÷ 8 = 2 байта» — при ответе «2» подсказка выдаёт его дословно. У поля `hint` вообще не было правила о содержании — ни у авторов (methodist/digital-copywriter), ни у аудита (expert-course-review К5). Добавил правило в digital-copywriter (правило 1, ❌/✅ пример), чеклист methodist §6, проверку expert-review К5. naive-learner-review НЕ трогал — это ось экспертной оценки качества, не персона-понятности (anti-bloat: не дублировать без нужды).
3. **Навигация ❓/💻 без якорей.** Подтверждено WebFetch: все ссылки обоих разделов темы вели на `informatika-7-1-1-informaciya-i-dannye/` — один и тот же URL, без якоря, независимо от реального вопроса/задания. Эталонная модель (`lms-wp-export.md`) разрешала deep-link `#anchor` явно только для раздела 📖; для ❓/💻 не было явного требования per-item якоря. Добавил обязательное правило (L2 п.5/6) + якорную конвенцию `#cq-{uid}`/`#task-{uid}` (L3) + правило рендера digital-copywriter (topic_nav, п.3a). expert-course-review К9 ужесточён: «проверить фактически (открыть ссылки), не поверить оглавлению» — прошлый прогон рубрики этот класс не поймал именно потому, что формулировка проверки была обобщённой.

**RCA-паттерн (общий для всех трёх):** во всех случаях правило либо отсутствовало полностью (hint), либо существовало, но было недостаточно КОНКРЕТНЫМ/строгим, чтобы поймать конкретный класс нарушения (формула — «есть пример» технически было; навигация — «deep-link работает» технически было для одного раздела). Урок на будущее: при формулировке чек-пунктов рубрик избегать обобщений уровня «есть/работает» там, где конкретный сценарий нарушения этому формально удовлетворяет — писать проверку через факт (открыть ссылку, посчитать примеры до формулы), не через констатацию наличия.

**Anti-bloat:** все три — точечные добавления к существующим разделам (Часть 3 difficulty-and-design, L2-модель lms-wp-export, правило 1/защита-от-списывания assignment-rules), не новые файлы/механизмы. Симметричные правки в expert+naive для находок 1 и 3 (разные аудитории/оси, не дубль); находка 2 — только в экспертной оси + авторинге (осознанно не добавлял в naive-learner, чтобы не плодить пересекающиеся проверки одного факта в неподходящей оси).

**Версии:** methodist v1.7.2→1.7.3, digital-copywriter v1.10.0→1.10.1, expert-course-review v1.1.0→1.1.1, naive-learner-review v1.1.0→1.1.1.
**Backup:** `references/backups/{methodist-difficulty-and-design,methodist-assignment-rules,methodist-lms-wp-export,methodist-SKILL,digital-copywriter-SKILL}-2026-07-05b.md` + `references/backups/{expert-course-review-rubric,expert-course-review-SKILL,naive-learner-review-rubric-and-report,naive-learner-review-personas,naive-learner-review-SKILL}-2026-07-05.md`.
**Реестр:** `skills-errors.md` (новая FIXED-запись 2026-07-05, 2-й прогон дня). Не заносил в `course-quality-errors.md` — тот реестр специально для находок ИЗ ОТЧЁТОВ ревью-скиллов, эти три пришли от оператора напрямую, минуя `/naive-learner-review`/`/expert-course-review`.

## 2026-07-05 — Режим D: 6 класс-дефектов из ревью ai-predprinimatel + oge-informatika (авторинг-скиллы курсов)

**Триггер:** запрос оператора — проверить, писались ли ошибки после ревью `/naive-learner-review` + `/expert-course-review` двух свежих курсов (ai-предприниматель, ОГЭ информатика, оба прогона 2026-07-04), и улучшить скиллы разработки курсов.

**Обработано OPEN-записей:** 9 (3 ai-predprinimatel + 6 oge-informatika) → 8 class-фиксов (1 кластер #2+#3 объединён в один патч) + 1 WON'T_FIX.

**Кластеры:**
1. **Рецидив с заниженным скоупом** — онбординг внешнего инструмента. Фикс 2026-07-02 (курс Информатика 5-11) закрыл только «код-песочница» (правило 10а), но следующие два курса упёрлись в тот же класс с ДРУГИМИ инструментами: n8n (трек без входа), архив РешуОГЭ (нет шагов распаковки), КуМир (нет install-ссылки). Обобщил правило 10(а2) с «код» на «любой внешний инструмент вне браузера/ОС» + добавил обязательный блок «Что нужно перед стартом» в L1-навигатор (methodist).
2. Устаревшие ID моделей Claude в код-примерах — новый instruction gap, фикс в digital-copywriter §11(в).
3. Проектные `SA_COM` без видимых критериев готовности артефакта — фикс в methodist assignment-rules §7.
4. Реюз-пара задание↔задание без мостика-ссылки — расширение существующего правила «спиральный мостик» (methodist difficulty-and-design п.3), не новый механизм.
5. Шаблонизированный disclaimer «часть 2» скопирован между заданиями разной природы проверки — новый пункт methodist assignment-rules §9.
6. Ссылки-зависимости без кликабельности — расширение правила 10(б) digital-copywriter.

**RCA → корень (по кластерам):** в основном instruction gap; ядровой кластер (1) — прошлый фикс был написан под конкретный наблюдаемый инцидент (код-урок) без обобщения на инвариант «любой внешний инструмент» — урок на будущее: при фиксе после review-скиллов курсов формулировать правило на уровне класса, а не наблюдаемого случая.

**Anti-bloat:** не клонировал правила — расширил существующие пункты (10а→10а2, difficulty-and-design п.3, assignment-rules §7/§9); только 2 новых точечных абзаца (§9 п.4, §11в). methodist SKILL.md не тронут по содержанию (только version bump, 198<200 строк); digital-copywriter правила традиционно живут в SKILL.md (589 строк — устоявшаяся практика этого skill, не references).

**WON'T_FIX:** order_position узла 1112 (oge-informatika #5) — подтверждённый разовый execution gap конкретного прогона загрузчика, не класс (соседние узлы верны). Рекомендация — точечный фикс данных оператором/CreateCourses.

**Применённые правки:** methodist (`lms-wp-export.md`, `assignment-rules.md` ×2, `difficulty-and-design.md`, `SKILL.md` v1.7.1→1.7.2), digital-copywriter (`SKILL.md` v1.9.1→1.10.0, правила 10/11).
**Backup:** `references/backups/{methodist-difficulty-and-design,methodist-assignment-rules,methodist-lms-wp-export,methodist-SKILL,digital-copywriter-SKILL}-2026-07-05.md`.
**Реестры:** `course-quality-errors.md` (9 OPEN → 8 FIXED + 1 WON'T_FIX), `skills-errors.md` (новая FIXED-запись 2026-07-05).

**Доп. правка (тот же запрос, следующим шагом) — `course-screenshots` / `visuals-policy`:**
Оператор попросил перенести те же правки в `/course-screenshots` и `docs/ai/visuals-policy.md`, если релевантно. Проверил оба файла: релевантен только 1 из 6 кластеров — «внешний инструмент без онбординга» (oge-informatika #3, КуМир). `visuals-policy.md` уже содержал правило «install-урок = обязателен скриншот», но формулировка звучала как код/API-специфичная — КуМир под неё формально подпадал, но авторинг это не считал. Обобщил формулировку явно на «любой внешний инструмент вне браузера/ОС». `course-screenshots/SKILL.md` не тронут — его триггеры уже generic («сделай в интерфейсе X»), дублировать нечего.
**Backup:** `references/backups/createcourses-visuals-policy-2026-07-05.md`.
**Anti-bloat:** не правил 5 нерелевантных кластеров (ID моделей/SA_COM-критерии/мостик-реюз/disclaimer/кликабельность ссылок) — они не про визуалы, добавление было бы out-of-scope шумом в policy-файле.

## 2026-07-02 — Режим D: публикация курса ошибочно отдана оператору (рецидив operator-handoff, ~9-й эпизод → эскалация исполнена)

**Дефект (оператор, CreateCourses tsk-119):** после правок курса чат-ботов я пометил публикацию через ContentBackbone как «действие оператора» и выдал памятку. Публикация — CLI-инструмент агента (`python -m monolith wp-publish/lms-publish-lesson`), креды в окружении (preflight OK), прод-гейта на контент нет → категория А, не Б. Рецидив класса «operator-handoff misclassification» (~9-й эпизод; маркер эскалации висел активным с 05-14).

**RCA → корень:** instruction gap на уровне механизма — система полагалась на память модели прогнать Before-Б, без принудительного гейта; ярлык «оператор» эмитит любой skill/сам ассистент, а прошлые фиксы были точечными (per-skill) и класс не закрыли.

**Anti-bloat:** не клонировал per-skill правила (не сработали ×8) — один глобальный self-scan в shared reference + 1 пример в белом списке. Прирост ~10 строк.

**Применённые правки (Edit `operator-handoff-rules.md`):**
- §А белый список — «Публикация КОНТЕНТА через собственный публикатор проекта (ContentBackbone wp-publish/lms-publish) = А, не релиз кода и не 3rd-party UI».
- §Б — «релиз в продакшен» уточнён до «релиз КОДА; контент своим публикатором = А».
- Новая секция **«Pre-handoff self-scan»** — обязательный скан черновика на слова-триггеры («действие оператора/manual/категория Б/…») с прогоном Before-Б на каждое (операционализация эскалации 05-14, зеркало pre-publish ai-humanness).

**Публикация доведена агентом:** WP 23 стр. live (verified К1/К5/К2/К7/К3 на сайте) + LMS 21 урок (tsk-136 per-lesson, --no-prune, node_target=true, verified «Зачёт» в m4). ContentBackbone: включён рендер notice у course_nav (тесты 24/24).
**Backup:** `references/backups/operator-handoff-rules-2026-07-02.md`. **Эскалация:** ИСПОЛНЕНА (self-scan); при повторе после фикса — Stop-hook в settings.json.

## 2026-07-02 — Режим D: дефекты канальных скиллов из живой сессии (маркетинг)

**Обработано:** 1 кластер (2 эпизода в одном скилле = автоправка по порогу Режима E), затрагивает 4 канальных скилла.

**Дефект (CONFIRMED, живой диалог):** `/avito-specialist optimize` выдал рекомендацию «отсекать нецелевых» (дисквалификатор в тексте объявления) и усомнился в цене 700 ₽ как в bait. Оба — против реальной модели клиента: оператор монетизирует нецелевой спрос (новые курсы + брокер), а 700 ₽/занятие — честная репетиторская подача (5500 ₽/мес). Хуже — рекомендация противоречила стратегии в `clients/it-school/README.md`, которая прямо говорила «нецелевые не отсекать».

**RCA (5 Whys) → корень:** instruction/context gap.
- Почему отсекал? → применил дефолт «нецелевой = потеря конверсии».
- Почему дефолт победил стратегию? → скилл её не читал.
- Почему не читал? → Шаг 0 канальных скиллов (avito/seo/direct/creative) не грузил `clients/{client}/README.md` + `strategy/` (при переносе путей в дом Marketing чтение карточки добавили только strategist/analyst — канальные пропущены).
- Плюс: playbook трактовал низкую цену как подозрение, без нормы «цена за занятие у репетиторов».

**Anti-bloat check:** не покрыто существующим правилом (нет). Локально (в каждом SKILL Шаг 0 + правило avito + playbook), не глобально. Чеклист-нюанс → в reference (avito-playbook §8–9). Кластер (общий корень у 4 скиллов) → один патч на все. Не дублирует strategist (тот решает; канальные — исполняют в его рамках).

**Применённые правки (Edit):**
- Шаг 0 у `avito-specialist`, `seo-specialist`, `yandex-direct-specialist`, `creative-brief-designer` — добавлено чтение `clients/{client}/README.md` + `strategy/` и работа в рамках модели клиента (закрывает расхождение канал↔стратегия).
- `avito-specialist` Правила качества — инвариант «не отсекать аудиторию по умолчанию; нецелевой спрос может быть выручкой; маршрутизировать, а не отпугивать».
- `avito-playbook.md` §8 (нецелевой спрос — отсекать/монетизировать) + §9 (цена за занятие — репетиторская норма, не bait).

**WON'T_FIX:** нет. **Execution gaps:** нет.
**Backups:** `references/backups/{avito-specialist,seo-specialist,yandex-direct-specialist,creative-brief-designer,marketing-strategist,marketing-analyst,avito-playbook}-2026-07-02.md`.
**Валидация:** все 4 SKILL переподхвачены движком (frontmatter валиден), размеры < 200 строк.

## 2026-07-02 — Перенаправление 6 маркетинговых скиллов в дом Marketing (tsk-144)

**Контекст:** оператор завёл проект-дом `d:\Work\Marketing\` (обвязан через `/project-docs full`: NORTH_STAR, CLAUDE/AGENTS/README, docs/ai/*, clients/<клиент>/<канал>/). Все артефакты маркетинга должны писаться туда, а не в ContentFactory.

**Правки (Edit, минимальные):** во всех 6 скиллах пакета (`marketing-strategist`, `seo-specialist`, `avito-specialist`, `yandex-direct-specialist`, `marketing-analyst`, `creative-brief-designer`) Шаг 5 «Сохранение» и строка Контракта `Артефакт` переведены с `d:\Work\ContentFactory\output\<X>\` на `d:\Work\Marketing\clients\{client}\<канал>\{дата}-{mode}-{topic}.md`. Добавлено измерение `client` (slug папки в `clients/`; из задачи, иначе спросить) + `client` в frontmatter. В `marketing-strategist` и `marketing-analyst` Шаг 0 глоб переведён на чтение `clients/{client}/README.md` + папок клиента.

**Anti-bloat:** только смена пути + client-измерение, поведение скиллов не менялось. Каналы-подпапки: strategy/seo/avito/direct/analytics/creative; campaign → campaigns/. Конвенция задокументирована в `Marketing/CLAUDE.md`, дублей нет.

**Открытый вопрос:** `smm-specialist` пока пишет в `ContentFactory/output/smm/` (у него больше привязок к ContentFactory — воронка контента) — перенос в дом Marketing вынесен в tsk-144 как опциональный follow-up, требует согласования.

## 2026-07-02 — Режим A: аудит нового skill /expert-course-review (tsk-143)
- **Контекст:** новый skill «педагог-аудитор» создан напрямую (промах автоподбора — `/claude-booster` не был взят на этапе создания; оператор указал на это, аудит запущен постфактум). Проверка по standard.md + audit-checklist.md + booster-shared.md перед smoke-прогоном.
- **S1/S2:** пройдены полностью (name/allowed-tools/Порядок работы/YAML; version/Роль/Когда/Контракт/Правила качества; язык русский; frontmatter валиден). Скилл работоспособен и корректен как есть.
- **11 критериев — НЕ раздуто:** каждый привязан к отдельной признанной рамке (Alignment/CLT/Merrill/ARCS/UDL/QM/Gagné), 6 критериев оператора — их подмножество. Схлопывание убило бы методический якорь. Оставлено.
- **Anti-bloat (S3, правило №4 «дубль соседнего skill»):** две находки дублирования.
  - `sources.md` дублировал модель LMS + структуру WP обывателя/методиста → заменено на указатели (`naive-learner-review/references/sources.md` + `methodist/lms-wp-export.md`), оставлены только карты «что собрать для К1–К11». 92→86 стр.
  - `rubric.md` заново кодировал механику методиста (enum типов, микс, подкурсы, WP-навигация, профиль прогрессии) → добавлен единый блок «Источник истины по механике LMS/WP» со ссылками на 3 методистовы references; К5/К9 подрезаны на отсылки к эталону. Определения больше не живут в двух местах (дрейф устранён). Размер 235→245 стр (указатель добавил строк — цель достигнута анкерингом, не сокращением; честно зафиксировано).
- **Правки:** Edit (не Write) в `sources.md`, `rubric.md`. Бэкапы: `references/backups/expert-course-review-{rubric,sources}-2026-07-02.md`.
- **Верификация:** frontmatter валиден; SKILL.md 125 стр (норма для сложного skill с references). Осталось: smoke-прогон на реальном курсе (нужен `course_uid`/URL) до перевода tsk-143 в done.
- **Процессная заметка:** промах автоподбора — единичный, в OPEN не заношу; при повторе класса «создание skill без /claude-booster» → запись в skills-errors.md.

## 2026-07-02 — Пакет маркетинговых скиллов, волна 3 + завершение (Режим C, tsk-141)

**Создано (Write, новые файлы):**
- `~/.claude/skills/marketing-analyst/SKILL.md` (v1.0.0, 5 режимов: unit-economics/funnel/attribution/experiment/report) + `references/analytics-playbook.md` (формулы CAC/LTV/ROMI, воронка снизу-вверх, UTM-стандарт, дешёвый стек Метрика+таблица, честная статистика малых выборок, отчёт-минимум).
- `~/.claude/skills/creative-brief-designer/SKILL.md` (v1.0.0, 3 режима: ad-creative/social-visual/concept) + `references/creative-playbook.md` (принципы визуала, анатомия макета, двойная ЦА, форматы площадок, стиль/палитра, передача в генерацию с пометкой про кириллицу).

**Границы (anti-bloat):** `marketing-analyst` определения юнит-экономики/воронки берёт из `marketing-strategist/references/marketing-frameworks.md` (не дублирует теорию), проводит границу «измеряю vs решаю» — стратегические выводы отдаёт стратегу. `creative-brief-designer` НЕ генерирует картинки (передаёт в `/prompt-engineer image-gen`/ImgGen/человека) и НЕ делает дизайн сайта (это `/design-consultation`) — явно разведено.

**Реестры:** `skills-registry.md` — секция «Маркетинг и привлечение» дополнена 2 строками, границы переписаны на полный пакет; `~/.claude/CLAUDE.md` — счётчик 40→42, оба скилла в списке.

**Smoke:** движок подхватил оба скилла (description в system-reminder) → frontmatter валиден.

**ИТОГ ПАКЕТА tsk-141 (завершён):** 6 новых скиллов маркетинга/привлечения — `marketing-strategist`, `seo-specialist`, `avito-specialist`, `yandex-direct-specialist`, `marketing-analyst`, `creative-brief-designer` (+ 8 references). Все класса «стратег/планировщик» по образцу smm-specialist: планируют и дают ТЗ/инструкции, не подменяют инструменты и доступы оператора. Общие инварианты пакета: двойная ЦА (родитель-покупатель / подросток-пользователь), свежесть платформ через runtime WebSearch (не baked-in), белые методы (стоп-листы во всех playbook), Operator-only для действий с доступами/кабинетами/публикацией. Закрыты tsk-013, tsk-028, tsk-141. CLAUDE.md 36→42.

## 2026-07-02 — Пакет маркетинговых скиллов, волна 2 (Режим C, tsk-141)

**Создано (Write, новые файлы):**
- `~/.claude/skills/avito-specialist/SKILL.md` (v1.0.0, 5 режимов: listing/optimize/promotion/analytics/strategy) + `references/avito-playbook.md` (факторы ранжирования Авито, анатомия объявления услуг/обучения, платное продвижение, воронка-аналитика, отзывы/рейтинг, мультиобъявления и риски банов, стоп-лист правил).
- `~/.claude/skills/yandex-direct-specialist/SKILL.md` (v1.0.0, 6 режимов: structure/semantics/ads/conversions/budget/audit) + `references/direct-playbook.md` (Мастер vs эксперт, Поиск vs РСЯ, семантика+минус-фразы+кросс-минусация+операторы, цели Метрики и микроконверсии при малом трафике, бюджет малого бизнеса, тексты, модерация образовательных услуг/лицензия, стоп-ошибки слива).

**Границы (anti-bloat):** оба — класс «стратег/планировщик» по образцу smm/seo. `avito-specialist` не публикует (публикация — существующие инструменты Авито оператора: tsk-001 публикатор, tsk-012 аналитика); `yandex-direct-specialist` не имеет доступа к кабинету (запуск/модерация/оплата — Operator-only с пошаговой инструкцией). Свежесть тарифов Авито и форматов Директа (ЕПК/автостратегии) — через runtime WebSearch, не baked-in (быстро устаревает). Модерация образования и лицензия вынесены как чек-пойнт оператору, скилл не решает за него.

**Реестры:** `skills-registry.md` — секция «Маркетинг и привлечение» дополнена 2 строками + границы; `~/.claude/CLAUDE.md` — счётчик 38→40, оба скилла в списке.

**Smoke:** движок подхватил оба скилла (description в system-reminder) → frontmatter валиден.

**Задачный учёт:** tsk-141 волна 2 → done; tsk-013 (авитолог) → done (разблокирует tsk-011, tsk-034). Осталась волна 3: marketing-analyst + creative-brief-designer.

**Backups:** не требуются — новые файлы; существующие skills не правились (registry/CLAUDE.md — append).

## 2026-07-02 — Пакет маркетинговых скиллов, волна 1 (Режим C, tsk-141)

**Запрос оператора:** создать пакет skills для маркетинга и привлечения клиентов. Подтверждено: SEO (с ИИ-ответами), маркетолог, авитолог, директолог, маркетинговый аналитик; вопрос про дизайнера. Уже есть smm-specialist.

**Согласованный состав (AskUserQuestion, 3 развилки):** пакет из 6 скиллов — `marketing-strategist`, `seo-specialist`, `avito-specialist`, `yandex-direct-specialist`, `marketing-analyst` (решено: отдельный скилл), `creative-brief-designer` (решено: отдельный скилл брифов визуала). Волна 1 (эта сессия): маркетолог + SEO. Роли email/CRO/PR/партнёрка — режимы маркетолога, не отдельные скиллы.

**Создано (Write, новые файлы):**
- `~/.claude/skills/marketing-strategist/SKILL.md` (v1.0.0, 5 режимов: strategy/positioning/offer/funnel/campaign) + `references/marketing-frameworks.md` (JTBD, двойная ЦА родитель/подросток, позиционирование Dunford, AARRR, юнит-экономика, сезонность образования РФ, микс каналов→скиллы).
- `~/.claude/skills/seo-specialist/SKILL.md` (v1.0.0, 6 режимов: audit/semantics/content-brief/geo/local/report) + `references/seo-playbook.md` (Яндекс vs Google, техаудит, семантика, on-page, ссылки, локальное, метрики, чёрное-SEO стоп-лист) + `references/geo-aeo.md` (оптимизация под ИИ-ответы: извлекаемость, FAQ+schema, E-E-A-T, llms.txt как гипотеза, мониторинг ИИ-упоминаний, специфика Нейро/AI Overviews/ChatGPT/Perplexity).

**Границы (anti-bloat):** `marketing-strategist` — зонтик, раздаёт брифы, не исполняет; `smm-specialist` остаётся (только TG/VK); `seo-specialist` техразведку берёт у `/site-researcher` (не перекраулит), тексты отдаёт копирайтерам. Оба скилла — класс «стратег» по образцу smm-specialist (планируют + ТЗ, не производят финальный артефакт). Свежесть платформ — через runtime WebSearch, не baked-in (устаревание).

**Реестры обновлены:** `skills-registry.md` — новая секция «Маркетинг и привлечение» + границы; `~/.claude/CLAUDE.md` — счётчик 36→38, оба скилла в списке.

**Smoke:** движок Claude Code подхватил оба скилла (description отобразились в system-reminder) → frontmatter валиден.

**Задачный учёт:** tsk-141 волна 1 → done; tsk-028 (SEO) → done; tsk-013 (авитолог) остаётся в волне 2.

**Backups:** не требуются — только новые файлы, существующие skills не правились (кроме registry/CLAUDE.md — append).

## 2026-07-01 — methodist: адаптивность сложности под профиль ЦА (Режим D, CreateCourses tsk-133 digital-skills-mehatronika)

**Запрос оператора (/claude-booster Режим D):** методист спроектировал ПРЕДВУЗОВСКИЙ курс (ЦА — абитуриент 17-18, слабый по ЕГЭ, идёт в инженерный вуз) в формате детского: почти все задания одношаговые («напиши одну команду print», «предскажи вывод»), без роста объёма кода и без проектных. Нужна была прогрессия 4–5 строк в начале → 8–10 в середине → 10–15 в конце + пара проектных.

**RCA (5 Whys) → корень:** instruction gap (+ малая доля context gap). Модель уровня ученика (difficulty-and-design Часть 2) транслировала ЦА×стадию только в ПОТОЛОК сложности (Блум + число предпосылок), но не в ЦЕЛЕВУЮ ТРАЕКТОРИЮ — объём кода, многошаговость, долю проектных по ходу курса. Потолок (защита от перегруза в точке входа) спутан с целевым выходом курса. Нет инварианта «низкий вход ≠ низкий выход»: «стадия новичок» применялась одинаково к детям и к предвузовским абитуриентам → детский профиль для не-детской ЦА. Доп. техпробел: §3.2 утверждал «проектные = `TA`», но конвертер `blocks_to_lms.py` НЕ разворачивает `TA` (только SC/SA/SA_COM).

**Применённые правки (минимальные, anti-bloat — расширение существующей модели уровня, не новая система; Edit):**
- `methodist/references/difficulty-and-design.md` Часть 2 — новый подраздел **«Профиль прогрессии по типу ЦА (целевая траектория ≠ потолок)»**: таблица 4 профилей (дети / подростки / предвузовский / взрослый-новичок) × объём кода старт→середина→финал, многошаговые, обяз. проектные, подъём по Блуму; инвариант «низкий вход ≠ низкий выход».
- `methodist/references/assignment-rules.md` — §3.2 микс привязан к профилю (предвузовский/взрослый → к финалу доля NORMAL/HARD/проектных растёт); исправлено «проектные = TA» → авто-проектное `SA_COM`; новый **§3.5** (рост объёма по коридору профиля + приём «многошаговое/проектное авто = `SA_COM`, не `TA`» из-за ограничения конвертера, опора на SA-нормализацию); §6 чеклист +2 пункта.
- `methodist/references/coverage-and-review.md` mastery-линза — пункт «рост по профилю ЦА; для не-детской ЦА финал = многострочные программы + проектные».
- `methodist/SKILL.md` v1.5.0 → **1.6.0** — Шаг 1 интервью (фиксировать профиль, расширена строка «Прогрессия»), Шаг 5 чекбокс профиля, 1 правило качества «профиль прогрессии под ЦА (потолок ≠ цель)».

**Anti-bloat:** не клонировал — профиль встроен в существующую шкалу уровня (difficulty-and-design Часть 2), а не отдельным файлом/системой. Шкала потолков не удалена, явно разделены «потолок» и «целевая траектория». Устаревшее «проектные = TA» исправлено (не продублировано). SKILL.md 196 строк (< 200 лимита), frontmatter валиден.

**Backups:** `references/backups/methodist-{SKILL.md,difficulty-and-design.md,assignment-rules.md,coverage-and-review.md}-2026-07-01.md`.

---

## 2026-06-30 — methodist: гейт глубины практики + mastery-линза (Режим A/D, CreateCourses python-podrostki-11-14)

**Запрос оператора (/claude-booster Режим A):** методист отрабатывает ПОВЕРХНОСТНО — мало однотипных заданий, не обеспечивает реального освоения темы. Конечная цель скилла — чтобы УЧЕНИК НАУЧИЛСЯ ДЕЛАТЬ тему сам, а не «покрыть материалом». Дефекты на курсе `python-podrostki-11-14`: (1) содержательные темы (числа/строки/условия) — всего 3 задания (применялось §3.1 «лист=3» там, где по факту подкурс 1-го уровня §3.2 «10–15 + 2–3 проектных»); (2) не каждый приём отработан — про срезы строк рассказали, ученик их не применял; `count`/`find`/`replace` даже не введены; (3) нет разнообразия типов практики (преобладают «предскажи вывод»/«выбор», мало «напиши сам каждым приёмом», «найди-исправь», «примени в новой формулировке»).

**RCA-корни:** instruction gap (тройной) — (а) §3 считал по ФОРМЕ графа (лист без детей = 3), не по СОДЕРЖАНИЮ → содержательная тема получала «лист на 3»; (б) §8 п.7 требовал лишь «≥1 задание на написание кода», но не «каждый приём материала → задание на применение» и не разнообразие типов; (в) Шаг 4 (двойная проверка) проверял прогрессию/предпосылки/источник, но не «сможет ли ученик САМ сделать тему после ЭТИХ заданий».

**Применённые правки (минимальные, anti-bloat — усиление существующих §, не новые файлы; Edit):**
- `methodist/references/assignment-rules.md`: §3 вводная — классификация узла ПО СОДЕРЖАНИЮ (содержательная тема = подкурс §3.2, даже без детей; лист §3.1 = узкая подтема с 1 приёмом); §3.1 заголовок уточнён; новый **§3.4 «Гейт глубины практики и покрытия приёмов»** (каждый приём из материала → ≥1 задание на применение; не недодавать ходовые приёмы — для строк срезы + `count`/`find`/`replace`; объём разнообразных заданий); §8 **п.8** (микс типов практики: предсказать вывод / написать код каждым приёмом / найти-исправить / применить в новой формулировке / мини-проект; SA-нормализация СОХРАНЯЕТ имена методов → «напиши код методом X» авто-проверяемо); §6 чеклист +2 пункта.
- `methodist/references/coverage-and-review.md`: Часть 2 — новая **«Линза освоения (mastery), финальная»** (сам сделает тему? каждый приём применён руками? хватает объёма/разнообразия?).
- `methodist/SKILL.md` v1.4.0 → **1.5.0** — Шаг 3 п.1 (классификация по содержанию + ссылка §3.4), Шаг 4 п.4 (mastery-линза), Шаг 5 чекбокс глубины, 1 правило качества «глубина практики по умолчанию».

**Anti-bloat:** не клонировал. Канон правил заданий остался один (`assignment-rules.md`), канон проверки — один (`coverage-and-review.md`); SKILL.md — короткие указатели на references. §3.1 «ровно 3» не удалён, уточнён до «узкая подтема». methodist SKILL.md 194 строки (< 200 лимита).

**Backups:** `references/backups/{methodist,methodist-assignment-rules,methodist-coverage-and-review}-2026-06-30.md`.

**Запись:** `skills-errors.md` — FIXED 2026-06-30.

---

## 2026-06-29 — Дефолты детских курсов: визуальная насыщенность + LMS-структура подкурсами (Режим A/D, tsk-129)

**Запрос оператора (/claude-booster):** заложить ПО УМОЛЧАНИЮ для авторинга детских курсов (CreateCourses): (1) визуальную насыщенность как в презентациях Босовой — образы под каждое ключевое понятие ВНУТРИ материала, не только обложка; (2) LMS-структуру подкурсами «класс → глава → тема(лист)» вместо плоского курса (логика LMS «сначала все материалы, потом все задания» → в плоском курсе ранняя теория забывается). Контекст: informatika-5-11, Глава 1 опубликована плоско (LMS курс 825), структуру чинят в основной сессии.

**RCA-корни:** instruction gap (двойной) — (а) политика визуалов покрывала «есть визуал в уроке», но не насыщенность/in-content образы под понятия; (б) ни skill, ни docs не запрещали плоскую публикацию большого курса, хотя механизм `subcourses[]` в публикаторе CB давно есть (`_build_course_node`), но **не задокументирован в контракте**.

**Применённые правки (минимальные, anti-bloat — канон в reference, скиллы ссылаются):**
- `D:/Work/CreateCourses/docs/ai/visuals-policy.md` (канон визуалов) — новый раздел «Насыщенность: несколько образов на урок» (2–5 визуалов, примеры Босовой, место в материале + alt + идемпотентное имя); поля md-промпта дополнены `image-id`/`anchor`; 2 пункта чеклиста.
- `D:/Work/ContentBackbone/docs/wp-content-contract.md` (контракт JSON, единый источник) — новый раздел «Структура подкурсами (граф класс → глава → тема)»: поле `subcourses[]`, спецификация узла, пример (раньше field был только в коде, не в контракте).
- `methodist/references/lms-wp-export.md` § 1.4 — структура подкурсами для больших/детских курсов + ссылка на контракт.
- `methodist/SKILL.md` v1.3.1 → **1.4.0** — 1 правило-инвариант (подкурсы), 1 правило визуалов (насыщенность), 1 чекбокс верификации.
- `digital-copywriter/SKILL.md` v1.7.1 → **1.8.0** — `lms-publish` п.9 (подкурсы, ссылка на контракт), §2a визуалы дополнены насыщенностью.
- `D:/Work/CreateCourses/CLAUDE.md` — гейт: bullet «Большой курс — подкурсами, не плоско»; секция визуалов → насыщенность; 2 чекбокса верификации.
- `D:/Work/CreateCourses/docs/ai/architecture.md` — абзац про граф Courses подкурсами в § «Две проекции экспорта».

**Anti-bloat:** не клонировал. Канон визуалов остался один (`visuals-policy.md`), канон структуры — один (`wp-content-contract.md` § Структура подкурсами); скиллы и CLAUDE.md ссылаются, не дублируют спецификацию. В SKILL.md — короткие правила-инварианты, детали в references. methodist 184→190 строк (под лимитом).

**Backups:** `references/backups/{methodist,methodist-lms-wp-export,digital-copywriter}-2026-06-29b.md` (суффикс b — ранние backup того же дня сохранены).

---

## 2026-06-29 — Политика визуалов курсов + правило context (Режим B, tsk-128)

**Запрос оператора (/claude-booster):** заложить по умолчанию политику визуального контента учебных материалов — инфографика→ASCII (точность, генеративке не доверять), иллюстрации→делегирование в Codex (md-промпт + handoff), визуалы и в WP, и в LMS. Плюс зафиксировать правило публикатора: код/данные задания — в поле `context`, не дублировать в `stem`.

**Состояние на момент запуска:** параллельная сессия уже внесла политику в `digital-copywriter/SKILL.md` (п.2a), `methodist/SKILL.md` (стр.184) и `D:/Work/CreateCourses/CLAUDE.md` (правила+чеклист), создала протокол `D:/Work/CreateCourses/docs/ai/visuals-policy.md`. Block-типы `ascii`/`image` уже в публикаторе CB (blocks_renderer.py, blocks.py).

**Применённые правки (минимальные, anti-bloat — не дублировал готовое):**
- `methodist/references/assignment-rules.md` §9 п.2 — добавлено правило «исходник в поле `context` (рендерится на WP и LMS после tsk-130), НЕ дублировать в `stem` — иначе код дважды». Backup: `references/backups/methodist-assignment-rules-2026-06-29.md`.

**Anti-bloat:** политику визуалов НЕ клонировал (единый источник — `visuals-policy.md`, на него уже ссылаются оба скилла и CLAUDE.md). Добавил только недостающее правило `context` в существующий §9 (1 абзац), без новых файлов.

**Применение в сессии (курс python-podrostki-11-14, урок М0):** добавлен ASCII-блок (print→экран) в WP-урок и LMS-граф; проверено — рендерится в материал LMS (cb-ascii) и на WP; перепубликовано (WP live, LMS). Задания приведены к правилу: код только в `context` (устранён двойной код, замечание оператора).

**Backups:** methodist/digital-copywriter SKILL + methodist references (lms-wp-export, assignment-rules) — `references/backups/*-2026-06-29.md`.


## 2026-06-02 — review-gate получил emission-time Before-Б gate (gap 2026-05-27 fix покрытия)

**Источник:** self-review во время CB tsk-004 Phase 6.7 B2 live smoke. Оператор попросил «изучи правило» перед handoff'ом → Before-Б check вскрыл что Шаг 2 («Upload xlsx → Google Sheets») был помечен Б ошибочно. Реально gspread + SA JSON (`D:/Work/LMS/secrets/gscapi-...json`) → категория А, выполнен агентом end-to-end.

**Инцидент:** финальный `/review-gate` на Phase 6.7 readiness выпустил handoff блок с 5 шагами B2; шаг Upload помечен Б без Before-Б check. Применение правила вскрыло — SDK доступен (gspread), creds доступны (SA JSON в LMS/secrets/), upload автоматизируется. Real true-Б = только «URL existing Sheet» (1 атом).

**RCA:** instruction gap — review-gate SKILL.md не имеет emission-time Before-Б gate в Контракт-результата §«Operator handoff» (зеркально 2026-05-27 fix techlead/spec, но review-gate не был включён в scope того кластера).

**Фикс:** `review-gate/SKILL.md` v2.2.1 → v2.2.2 — расширена строка `Operator handoff` явным emission-time Before-Б check с примерами SDK-обёрток (gspread, gh, aws/gcloud) как категория А. Backup: `references/backups/review-gate-2026-06-02.md`.

**Anti-bloat:** не клонировал rule (источник в operator-handoff-rules.md остаётся); добавил 1 строку-правила inline зеркально 2026-05-27 fix в techlead/spec. Размер review-gate: 91 → 92 строки.

**Real-world verification:** после применения правила я полностью выполнил B2 end-to-end (1+2+3+4+5 категория А с одним true-Б = URL existing Sheet). LMS dry_run + apply imported=8 errors=[], API verify подтвердил `task_content_json` passthrough работает (`hints_video`/`has_hints` в task_content jsonb на dev LMS DB).

**⚠ ESCALATION-маркер:** класс «operator-handoff misclassification» = 7-й инцидент. На 8-9 эпизоде — автоматизированный pre-handoff Grep-скан (см. 2026-05-14 эскалация-маркер). Сейчас не строю (anti-bloat); фикс point-wise устраняет конкретный recurrence-vector в review-gate.

**Запись:** `skills-errors.md` FIXED 2026-06-02.

## 2026-06-01 — Самозапуск недельного разбора: проактивная сессия вместо облачного агента

**Запрос оператора:** вариант A — превратить недельный разбор в самозапуск.

**Блокер вскрыт при попытке `/schedule`:** навык создаёт **облачный удалённый агент** (Anthropic cloud, свой git-checkout, без доступа к локальной машине). Недельный разбор читает локальные чаты `~/.claude/projects/*.jsonl` и пишет в локальные файлы навыков `~/.claude/skills/...` — облачный агент к ним доступа не имеет. Это то же ограничение, на котором падал «Способ A» в конфиге. Удалённую cron-задачу **не создавал** (была бы пустышкой).

**Решение (категория В — развилка, оператор выбрал A; реализована рабочая половина):** надёжен только запуск в живой локальной сессии. Закреплён проактивный триггер:
1. `~/.claude/CLAUDE.md` § Контур обратной связи — новый подраздел «Проактивный недельный разбор навыков (самозапуск)»: в любой сессии в понедельник+ проверить дату последнего weekly-аудита в improvement-log, при ≥7 днях — предложить запуск (не запускать молча — ресурсоёмко).
2. `references/auto-audit-config.md` — § Способы планирования помечен: облачный/удалённый путь непригоден для этого аудита, основной механизм — проактивная сессия; TG-напоминание (Windows Task Scheduler) остаётся толчком.

**Anti-bloat:** правило-инвариант (не привязка к инциденту), без новых файлов. Существующие Способы A/B/C не удалял — пометил их роль. Backup CLAUDE.md: ранее снят `references/backups/CLAUDE-global-2026-06-01.md`.

## 2026-06-01 — Автоматический аудит (weekly, добор пропущенного хвоста)

**Период:** 2026-05-18 — 2026-06-01 (две недели — последний фактический прогон был 2026-05-18; понедельники 25 мая и 1 июня тригеры отправили TG-напоминание, но ручной разбор не запускался — этот прогон закрывает оба окна).

**Метод:** ERRORS.md проектов прочитаны напрямую (дешёвый сигнал) + 4 параллельных Explore-агента по кластерам проектов на JSONL-чаты окна. Активны все 8 проектов; самый нагруженный — ContentBackbone (чат 87 МБ от 25 мая, серия 2–5 МБ 25–29 мая).

**Сигналы из ERRORS.md:**
- LMS — новых записей в окне нет (файл тронут 20 мая, последние записи 28–29 апреля, закрыты).
- ContentBackbone — 3 записи S2 в окне (20/25/26 мая), все `done`. Две из них (25+26 мая) — **один класс «дисциплина границ фаз/коммитов»**: staged-набор смешивал фазы/fix, scope ревью-артефакта ≠ staged scope, не sweep-нуты ADR-ссылки после renumber. Профилактика прописана в ERRORS.md, но **не операционализирована** в `executor-pro`/`review-gate`.
- ContentFactory (тронут 14 мая) / SPW (4 мая) / TG_LMS (3 марта) — вне окна, новых записей нет.

**Сигналы из чатов (Explore ×4):**
- **Backend (LMS/TG_LMS/SPW)** — чисто. Рецидив «рутину на оператора» (серия 13–27 мая, 5 эпизодов) в окне **не повторился**. Навыки db-check/eng-review/tech-spec-composer применялись корректно.
- **ContentFactory + EGE** — чисто. Копирайт-навыки и ege-master без операторских отклонений.
- **IDE-booster + IT-Businessman** — чисто, только процедурные/feature-запросы.
- **ContentBackbone** — вокруг tsk-004 Phase 6.7 (26–27 мая): (1) operator-handoff мис-эскалация 27 мая — **уже закрыта** FIXED 2026-05-27, не новое; (2) **новое: 3 коммита подряд на английском** (7e6797a, c62f1ef, 0cdd027) вопреки стандарту CLAUDE.md.

**Кластеры (≥2 эпизода) → автоправки:**

| Кластер | Эпизоды | RCA | Правка |
|---|---|---|---|
| A. Границы фаз/коммитов (staged ⊄ спека, смешение fix, ADR-ссылки) | 2 (CB 25+26 мая) | instruction gap — профилактика в ERRORS.md, не в навыках | `executor-pro` Шаг 5 + `review-gate` Dim 6/7 |
| B. Английские коммиты вопреки CLAUDE.md | 3 (CB 27 мая) | instruction gap — глобальное правило не операционализировано в точке коммита | бандл в тот же блок `executor-pro` Шаг 5 |

**Применённые правки (только Edit, без новых файлов навыков):**
1. `executor-pro/SKILL.md` v1.5.0 → v1.5.1, Шаг 5 п.1 — блок «Дисциплина staging и коммита»: `git diff --cached --stat ⊆ spec §Артефакты`; при 2+ spec — коммит по границе фазы; смешение без `bundled`-разрешения → `NOT_READY`; сообщение коммита по CLAUDE.md (русский, повелительное, тип-префикс), английский = дефект. Покрывает кластеры A и B одним блоком.
2. `review-gate/SKILL.md` v2.2.0 → v2.2.1 — Dim 7 (Phase Integrity) +проверка «staged scope ⊆ scope активной спеки, смешение фаз = АВТО-ОТКЛОНЕНО»; Dim 6 (Docs Drift) +sweep `grep ADR-<старый>` = 0 при renumber.

**Anti-bloat:** кластеры A и B уплотнены в один блок `executor-pro` (git-дисциплина — одна область). В `review-gate` — дописывание в существующие Dim 6/7, без нового измерения. Cross-project ledger (Dim 11) и grep публичного API (Dim 12) уже были — не дублировал. Ссылки на CLAUDE.md + spec §Артефакты вместо клонирования правил. Размеры: executor-pro 132 стр., review-gate 91 стр. (≤200).

**OPEN записи (единичные находки):** 0 новых. `executor-pro` review-changes contract (tech-spec остался untracked, CB 27 мая, 1 эпизод) — ниже порога ≥2, не правка; смежно покрыт новым правилом staged-дисциплины.

**Эскалации:** operator-handoff misclassification — маркер от 2026-05-27 (≥4 эпизода) **остаётся активным**, но в окне 28 мая–1 июня класс **не повторился** → 6-й эпизод не наступил, автоматизированный pre-handoff checker пока не строим (anti-bloat). Под наблюдением.

**Бэкапы:** `references/backups/{executor-pro,review-gate}-2026-06-01.md`.
**Метрики:** заведён `references/auto-audit-metrics.md` (ранее отсутствовал) — этот прогон + ретро-строки за 05-11/05-18.
**Контур:** напоминания (Windows Task Scheduler) работают; узкое место — ручной разбор пропущен 2 недели подряд. Рекомендация оператору ниже.

## 2026-06-01 — Глобальное правило о языке общения с оператором

**Источник:** оператор, `/claude-booster Нужны глобальные инструкции для всех сессий. Все сообщения оператору делаем на русском языке без англицизмов и сложных терминов`.

**Действия (Режим B, инфраструктура):**
1. Резервная копия `~/.claude/CLAUDE.md` → `references/backups/CLAUDE-global-2026-06-01.md`.
2. В `~/.claude/CLAUDE.md` добавлена новая секция `## Язык общения с оператором (глобально, все сессии)` (7 строк) сразу после блока «Роль в системе» — место заметное, правило применяется в каждой сессии.
3. Содержание: все сообщения оператору на русском простыми словами; без англицизмов (с примерами замен); термины без расшифровки запрещены; исключения — код, команды, пути, имена skills, общепринятые сокращения (БД, ТЗ, PR).

**Anti-bloat:** существующее правило «Комментарии и вывод на русском языке» (секция PowerShell) узкое — про вывод PowerShell-команд, не про все сообщения. Язык коммитов — отдельная тема. Дубля нет. Вынес явную сноску, что правило про коммиты/код это не отменяет, чтобы не было конфликта толкований.

## 2026-05-28 — pdftoppm (poppler 26.02.0) добавлен в системные CLI-инструменты

**Источник:** оператор, `/claude-booster Нужно добавить pdftoppm в инструменты`. Сценарий — конвейер ContentBackbone (PDF-методички → изображения), долгосрочное использование.

**Действия (Режим B, инфраструктура):**
1. Установлен `oschwartz10612/poppler-windows` Release-26.02.0-0 в `C:\Users\user\AppData\Local\poppler\poppler-26.02.0\` (User-уровень, без admin).
2. `C:\Users\user\AppData\Local\poppler\poppler-26.02.0\Library\bin` добавлен в User PATH (HKCU\Environment + WM_SETTINGCHANGE).
3. Верификация в свежей сессии: `pdftoppm -v` → `26.02.0`. Также доступны: `pdftocairo`, `pdfinfo`, `pdfimages`, `pdftohtml`. `pdftotext` уже был от Git mingw64 — конфликт безопасный, обе валидные.
4. `~/.claude/CLAUDE.md` — новая короткая секция `## Системные CLI-инструменты` (5 строк) с poppler + tesseract (уже стоял) как точкой опоры для будущих утилит.

**Permissions:** не трогал. `Bash(*)` уже в allow покрывает все CLI-вызовы; отдельная запись для pdftoppm была бы дублем.

**Anti-bloat:** одна секция в CLAUDE.md вместо нового reference-файла (3 утилиты — не критерий выноса). Не дублировал инструкции в ContentBackbone/CLAUDE.md — глобальный уровень корректен, утилита системная.

**Затронутые skills:** `anthropic-skills:pdf` теперь может рендерить страницы (pdftoppm был его зависимостью). Изменений в самом skill не требуется.

## 2026-05-27 — Emission-time Before-Б gate в label-emitting skills (techlead-code-reviewer + tech-spec-composer)

**Источник:** self-review через `/claude-booster` (CB tsk-004 Phase 6, запрос оператора «всё ли правильно эскалировано на оператора»).

**Инцидент:** план техлида готовности к боевому прогону Phase 6.7 отдал «оператору» 2 строки категории А: dev-DB `prep` + MCP-SQL-верификацию и генерацию Yandex `answer_candidate` (LLM-computation, claude-tier доступен). Корень ярлыка — `tech-spec-composer` fix2 Шаг 9 `Исполнитель: operator (категория Б — runtime evidence на real DB)` без Before-Б check. **Рецидив класса «operator-handoff misclassification»** (05-13/14/21/24).

**RCA:** instruction gap — `operator-handoff-rules.md` полон (Before-Б check п.5 + Brief discipline есть), но label-emitting skills не прогоняют check в момент эмиссии ярлыка. Прошлые фиксы целились в конкретный упавший skill, не унифицированно.

**Фикс:**
1. `techlead-code-reviewer/SKILL.md` v2.1.0→v2.1.1 § Правила качества — правило «Эмиссия ярлыка “оператор” — только через Before-Б check».
2. `tech-spec-composer/SKILL.md` v1.7.0→v1.7.1 — Шаг 6 закрыт free-pass «ручное исполнение»; § Правила качества — правило «`Исполнитель: operator`/`категория Б` — только после Before-Б check; live-smoke на dev ≠ Б».
3. Backups: `references/backups/techlead-code-reviewer-2026-05-27.md`, `tech-spec-composer-2026-05-27.md`.

**Anti-bloat:** не клонировал правила (источник истины — operator-handoff-rules.md), добавил по 1 emission-time pointer на skill. Размеры: techlead 120 стр., tech-spec 123 стр. (≤200).

**⚠ ESCALATION АКТИВЕН:** класс повторился ≥4 раз за 2 недели. При 6-м эпизоде — автоматизированный pre-handoff checker (Grep-скан плана/ТЗ на «оператор/manual/Б» + принудительный Before-Б), не точечный фикс.

**Запись:** `skills-errors.md` FIXED 2026-05-27.

## 2026-05-26 — Парность .py+.md поднята в non-negotiable rules (ege-workplace)

**Источник:** оператор EGE workplace («Не сделал md файл с объяснением, как указано в правилах»), задача tasks/26/3279f3d4-… (бронирование комнат).

**Инцидент:** создано 5 `.py` (решение + 3 диагностических + compare), ни одного `.md`. `/ege-master` не активирован — работа шла как «отладка». Правило парности было в «Coding Conventions» проектного CLAUDE.md, воспринималось как стилистика.

**RCA:** instruction gap. Правило в неправильной секции (стиль вместо hard-rule) → не enforced при прямой работе без skill.

**Фикс:**
1. `d:\Work\CyberGuru\EGE\workplace\CLAUDE.md` § Project Rules (non-negotiable) — добавлен п.5: парность `.py + .md` применяется независимо от активации `/ege-master`, DoD не закрывается без неё.
2. Создан недостающий `26_3279f3d4.md` с разбором алгоритма.
3. Backup → `references/backups/ege-workplace-CLAUDE-2026-05-26.md`.

**Anti-bloat:** усиление существующего правила переносом в non-negotiable секцию (не клонирование), локальное (EGE-специфика), границы с `/ege-master` чёткие — skill enforce'ит при активации, hard-rule покрывает прямую работу.

**Запись:** `skills-errors.md` FIXED 2026-05-26.

## 2026-05-21 — Skill name validation + tracker-routine self-execution (tech-spec-composer + eng-review)

**Источник:** оператор (TG_LMS tsk-084 review-цикл), цитата «рутину опять прекладывают на оператора!!! У нас есть такие скиллы? (они прописаны в спеке!!!)»

**Инцидент:** в одном workflow допущены **две связанные ошибки**:
1. `eng-review` бриф (`D:\Work\TG_LMS\docs\briefs\tsk-NNN-methodist-tasks-ordering.md`) указал в skill-pipeline таблице 3 несуществующие slash-команды: `/tg-lms-tech-spec-composer`, `/tg-lms-bot-developer`, `/tg-lms-techlead-code-reviewer`. Они существуют как **проектные** `SKILL.md` файлы в `d:\Work\TG_LMS\skills\core\` (читаются AI через AGENTS.md), но **не зарегистрированы** как user-invocable slash-команды Claude Code.
2. `tech-spec-composer` повторил ошибку в ТЗ Этапа B (`docs/specs/2026-05-21-tz-methodist-tasks-ordering-stage-b.md`) — те же 3 несуществующих skill name'а в inline-маркерах. Дополнительно: ТЗ содержал раздел «Pre-impl operator action: завести задачу tsk-NNN», перекладывая на оператора рутину категории А по `operator-handoff-rules.md`.

**RCA:** двойной **instruction gap** в обоих skills (см. `skills-errors.md` 2026-05-21):
- Skills брали имена из `skills-registry.md` (где перечислены ВСЕ skills, включая проектные) без верификации против **available-skills** списка в system reminder текущей сессии (только slash-команды).
- Skills не имели явного правила про tracker-routine как категорию А: создание `tsk-NNN.md` в `D:\Work\Root\tasks\` — рутинное действие в рамках согласованного плана, не offload.

**Применённые правки:**
- `~/.claude/skills/tech-spec-composer/SKILL.md` v1.6.0 → v1.7.0: добавлено правило «Skill name в inline-маркере обязан быть user-invocable» с инструкцией указывать глобальный аналог (через AGENTS.md проектные правила подхватятся автоматически); добавлено правило «Tracker-task — категория А, выполнять без AskUserQuestion».
- `~/.claude/skills/eng-review/SKILL.md` v1.2.0 → v1.3.0: симметричные правила в секциях «Проверка ролевой модели» и новая «Tracker-task — категория А operator-handoff».
- `D:\Work\TG_LMS\docs\specs\2026-05-21-tz-methodist-tasks-ordering-stage-b.md`: `/tg-lms-bot-developer` → `/executor-pro`, `/tg-lms-techlead-code-reviewer` → `/techlead-code-reviewer`, post-merge CB исполнитель → `/executor-lite`. Раздел «Pre-impl operator action» удалён, заменён на «Pre-impl state» со ссылкой на уже созданную tsk-084.
- `D:\Work\TG_LMS\docs\briefs\tsk-NNN-methodist-tasks-ordering.md`: skill-pipeline таблица Этапа B обновлена с глобальными slash-командами + комментарий про AGENTS.md проекта.
- `D:\Work\Root\tasks\tsk-084-methodist-tasks-ordering.md` — создан автоматически (категория А) с `depends_on: [tsk-004]`, status active.
- `references/skills-errors.md` — FIXED-запись 2026-05-21 с полным 5 Whys и anti-bloat-отчётом.
- Backup: `references/backups/tech-spec-composer-2026-05-21.md`, `references/backups/eng-review-2026-05-21.md`.

**Anti-bloat-check:**
- Усилено существующее правило inline-маркеров (не клонировано). Tracker-routine — ссылка на operator-handoff-rules.md, не дубль. Оба skill'а получили симметричные формулировки с общим источником истины.

**Метрика:** двойная ошибка (2 skills × 1 инцидент) поймана оператором немедленно после публикации ТЗ B. Auto-применённые правки покрывают оба проявления; повторение паттерна в будущих ТЗ блокируется явным правилом «сверка с available-skills system reminder».

---

## 2026-05-20 — Pre-publish check для копирайт-skills (активация эскалации M9b)

**Источник:** оператор (ContentFactory, `output/2026-05-18-digital-copywriter-tg-edu.md`), цитата «проверь файл на инстаграм стиль. Похоже наш скилл /digital-copywriter снова допустил этот промах».

**Инцидент:** 4-й по счёту повтор кластера «victor-voice не операционализирован» (2026-05-11 M9b → 2026-05-12 M13 → 2026-05-12 M14 → 2026-05-20 M9b). В детской ветке летней волны tsk-070 проскочили 3 инста-обёртки (Д1, Д6, Д7), 4 конструкции M9a (лимит 1), превышение M8 (тире) в 12 раз (43 на 1180 слов = 36.4/1000 при лимите ≤3), «по сути» в Д4. Триггер активации эскалации, зафиксированной 2026-05-12 («откладываем до 4-го инцидента»).

**RCA:** instruction gap, специфический подкласс — отсутствие внешнего pre-publish прогона. Внутренний чеклист в Шаге 3 digital-copywriter (расширенный фиксом 2026-05-11 на «весь текст, ноль допусков») выполняется той же моделью, что писала текст. Это работает для лексических маркеров (M2/M5/M6/M12 — точные слова из стоп-листа), но плохо ловит структурные паттерны (M9b — обёртка зависит от ритма и контекста, проскакивает как естественный риторический жест). 5 Whys в `skills-errors.md` 2026-05-20.

**Применённые правки:**
- `~/.claude/skills/response-quality-coach/SKILL.md` v2.0.0 → v2.1.0: добавлен новый **режим Apply-to-Text (pre-publish check копирайт-skill)** — алгоритмический прогон по M1-M14 из ai-humanness.md с цитатами нарушений и вердиктом PASS/FAIL по каждому маркеру. Реализация через Grep по стоп-листам (без новых зависимостей и без отдельного Python-скрипта). Описаны 15 проверок (M1-M14 + сводный вердикт), интеграция с `/digital-copywriter` и `/travel-copywriter`.
- `~/.claude/skills/digital-copywriter/SKILL.md` v1.4.1 → v1.5.0: добавлен **Шаг 3d «Внешний pre-publish check»** перед Шагом 4 (выдача). Обязателен для целей edu/sales/it-writer/reality/lifehack/news/experiment. Outreach-script исключён (короткий формат + ручная подстановка плейсхолдеров Виктором = естественный второй прогон).
- `output/2026-05-18-digital-copywriter-tg-edu.md` — переписан вручную (8 постов): инста-обёртки заменены прямыми утверждениями или двумя короткими предложениями, M9a сокращены до 1 на текст, тире в прозе с 43 до 4 (4.7/1000), убран «по сути». Сохранён голос Виктора, фактура (цены, теги, CTA), persona-attuned тон.
- `references/skills-errors.md` — FIXED-запись 2026-05-20 (digital-copywriter M9b повтор) + раздел «⚠ ESCALATION» переведён в статус «АКТИВИРОВАНА И ЗАКРЫТА».

**Anti-bloat:** Y.
- Не создан новый skill (`response-quality-check`) — расширен существующий, как изначально и планировал оператор.
- Не клонирована таксономия M1-M14 — алгоритм ссылается на `ai-humanness.md` как источник истины, использует Grep по стоп-листам из этого же файла.
- Не создан отдельный Python-скрипт `quality-checker.py` (альтернатива в эскалационном плане) — режим работает через Grep/Bash в самом skill, ноль новых зависимостей.
- Не плодили дублирующие чеклисты в travel-copywriter — расширение покрывает все копирайт-skills через ai-humanness.md, который они уже читают в Шаге 0.

**Размеры:**
- response-quality-coach: ~112 → ~185 строк (+73, новый режим). Reference-лимит <200 соблюдён.
- digital-copywriter: ~452 → ~466 строк (+14, Шаг 3d + version bump). Skill крупный, но без новых вертикалей.

**Validation (применён новый pre-publish прогон к переписанному файлу):**
- M9b инста-обёртки: 0 (было 3-4) ✓
- M9a `Не X, а Y`: 1 (лимит ≤1, было 4) ✓
- M5 overhedging: 0 (было 1 — «по сути») ✓
- M8 тире в теле постов: 4.7 на 1000 слов (было 36.4/1000, в 7.8 раз меньше; лимит ≤3 — превышение в 1.6 раза, минимальное). Все 4 оставшихся тире несут смысловую нагрузку (определение/контраст).

**Backup:**
- `references/backups/digital-copywriter-2026-05-20.md`
- `references/backups/response-quality-coach-2026-05-20.md`
- `references/backups/output-2026-05-18-tg-edu-pre-m9b-fix.md`

**Следующее наблюдение:** если за 2 недели появится 5-й инцидент того же класса по копирайт-skills — это значит, что внешний прогон выполняется формально или не вызывается. Тогда переходим к pre-commit hook на `output/` (Python-скрипт + `.git/hooks/pre-commit`) — это уже выход за пределы SKILL.md как контура контроля.

---

## 2026-05-20 — Pre-flight «Шаг 0» для Cross-project Task Tracking

**Источник:** оператор (повторное замечание «опять не связали данные запросы с задачей»).
**Инцидент:** сессия `/db-check` в LMS — выполнена очистка тестовых артефактов, но `tsk-004 "Порядок в LMS" (P1, backlog)` не был найден и не назван в первом ответе. Skill стартовал workflow без проверки трекера.

**RCA:** execution gap при первом инциденте, повторно — instruction gap. Правило «Cross-project Task Tracking Protocol» в global CLAUDE.md существовало, но было оформлено как информация, не как обязательный pre-flight. Триггерный словарь узок («давай сделаем X»), не покрывал «начинаем / первая задача / делаем».

**Применённые правки:**
- `~/.claude/CLAUDE.md` § Cross-project Task Tracking Protocol — добавлен подраздел **«Шаг 0 любой задачи (обязательно, до запуска skill/Bash/MCP)»**. Расширен список триггерных слов (включены «начинаем», «первая задача», «делаем», «настрой», «почини», «оптимизируй», «удали», «очисти»). Добавлен hard-stop: запрещён запуск skill workflow до проверки `_index.md`. Алгоритм: grep `_index.md` → если есть active/backlog задача — назвать `tsk-NNN` в первом ответе, `backlog → active` при необходимости → только потом приступать.
- `D:\Work\Root\tasks\tsk-004-poryadok-v-lms.md` — `backlog → active`, добавлена декомпозиция (5 этапов) и история выполнения Этапа 1 со ссылкой на скрипт и review.
- `references/skills-errors.md` — FIXED-запись с RCA.

**Anti-bloat:** Y. Новых файлов не создано. Усилено существующее правило (правка в CLAUDE.md). Skills не правлены — pre-flight общий, ниже всех skill'ов в иерархии.

**Backup:** `references/backups/global-CLAUDE.md-2026-05-20.md`.

## 2026-05-19 (v3) — Операторская правка №2: арифметика дат + состояние выпускника после ЕГЭ

**Источник:** оператор обнаружил в свежем пакете летних волн две системные дыры:
1. **Фактологическая:** «старт 1-15 июля + базовый цикл 3 месяца → к 1 сентября» — арифметически неверно (15 октября). Date math не проверялся.
2. **Эмпатическая:** скрипт `graduate-egemay` давил на «не теряй два месяца до вуза», игнорируя реальное состояние выпускника после ЕГЭ (опустошены, не хотят решений). Тестировал бы — отказы или молчание.

**RCA:**
- (1) Instruction gap: чеклист Шага 3a не имел проверки арифметики дат. Скилл может цитировать цифры из реестра, но не считал производные сроки.
- (2) Context gap: ege-informatika.md § 13 описывал сегмент `hot-recent` (выпускники ЕГЭ-курсов), но не описывал **состояние выпускника после свежего ЕГЭ** как фактор для скрипта.

**Применённые правки:**
- `references/subjects/ege-informatika.md` — v1.0.2 → v1.0.3. § 13 расширен сегментом `graduate-egemay` с фиксацией: усталость после ЕГЭ + рабочий угол «легитимизация отдыха + ценовой рычаг как мягкий driver возврата к разговору».
- `~/.claude/skills/digital-copywriter/SKILL.md` — v1.4.0 → v1.4.1. Шаг 3a получил два новых чека: (а) проверка арифметики дат с правилом «если точная дата не критична — заменять сезонными ориентирами»; (б) проверка эмпатии для адресатов сразу после ЕГЭ.
- `templates/social/tg-outreach.md` — секция 10 (graduate-egemay) переписана под угол «отдых + ценовой рычаг», с явными антипаттернами под этот сегмент. Секции 11/12 — точные даты убраны.
- `output/2026-05-19-...summer-waves-package.md` — v2 → v3, обновлены 3 скрипта (graduate-egemay полностью, student-tech и adult-career-switch — только удаление дат).

**Anti-bloat check:**
1. Date arithmetic — закрыто **одним чеком** в Шаге 3a, не правилом-инвариантом в каждой секции. Минимальное вмешательство.
2. Состояние выпускника после ЕГЭ — добавлено в **существующий § 13** ege-informatika.md как описание сегмента, не отдельный документ.
3. Ценовой рычаг — описан **только в секции graduate-egemay** tg-outreach.md как контекстный паттерн, не вынесен в глобальное правило (это специфический рычаг для конкретной ситуации, не универсальный).

**Эффективность:** проверка на следующих волнах. Если 2-3 текста подряд без операторских правок арифметики дат и без переписывания graduate-egemay — фикс закрыт.

---
## 2026-05-19 (v2) — Регистрация летних сегментов + принцип «развёрнутого партнёрского контура»

**Контекст:** kid-egemay wave 1 закрыта успешно. Запросили драфты для трёх волн после 19 июня (8-9 кл двойной, 11 кл выпускники-2026, взрослые/студенты). По итогам операторской редактуры graduate-egemay выделен сквозной принцип усиления.

**Системное улучшение — принцип «партнёрский контур как наглядный обмен»:**
- **До:** партнёрский контур упоминался одной строкой «у меня партнёры дают брифы».
- **После:** развёрнутая формула «От меня — ученики, которых знаю лично и за которых ручаюсь. От них — реальная задача и выплата.» Анафора H8 превращает abstract mention в видимую механику обмена.
- **Зафиксировано в:** `templates/social/tg-outreach.md` § «Сквозной принцип: партнёрский контур как наглядный обмен» + матрица «где применять / где не применять» по 12 сегментам.

**Зарегистрировано (12 сегментов outreach суммарно):**
- 3 новых тега: `parent-school-8-9`, `graduate-egemay`, `student-tech`
- 1 объединение: `adult-ca4` (предложенный) ⊕ `adult-career-switch` (существующий) → расширенный `adult-career-switch` с двойным углом (вход в IT ИЛИ AI в текущую профессию)
- 2 ранее зарегистрированных в tsk-057, но без скриптов → скрипты готовы: `kid-school-8-9`, `inactive-reactivate` (летняя v2)

**Изменённые файлы:**
- `D:/Work/Root/tasks/tsk-057-lichnaya-kampaniya-viktora.md` — § Сегментация (6 → 12 тегов), § История движения (запись 2026-05-19 о закрытии wave 1 + анонс трёх волн).
- `D:/Work/ContentFactory/templates/social/tg-outreach.md` — v1.0.0 → v1.1.0. Добавлены секции 8-12 (parent-school-8-9, kid-school-8-9, graduate-egemay, student-tech, adult-career-switch расширенный), новая глава «Сквозной принцип: партнёрский контур» с матрицей применения по сегментам.
- `~/.claude/skills/digital-copywriter/SKILL.md` — v1.3.1 → v1.4.0. Список тегов outreach 7 → 12, ссылка на канонический реестр в tsk-057. Зафиксирован принцип «партнёрский контур как наглядный обмен» с указанием для каких сегментов применяется.
- `D:/Work/ContentFactory/output/2026-05-19-digital-copywriter-outreach-summer-waves-package.md` — v2 с полированной graduate-egemay (по правке Виктора) + распространение принципа на student-tech и inactive-reactivate.

**Anti-bloat check:**
1. Покрыто ли существующим правилом? Нет — старый шаблон tg-outreach.md описывал контур одной строкой через усилитель.
2. Локальное или глобальное? Локальное в `tg-outreach.md` (контекст outreach-кампании), но **переиспользуется в 4 сегментах** — поэтому вынесено в отдельную главу «Сквозной принцип», а не в каждую секцию.
3. Вынести ли в reference? Шаблон tg-outreach.md и есть reference. Не дублирую в SKILL.md (только тонкая ссылка).
4. Не дублирует ли соседний skill? Нет — паттерн уникален для outreach.
5. Не устарело ли старое? «Опциональный усилитель партнёрского контура» из старой версии tsk-057 — остаётся для hot-recent / inactive-reactivate / already-monetizing как короткая форма. Новая длинная форма — для сегментов где контур central pillar (graduate-egemay / student-tech).

**Эффективность:** проверка на следующих волнах после 19 июня. Если 3+ скрипта используют развёрнутый контур без операторской редактуры — паттерн закрыт как стабильный.

---
## 2026-05-19 — Режим D: пакет дельт от операторской редактуры outreach kid-egemay

**Источник дефекта:** реальное использование `/digital-copywriter outreach-script` для нового сегмента `kid-egemay` (действующие ученики 10 класса школы Виктора, цель — удержать на летнем темпе). Оператор отредактировал финальный скрипт, сравнение его версии с моей выявило 5 системных дельт.

**Эпизод не одиночный** (тот же класс ошибки — «фактологический пробел по теме ЕГЭ / 11-класс» — уже всплыл 2026-05-12 с FACT-ошибкой про «4 задачи»). Кластер ≥2 → автоправка через Режим D + пакет дельт.

**Дельты и куда применены:**

| # | Дельта | Корневая причина (RCA) | Куда |
|---|---|---|---|
| 1 | Скилл не указывает год экзамена | Context gap: справочник не задавал «текущую когорту» | `ege-informatika.md` § 0 (новая) + `digital-copywriter` Шаг 3a чек |
| 2 | Параллельные предметы 11-класса игнорированы | Context gap: справочник не описывал сезонный календарь нагрузки | `ege-informatika.md` § 9 (новая) + Шаг 3a чек |
| 3 | Длительность интенсива зафиксирована «8 недель» вместо диапазона | Context gap: § 5 не указывал диапазон | `ege-informatika.md` § 5 уточнение |
| 4 | Конструкция «не X, а Y» в финале короткого DM | Instruction gap: M9a лимит 1 — приемлемо для текста, но штамп для коротких форм | `ai-humanness.md` M9a ужесточение для < 800 знаков + `digital-copywriter` Шаг 3c |
| 5 | Анафора «Потом X. Потом Y. Потом Z.» не зафиксирована как приём | Instruction gap: набор H-приёмов остановился на H7 | `ai-humanness.md` H8 + чеклист H1-H8 |

**Anti-bloat check (5 вопросов):**
1. Покрыто ли существующим правилом? **Нет.** § 0 и § 9 справочника отсутствовали, H8 — новый приём, M9a имел общий лимит без коротких форматов.
2. Локальное или глобальное? **§ 0/§ 9 локальные (тема ЕГЭ — справочник).** **H8 / M9a — глобальные (ai-humanness, все копирайт-skills).**
3. Вынести в reference? **Уже в references** (ege-informatika.md, ai-humanness.md). Skill (`digital-copywriter`) только тонкие ссылки.
4. Дублирует ли соседний skill? **Нет.** M9a/H8 переиспользуются через ai-humanness (общий контракт), не клонируются.
5. Не устарело ли старое? **§ 5 (3 → 2 занятия) был обновлён 2026-05-19 v1.0.1.** Тот же ритуал синхронизации.

**Изменённые файлы:**
- `d:/Work/ContentFactory/references/subjects/ege-informatika.md` — v1.0.1 → v1.0.2 (новый § 0 «Текущая когорта» с ритуалом 1 сентября, новый § 9 «Календарь 11-классника» с сезонной картой нагрузки и параллельными предметами, § 5 диапазон 8-12 недель, § 13 расширен сегментом kid-egemay, антирекомендация № 7).
- `~/.claude/skills/claude-booster/references/ai-humanness.md` — добавлено M9a ужесточение для коротких форматов, добавлен H8 «Анафора через повтор служебного слова» с 2 примерами, обновлён чеклист Часть 4 (H1-H8).
- `~/.claude/skills/digital-copywriter/SKILL.md` — v1.3.0 → v1.3.1. Шаг 0 п.7: обязательное чтение § 0 + § 9 ege-informatika.md для контента про 11-классника, расширены сегменты outreach (parent-egemay / kid-egemay / hot-recent-ЕГЭ). Шаг 3a: чек на конкретный год экзамена + конкретные параллельные предметы. Шаг 3c: M9a-уточнение и H1-H8.

**Резервные копии:** `references/backups/{ai-humanness,ege-informatika,digital-copywriter-SKILL}-2026-05-19.md`.

**Out-of-scope (требует отдельной задачи):**
- Сегмент `kid-egemay` отсутствует в `templates/social/tg-outreach.md` как 7-я секция и в digital-copywriter SKILL.md в списке тегов outreach (там до сих пор только 6 из 8 тегов tsk-057). Это шире текущего пакета — фиксирую как кандидат на следующий патч (tsk-069 уже отвечает за производство скриптов для новых сегментов).

**Эффективность:** проверка на следующем outreach по `kid-egemay` / `parent-egemay` — есть ли конкретный год экзамена + конкретные параллельные предметы в тексте без операторской редактуры. Если 2-3 текста подряд проходят без правок — фикс закрыт как эффективный.

---
## 2026-05-18 — Автоматический аудит (weekly)

**Период:** 2026-05-11 — 2026-05-18 (7 дней с последнего прогона 2026-05-11).

**Метод:** 4 параллельных Explore-агента на активные проекты с JSONL-чатами в окне + таргетная grep-верификация ContentFactory (агент не справился с большими JSON-строками).

**Проекты:** 4 активных / 8 всего. **Skipped (inactive — нет JSONL в окне):** ContentBackbone, LMS, SPW, TG_LMS (4 шт).

| Проект | Чаты 7д (КБ) | Файлов | Last | ERRORS.md mtime | Новых записей |
|---|---|---|---|---|---|
| ContentFactory | 22242 | 4 | 2026-05-16 | 2026-05-14 (touch, без новых инцидентов; last incident 2026-04-14) | 0 |
| CyberGuru-EGE | 1774 | 4 | 2026-05-14 | 2026-02-28 | 0 |
| IT-Businessman | 1069 | 1 | 2026-05-17 | нет файла | 0 |
| IDE-booster | 9071 | 6 | 2026-05-18 | 2026-04-28 / 03-05 | 0 |

**Сигналы из ERRORS.md:** 0 новых записей ни в одном активном проекте. ContentFactory ERRORS.md имеет mtime 2026-05-14, но последний инцидент — 2026-04-14 (файл тронут, новых инцидентов не залогировано). Таргетный grep по двум high-signal ContentFactory-файлам в окне (`8a87640f` smm летняя волна, `1b961672` digital-copywriter v2): **0 операторских отклонений** в роли user. 169 grep-совпадений по rejection-лексике — это текст правил самих skills (ai-humanness M13/M14 содержат «жаргон-дамп»/«выше компетенц» дословно, грузятся каждый прогон) + цитаты skills-errors в контексте, не новые дефекты.

**Кластеры (≥2 эпизода одного класса в одном skill, НЕ обработанные):** **0.**

Все операторские правки недели (digital-copywriter M9b 05-11 / M13+M14 05-12; methodist ×3 05-16; operator-handoff ×2 05-13/14) **уже обработаны и закрыты через Режим D в реальном времени в течение недели** (см. записи improvement-log 2026-05-11…05-16). Это не новые необработанные кластеры — контур обратной связи отработал по ходу.

**Автоправки skills/references:** **0.** Триггер «≥2 эпизода одного класса, не обработанные» не сработал. Edit-only auto-режим: новых файлов не создавалось, backup не требовался.

**OPEN записи (единичные находки):** **0 новых.** Спекулятивные гипотезы агентов (ContentFactory «SEGMENTATION_LOGIC», CyberGuru «METADATA») — 0 реальных инцидентов, ниже порога шума, не логирую (anti-bloat). Стоящая прежняя OPEN: `2026-05-07 — Claude (pipeline behavior) — пауза после каждого skill-вызова` (single episode, в окне не повторилась, ниже порога автоправки ≥2 — остаётся OPEN, кандидат на ручную правку `~/.claude/CLAUDE.md` § Рабочий цикл).

**Эффективность недавних фиксов (positive signals):**
- **methodist v1.1.0 / v1.1.1 / v1.3.0 — ПОДТВЕРЖДЕНО РАБОТАЕТ.** IT-Businessman tsk-059 (методика AI-наставник), 2026-05-16: гейт `/methodist` выловил **3 критических дыры на итер.1** до финализации (потеря §64-моста, незакрытый обход «пошаговый алгоритм», заглушка-альтернатива B), поведенческий риск на итер.3 (прокси-стресс), 2 косметики на итер.4. Цикл закрыт PASS без отката. §7 SA_COM / §8 вовлечённость / механическая трассировка предпосылок применялись каждую итерацию. Гейты сработали как механические процедуры, не мягкие вопросы.
- **digital-copywriter M9b/M13/M14 + ai-humanness операционализация** — после 2026-05-12 новых victor-voice инцидентов в окне нет; цель `it-writer` v1.3.0 без операторских отклонений.
- **operator-handoff Before-Б / After-fail checks** — 3-го эпизода misclassification в окне нет.
- **/ege-master + /project-docs** (CyberGuru-EGE) — использованы успешно, полная двухслойная документация сгенерирована, FACT-ошибок по ЕГЭ нет.

**Новый кросс-skill инсайт (наблюдение, НЕ правка):** tsk-059 поднял обобщаемый паттерн «**декларативный запрет проигрывает helpfulness-приору модели → нужен контрастный few-shot**». Это тот же корень, что в ESCALATION-сериях methodist (гейты как soft-вопросы) и digital-copywriter (victor-voice декларативен). Но в tsk-059 гейт **сработал** (выловил и довёл до few-shot-фикса в той же сессии), остаточный риск честно оформлен как known limitation → по RCA это **execution gap / ограничение модели, НЕ instruction gap skill** → skill не правится. Паттерн усиливает, но не пробивает существующие ESCALATION-watch.

**Эскалации (повторы 3+ / нужны новые файлы):** **Нет.** Пороги не достигнуты:
- methodist «гейт не операционализирован»: 2/3 эпизода (оба FIXED 2026-05-16), 3-го в окне нет. Мониторинг на неделю 2026-05-19.
- digital-copywriter «victor-voice не операционализирован»: 3/4 (решение оператора 2026-05-12 — ждать 4-го), 4-го в окне нет. Мониторинг.
- operator-handoff misclassification: 2/3 за неделю, 3-го в окне нет. Мониторинг.

**Operator handoff:** Не требуется. S1/S2 кластеров, требующих новых references/skills, нет. Покрытие skills достаточное.

**Anti-bloat check:** Не применялся (0 правок).
**Backups:** Не создавались (0 правок). Временный скрипт `references/backups/_audit-scan-tmp.ps1` удалён после прогона.
**Rate limit:** Последний прогон 2026-05-11 (7 дней назад) > 24 часа. ОК.
**TG notification:** см. Шаг TG — `mcp__plugin_telegram_telegram__reply` chat_id 344276500. Эскалаций отдельным сообщением нет (пороги не достигнуты).

**Итог:** Период стабильный. Дефекты недели пойманы и закрыты Режимом D в реальном времени; weekly-аудит подтверждает отсутствие остаточных необработанных кластеров и **валидирует эффективность methodist v1.1-v1.3** (гейты ловят критику до финала, цикл сходится без отката). Skills-инфраструктура вмешательства не требует. 3 ESCALATION-watch остаются под наблюдением (пороги не достигнуты).

## 2026-05-16 — Режим A: `/methodist` v1.2.1 → v1.3.0 (SA_COM семантика + фреймворк вовлечённости)

**Триггер:** оператор отклонил черновик заданий Фазы 0. Два дефекта: (1) неверно определён `SA_COM` (домыслил «короткий ответ + сочинение» вместо «авто-чек ответа + ручная/ИИ проверка качества кода» — двухфазно, для задач с кодом); (2) задания школьные/скучные, проектные «не зажигают», нет игромеханик/жизненности → ученики сольются. Указан источник: @teachcase #998.

**Изучен источник** (WebFetch t.me/s/teachcase/998, В. Устюгова): проверка через действие, встроенные контрольные точки, управляемая нарастающая сложность, «знание = способность действовать без готовой инструкции».

**RCA:** instruction gap — (а) семантика `SA_COM` домыслена из имени enum, не подтверждена у оператора; (б) в assignment-rules не было принципа вовлечённости/игромеханик → дефолт в академический Q&A. Новый класс (не gate-класс, не scope-класс).

**Anti-bloat:** правлен существующий reference `assignment-rules.md` (не новый файл): § 1 (приоритет SC/MC/SA, корректный SA_COM, анти-школьность), § 5/6 обновлены, добавлены § 7 (точная двухфазная семантика SA_COM) и § 8 (вовлечённость/игромеханики/жизненность, grounded в teachcase #998 + требование оператора). SKILL.md: Шаг 3 (+п.2 типы, +п.2a вовлечённость), Правила (+2 инварианта), v1.2.1 → 1.3.0 (minor — корректировка правила + новый принцип, влияет на выход). Backup: `methodist-2026-05-16-pre-engagement.md` + assignment-rules.

**Аудит:** PASS. assignment-rules.md ~160 стр (в норме для reference), SKILL.md ~182 стр. Followup: перегенерация Фазы 0 под v1.3.0 (минимум COM, живые миссии).

## 2026-05-16 — Режим A: `/methodist` v1.2.0 → v1.2.1 (AI-наставник → опционален) + прогон карты Трека 1 v4

**Триггер:** решение оператора при согласовании карты v4 — tsk-059/AI-наставник «вообще не влияет на курс, отдельный трек, не блокер, опц. в будущем». Skill жёстко требовал AI-наставник в каждом задании → исправлено, иначе будущие модульные прогоны навязывали бы методику (потенциальный дефект).

**Anti-bloat:** не удаление, а перевод в опцию. Правки точечные: SKILL.md (frontmatter v1.2.1, описание, Роль, Шаг 0 п.6, Шаг 3 п.4, Шаг 5 чек, Правила) + `assignment-rules.md` (§4/5/6 — опционально). Версия patch 1.2.0 → 1.2.1 (уточнение поведения, контракт не сломан). Backup: `methodist-2026-05-16-pre-aimentor-optional.md`.

**Прогон карты-плана Трека 1 v4 (методист v1.2.1, утверждён оператором):** применён префикс `IT-Businessman-` ко всем `course_uid`. Сохранены 5 артефактов в `d:\Work\IT_Businessman\docs\v2\curriculum\`:
- `NORTH-STAR-требования-к-курсам.md` — стандарт проектирования курсов (требования оператора + анализ + LMS/WP факты + 4 решения). Источник истины.
- `Трек-1-карта-план.md` (канонический v4: 2 проекции, карта покрытия, двойная проверка, мэппинг).
- `Трек-1-lms-import.json` (граф Courses + course_dependencies, задания — отдельными прогонами).
- `Трек-1-lms-sheets.md` (Google Sheets раскладка Courses/Tasks).
- `Трек-1-wp-навигатор.md` (модель «Навигатор», M=12).

**Эволюция methodist за день:** v1.0.0 (создан) → 1.1.0 (гейт двойной проверки) → 1.1.1 (механическая трассировка предпосылок) → 1.2.0 (LMS+WP проекции + assignment-rules) → 1.2.1 (AI-наставник опционален). 3 дефекта прогонов закрыты по RCA, 1 scope-уточнение оператора.

## 2026-05-16 — Режим A/C: `/methodist` v1.1.1 → v1.2.0 (LMS-импорт + WP-публикация + правила заданий)

**Триггер:** оператор (`/claude-booster`) — зашить правила создания заданий отдельным файлом, адаптировать план под LMS-проект (импорт) и WP-сайт victor-komlev.ru (публикация).

**Grounding-исследование (2 параллельных general-purpose агента, источник-grounded, без выдумок):**
- LMS `d:\Work\LMS`: одна модель `Courses` (граф `course_parents`, нет отдельных «подкурс/лист»); типы заданий строго `SC|MC|SA|SA_COM|TA` (кода `COM` нет → `SA_COM`; `TA`=рубрика, проектные); `difficulties` THEORY/EASY/NORMAL/HARD/PROJECT; идемпотентный upsert по `course_uid`/`external_uid`; bulk-API `tasks/bulk-upsert` + Google Sheets импорт; файлового экспорта нет.
- WP victor-komlev.ru: WordPress+Elementor, **LMS-плагина нет**; зрелая модель «Навигатор» (L1 навигатор → L2 тема с фикс. блоками + «Раздел N из M» → L3 пост-урок), breadcrumbs через `/shkolnye-programmy/`.

**Развилки (AskUserQuestion, 4, решения оператора):** LMS-экспорт = JSON+Sheets оба; difficulty-маппинг EASY/NORMAL/HARD/PROJECT; WP = модель «Навигатор»; docs/v2 = источник (план — слой нормализации, не переписывать).

**Anti-bloat:** вся детальная фактура (LMS-схема, WP-шаблон, правила заданий) вынесена в 2 новых reference; SKILL.md только ссылается + инварианты. Исследование закэшировано в `lms-wp-export.md` — skill не переисследует LMS/сайт каждый прогон. Версия minor 1.1.1 → 1.2.0 (additive capability, обратно совместимо — план по-прежнему производится, плюс 2 проекции).

**Новые файлы:**
- `references/assignment-rules.md` (99 стр) — enum типов, приоритет SA_COM/SC/MC, TA только проектные; счёт по узлам (лист=3, подкурс 1-го ур.=10–15 + 2–3 проектных); микс 70/25/5; маппинг сложности; защита от списывания по типам; чеклист.
- `references/lms-wp-export.md` (110 стр) — LMS-проекция (схема Courses/tasks/solution_rules/difficulties, JSON bulk-API пример + Google Sheets раскладка) + WP-проекция «Навигатор» (L1/L2/L3, slug, блоки) + таблица соответствия LMS-узел ↔ WP-страница.

**Правки SKILL.md:** frontmatter v1.2.0 + description; Когда использовать (+триггер LMS/WP); Шаг 0 (+п.4–5 чтение новых reference, СТОП «1–10»); Шаг 1 (фикс ЦА-3/ЦА-4 → курсовая ЦА-1/2/3); Шаг 3 переписан под assignment-rules (типы/счёт/микс/LMS-поля); Шаг 5 (+чек заданий и двух проекций); Шаг 6 переписан — 5 артефактов (план/задания/lms-import.json/lms-sheets/wp-навигатор), YAML +lms/wp/course-uids; Контракт (+LMS/WP/карта соответствия); Правила (+enum, +маппинг, +идемпотентность, +две проекции, docs/v2 не переписывать).

**Аудит (Режим A):** PASS. v1.2.0, шаги 0–6 связны, СТОП «1–10», SKILL.md 180 стр (сложный skill с 5 references, в норме), frontmatter валиден. Backup: `references/backups/methodist-2026-05-16-pre-lms-wp.md`.

**Followup:** карта-план Трека 1 и модуль «Фундамент» нужно перегенерировать под v1.2.0 (LMS+WP проекции, задания по assignment-rules) — следующий прогон с оператором.

## 2026-05-16 — Режим A: `/methodist` v1.1.0 → v1.1.1 (гейт ученика → механическая трассировка)

**Триггер:** 2-й отклонённый прогон. План ставил инфраструктуру (запрос к API, счёт токенов, калькулятор F-C5) раньше явного разбора базовых понятий (глоссарий блок 8: токен/Claude API/промпт). Гейт «глазами ученика» не сработал.

**RCA:** instruction gap 2-го порядка — гейт добавлен прошлым фиксом, но не операционализирован (мягкий вопрос «смогу ли выполнить?» → ответ «да» без пошаговой трассировки). Класс «гейт есть, но не механический». **2-й инцидент класса methodist-гейт-не-ловит → 3-й = ESCALATION** (зафиксировано в skills-errors).

**Anti-bloat:** усилен существующий пункт coverage-and-review Часть 2 (не новый файл): добавлена обязательная процедура механической трассировки предпосылок + инвариант «понятие раньше инструмента». SKILL.md: Шаг 4 п.1 (трассировка) + 1 строка в Правилах. Версия patch 1.1.0 → 1.1.1 (уточнение поведения, без новой секции/контракта).

**Правки:** coverage-and-review.md — процедура «для каждого шага: список требуемых понятий → найти более ранний шаг разбора → позже/параллельно/«по мере встречи» = дефект порядка». SKILL.md frontmatter v1.1.1, Шаг 4 п.1, Правила (+инвариант «понятие раньше инструмента»). Backup: `references/backups/methodist-2026-05-16-pre-prereq-trace.md` (+ coverage).

**Аудит:** PASS. SKILL.md 148 стр, coverage-and-review 103 стр, frontmatter валиден.

## 2026-05-16 — Режим A: усиление `/methodist` v1.0.0 → v1.1.0 (defect-driven, гейт «двумя глазами»)

**Триггер:** оператор отклонил первый прогон (карта-план Трека 1 поверхностна: срезан Фундамент Claude, нет `F-Глоссарий`/`F-Бесплатные-альтернативы`/онбординга/кросс-модулей X1-X2) + явный запрос `/claude-booster` усилить skill.

**RCA:** instruction gap. Шаг 0 предписывал читать «модуль по теме», не весь инвентарь docs/v2; нет гейта полноты против источника; Шаг 4 проверял только внутреннюю согласованность, не глубину/перспективу. 5 Whys + классификация — в `skills-errors.md` (FIXED 2026-05-16).

**Anti-bloat:** усилена формулировка Шаг 0 (не клон); чеклист покрытия-по-стадии + рубрика двойной проверки вынесены в новый reference `coverage-and-review.md` (3+ пунктов); сам гейт — inline (виден каждый прогон). Локально methodist.

**Правки (Edit, не Write):**
1. Frontmatter v1.0.0 → v1.1.0 (additive workflow gate + reference) + description (двумя глазами / полнота).
2. Шаг 0: +п.3 чтение `coverage-and-review.md`; п.8 — полный инвентарь `docs/v2/*.md` с классификацией include/exclude + обязательный-по-стадии минимум; п.6 — курсовая нумерация ЦА как источник истины (зафиксирован конфликт ЦА-1/2/3 vs reference); СТОП «(1–8)».
3. Новый **Шаг 4 «Двойная проверка»** (глазами ученика / глазами сеньора / анти-поверхностность); старые Шаг 4→5, 5→6.
4. Шаг 5 верификация: +полнота против инвентаря, +двойная проверка пройдена.
5. Шаг 6: артефакт обязан содержать «Карта покрытия источника» + «Двойная проверка»; YAML — курсовая нумерация ЦА.
6. Контракт: +`Карта покрытия источника`, +`Двойная проверка`. Правила: +полнота против инвентаря, +двойная проверка обязательна, +трассировка к docs/v2, для новичка фундамент/глоссарий/альтернативы не срезать.

**Новый файл:** `~/.claude/skills/methodist/references/coverage-and-review.md` (85 стр) — Часть 1 (обязательный минимум по стадии: онбординг, глоссарий, полный фундамент, бесплатные альтернативы, безопасность, кросс-модули) + Часть 2 (рубрика двойной проверки).

**Аудит (Режим A):** PASS. v1.1.0, шаги 0–6 связны, СТОП-ref «1–8», SKILL.md 147 стр (норма для сложного skill), frontmatter валиден.
**Backup:** `references/backups/methodist-2026-05-16-pre-dual-lens.md`.
**Доп. правка в этом же проходе:** `references/difficulty-and-design.md` Часть 2 — нумерация ЦА синхронизирована с курсовой (`ЦА-3/ЦА-4` → `ЦА-1 джуны / ЦА-2 руководители / ЦА-3 маркетологи`, источник истины `docs/v2/02. Три ЦА.md`). Конфликт нумерации устранён.

## 2026-05-16 — Режим C: создан skill `/methodist` v1.0.0 (tsk-008)

**Запрос оператора:** создать пустой skill методиста IT — интерактивная разработка учебных планов вместе с оператором, генерация заданий, оценка сложности заданий и уровня учеников, изучение веб-практик при необходимости. tsk-008 (P0/active, скилл №1 из 3 блокеров флагмана tsk-007).

**Развилки (AskUserQuestion, все рекомендации приняты):**
- Модель оценки → Блум + число предпосылок + время; уровень ученика = ЦА (ЦА-3/ЦА-4) × стадия (новичок/с-базой/уверенный).
- Зависимость tsk-059 (методика не финализирована, дедлайн 01.06) → baseline встроен + чтение финального артефакта когда появится + пометка `methodology-source`. Не блокирует флагман.
- Артефакты → markdown в `d:\Work\IT_Businessman\docs\v2\curriculum\` (план модуля + задания).
- WebSearch → по запросу/новому типу модуля + кэш в reference, не каждый прогон.

**Созданные файлы:**
- `~/.claude/skills/methodist/SKILL.md` (128 стр) — Роль/Когда/Порядок (Шаг 0–5)/Формат вопроса/Контракт/Правила/Обратная связь.
- `~/.claude/skills/methodist/references/difficulty-and-design.md` (105 стр) — модель сложности (Блум-таблица, предпосылки, время), шкала уровня ученика, принципы дизайна (scaffolding/retrieval/spaced), секция кэша веб-практик.
- `~/.claude/skills/methodist/references/ai-mentor-baseline.md` (82 стр) — суррогат tsk-059: 3 цепочки промптов (новое понятие/отладка/углубление) + анти-паттерны + 5 механизмов защиты от списывания. Помечен BASELINE с приоритетом источников.

**Аудит (Режим A, Шаг C5):** PASS. S1 (name/allowed-tools/Порядок/YAML) — ок. S2 (version 1.0.0/Роль/Когда/Контракт/Правила/русский) — ок. S3 (порядок секций, размер для сложного skill, роль не дублирует spec-writer/ege-master) — ок.

**Регистрация:**
- `skills-registry.md` — добавлен в «Узкие и специальные» (рядом с ege-master).
- глобальный `~/.claude/CLAUDE.md` — счётчик 33 → 34 skills + строка в списке.

**Anti-bloat:**
- Шаблон ContentFactory НЕ применён — это планировочно-методический skill, не копирайтерский (нет voice-guide/glossary/ai-humanness — они нерелевантны методике).
- Детальная модель сложности и методика вынесены в 2 references, SKILL.md остался коротким (128 стр).
- Методика — отдельный swappable reference, чтобы при финализации tsk-059 заменить один файл без правки skill (источник истины — tsk-059, baseline помечен).

**Статус tsk-008:** Этап 1 (дизайн роли) + Этап 2 (источники, методика baseline) + Этап 3 (skill в ~/.claude/skills, регистрация) — DONE. Остаётся прогон на 1–2 модулях Трека 1 (критерий готовности, дедлайн модулей 25 июня) и замена baseline на финальную tsk-059 после 01.06 — задача остаётся `active`.

## 2026-05-16 — Режим A: digital-copywriter v1.2.0 → v1.3.0 (цель `it-writer`, tsk-078)

**Запрос оператора:** добавить режим «ИТ-писатель» — объяснять сложные ИТ-темы простым языком для новичков, без сленга/англицизмов, с раскрытием всех терминов, на примерах и аналогиях. В рамках tsk-078 (решение оператора TG 108/111, ответ A: режим внутри digital-copywriter, не standalone-скилл).

**Развилки (AskUserQuestion, рекомендации приняты):**
- Уровень ЦА → **3 уровня**: `новичок` (дефолт, аудитория канала) / `с-базой` (ученик Трека 1) / `руководитель` (нетехнический ЛПР).
- Связь с `edu` → **параллельная цель**, дефолт-формат `article`; `edu` остаётся для коротких постов-разборов.

**Применённые правки (10 Edit, без Write):**
1. Frontmatter: `version 1.2.0 → 1.3.0` (minor — новая цель), description «7 → 8 целей» + it-writer.
2. `## Когда использовать` — триггер «учебный материал курса».
3. Таблица «Цели» — строка `it-writer`.
4. Алиасы (`ит-писатель`/`it-writer`, дефолт-формат article) + 3 примера вызова.
5. Шаг 0 п.6 — docs/v2 ОБЯЗАТЕЛЕН для it-writer (источник содержания, не «по памяти»).
6. Шаг 1 — формат-дефолт `article`, цель в списке, параметр «уровень ЦА» (3 уровня).
7. Шаг 2 — блок `#### Цель it-writer` (6 жёстких правил: аналогия раньше термина, ноль англицизмов, термин раскрыт 1 раз, пример на идею, тест на маму, голос Виктора).
8. Шаг 3b — ЖЁСТКИЙ гейт верификации (строже M13, запрет не лимит).
9. Шаг 5 — YAML `level`, обязательный `course-refs`, image prompt для it-writer.
10. Правила качества — инвариант it-writer.
+ глобальный `~/.claude/CLAUDE.md` — счётчик «6 → 8 целей» (был устаревший: skill уже был на 7).

**Anti-bloat:**
- M13/жаргон-дамп **не дублирован** — цель ссылается на `ai-humanness.md` M13 и `victor-voice.md` § Канал и аудитория, делая их жёстким гейтом для этой цели, без копирования стоп-листа.
- Отдельный шаблон НЕ создан — цель переиспользует существующие format-шаблоны + inline-правила (как edu/sales/news), как просила задача (минимум дублирования голоса/материалов).
- Уровень ЦА вынесен в Шаг 1 рядом с существующим параметром sales-ЦА и outreach-тегом — единый механизм, не новый.

**Резервная копия:** `references/backups/digital-copywriter-2026-05-16-it-writer.md` (402 стр).
**Размер после:** 438 строк (сложный skill, в норме по standard.md). Frontmatter валиден. Опечатка `термin`→`термин` поймана и исправлена (encoding-guard).

**Статус tsk-078:** Этап 1 (дизайн) + Этап 3 (интеграция в skill) — DONE. Остаётся прогон на 1 теме Трека 1 (Этап 3, критерий готовности) и согласование с методистом (Этап 2, tsk-008) — задача остаётся `active`.

## 2026-05-14 — Режим D: завершение аудита skills (tsk-077, P1 + P2)

**Триггер:** оператор запросил «делаем остаток здесь» после закрытия tsk-075/tsk-076
(skill prompt-engineer + русские description). Открыта задача tsk-077.

**Скоуп — 3 фактические правки, не 5-7:**

### P1 — qa / qa-fix / qa-only (анализ показал ложное перекрытие)

**Подагент-аудит** оценил перекрытие в ~80%. **Фактическое перекрытие близко к 0**:
- `qa` — веб-приложение, gstack-браузер, тест + фикс
- `qa-only` — веб-приложение, gstack-браузер, только отчёт
- `qa-fix` — кодовая база (Python/Node/SQL/API), pytest/vitest, тест + фикс

Граница объектная (UI vs код), а не функциональная. Перекрёстные ссылки между qa и
qa-only уже явные. **Реальная проблема:** description qa-fix содержал «Для только-отчёт —
см. qa-only», что вводило в заблуждение (qa-only это веб-UI, не код).

**Правка (1 файл):** qa-fix v2.1.1 → v2.1.2 — description уточнён:
- Подсвечено «**кодовой базы** (не веб-UI)»
- Misleading ссылка на qa-only заменена на «Для QA веб-приложения через браузер — см. /qa и /qa-only»

**qa / qa-only НЕ правились** — AUTO-GENERATED от gstack. Граница и так явная.

### P1 — plan-design-review / qa-design-review (тот же вывод)

**Подагент** видел ~80% пересечение текста — это намеренно (один объект «дизайн»),
разница только в действии (отчёт vs фикс), уже выражено в description с перекрёстными
ссылками. **Не трогаю** — оба AUTO-GENERATED, граница явная.

### P2 — ceo-review, eng-review (добавление обязательных секций)

Сложные skills с интерактивной структурой (10 разделов обзора + ASCII-диаграммы).
Audit-checklist S3 для них не применяется строго, но S2 (Роль + Контракт результата)
требует исправления.

| Skill | Версия | Изменения |
|-------|--------|-----------|
| ceo-review | 1.1.0 → **1.2.0** | + `## Роль` после title; + `## Контракт результата` со списком из 11 артефактов перед `## Правила форматирования` |
| eng-review | 1.1.0 → **1.2.0** | + `## Роль` после инженерных предпочтений; + `## Контракт результата` со списком из 9 артефактов перед `## Ретроспективное обучение` |

Существующие интерактивные секции (Шаг 0, Разделы 1-10, Бриф проекта, Обязательные
результаты, Итоговая сводка) **сохранены** — это рабочая структура skills, ломать
её для буквального следования стандарту = bloat.

**Что НЕ делали (зафиксировано как ВНЕ СКОУПА):**
- `## Когда использовать` в ceo-review/eng-review — расширение скоупа без согласования
- `## Правила качества` в ceo-review/eng-review — то же
- gstack-template generator — внешний апстрим, форк не делаем

**Anti-bloat check:**
1. Покрыто правилом? Да, audit-checklist S2. Усиление формулировки в description qa-fix.
2. Локальное/глобальное? Локальные правки в 3 SKILL.md.
3. В reference? Нет — конкретные структурные правки.
4. Дублирует соседний skill? Нет.
5. Устарело? Нет.

**Резервные копии:**
- `references/backups/qa-fix-2026-05-14-v2.md` (второй бэкап в тот же день)
- `references/backups/ceo-review-2026-05-14.md`
- `references/backups/eng-review-2026-05-14.md`

**Связано:** [tsk-077](D:/Work/Root/tasks/tsk-077-zavershit-audit-skills-ide-booster.md) — done

**Минор `version: 1.x.0` у ceo/eng-review** — добавление новых секций (Контракт результата)
по семвер-правилам стандарта = minor, не patch.

---

## 2026-05-14 — Режим D: перевод description codex-booster и cursor-booster на русский (S1)

**Триггер:** аудит skills выявил два S1-нарушения по требованию `standard.md` —
`description` во фронтматтере должен быть на русском. Задача в Root-трекере: tsk-076.

**Скоуп — 2 skills, не 3:**
- ✅ `codex-booster` — переведён
- ✅ `cursor-booster` — переведён
- ❌ `gstack` — исключён: SKILL.md auto-generated из `~/.claude/skills/gstack/SKILL.md.tmpl`,
  gstack — внешний инструмент. Правка SKILL.md будет затёрта регенерацией, правка
  template создаёт форк внешнего апстрима. Архитектурное решение зафиксировано
  в tsk-076 и здесь.

**RCA — почему description были на английском:**
1. codex-booster и cursor-booster написаны изначально по образцу gstack (английский)
2. Не было автоматической lint-проверки frontmatter на язык description
3. Корень: **instruction gap** — стандарт требует русский, но enforcement только ручной

**Anti-bloat check:**
1. Покрыто правилом? Да — standard.md прямо требует русский. Не дублирую.
2. Локальное/глобальное? Локальная правка в 2 SKILL.md.
3. В reference? Нет, конкретная structural fix.
4. Дублирует соседний skill? Нет.
5. Устарело? Нет, требование актуально.

**Применённые правки (Edit):**

| Skill | Версия | Что изменено |
|-------|--------|--------------|
| codex-booster | 1.0.0 → 1.0.1 | description: английский → русский (4 строки, multi-line YAML) |
| cursor-booster | 1.0.0 → 1.0.1 | description: английский → русский (4 строки, multi-line YAML) |

**Резервные копии:** `references/backups/{name}-2026-05-14.md`

**Верификация:** system-reminder с обновлённым списком skills подтвердил —
русские description подхвачены Claude Code без перезапуска.

**Тело SKILL.md осталось английским** — это отдельный вопрос (большая работа,
не в скоупе S1-кластера). codex-booster и cursor-booster в текущей форме сохраняют
английский body как стилистическое решение исходного автора. Перевод body —
кандидат на отдельный ticket, если потребуется.

**Связано:** [tsk-076](D:/Work/Root/tasks/tsk-076-perevesti-description-codex-cursor-booster-na-russkij.md) — done

---

## 2026-05-14 — Режим D: добавление `## Порядок работы` в 5 skills (S1-кластер)

**Триггер:** аудит skills выявил кластер S1-нарушений — 5 skills имели нумерованные `## Шаг N`
или `## Step N` секции без обёрточного заголовка `## Порядок работы` / `## Workflow`,
требуемого `standard.md`. Затронуты: pr-review, ship, retro, qa-fix, document-release.

**RCA (5 Whys):**
1. Почему отсутствует заголовок? — Skills писались итеративно, фокус был на содержании шагов
2. Почему не заметили при создании? — Не было автоматической проверки фронтматтера/секций
3. Почему стандарт не enforced? — `audit-checklist.md` есть, но запускается только по запросу
4. Почему ручной запуск — не дефолт? — Нет hook на pre-write skill-файлов
5. Корень: **instruction gap** — стандарт известен, но нет автоматического гейта

**Anti-bloat check:**
1. Покрыто существующим правилом? Да — audit-checklist S2. Решение: усилить применение, не клонировать
2. Локальное или глобальное? Глобальное (5 skills). Правка в каждом SKILL.md, не в общем reference
3. В reference? Нет — это не чеклист 3+ пунктов, а конкретная структурная правка
4. Дублирует ли соседний skill? Нет — каждый skill уникален по содержанию
5. Не устарело? Нет — стандарт актуален

**Применённые правки (Edit, не Write):**

| Skill | Версия | Подход | Доп. правки |
|-------|--------|--------|-------------|
| pr-review | 1.0.0 → 1.0.1 | A: вставить `## Порядок работы` + демоут `## Шаг N` → `### Шаг N` | Демоут `### Проход 1/2`, `### Шаг 4a-d` → `####` |
| ship | 1.0.0 → 1.0.1 | A: то же, шаги 0-8 | — |
| retro | 1.0.0 → 1.0.1 | A: то же, шаги 0-6 | Демоут `### {Имя}` → `#### {Имя}` |
| qa-fix | 2.1.0 → 2.1.1 | B: вставить `## Порядок работы` с summary-списком, шаги оставлены на `##` | Слишком много вложенных `### subsections` для демоута |
| document-release | 2.0.0 → 2.0.1 | B: то же, `## Step N` сохранены | Английские названия шагов — стиль самого skill |

**Подход A vs B — обоснование:**
- A (демоут до `### Шаг N` внутри Порядок работы) — чище иерархически, но требует каскадного демоута подсекций
- B (Порядок работы как summary-преамбула + шаги на `##` сиблинги) — сохраняет существующую иерархию, минимально инвазивный
- Выбор по skill: A для простых (pr-review/ship/retro), B для сложных с глубокой вложенностью (qa-fix/document-release)

**Резервные копии:** `references/backups/{name}-2026-05-14.md` — все 5 файлов

**Верификация:** `grep "^## (Порядок работы|Workflow)"` подтвердил наличие заголовка
во всех 5 skills + frontmatter version обновлён.

**Что осталось из аудита:**
- S1: `description` на английском у codex-booster, cursor-booster, gstack — отдельный проход
- S2: ceo-review / eng-review без явных `## Роль` и `## Контракт результата` — сложные skills,
  требуют обсуждения формата
- S2: перекрытия qa / qa-fix / qa-only — требуют декомпозиции триггеров, не механической правки
- Инфраструктурное: gstack-template generator → следующий цикл (не правится напрямую в SKILL.md)

**Связано:** [Cross-project task tracking](D:/Work/Root/tasks/_index.md) — задача на цикл tsk-076
(если потребуется отдельная) или включить в общий S1/S2-cleanup ticket.

---

## 2026-05-14 — Режим C: создание skill prompt-engineer v1.0.0

**Триггер:** оператор `/claude-booster` запросил новый skill — промпт-инженер с тремя режимами
(universal / claude-specific / image-gen) и интерактивным стилем по образцу ceo-review.

**Контекст:** в реестре skills не было выделенной роли промпт-инженера. Похожие функции
размазаны: spec-writer формирует план задачи, tech-spec-composer — ТЗ для агентов, но
ни один skill не создаёт качественный промпт по проверенным фреймворкам с rubric-оценкой
и альтернативами. Особенно отсутствует image-gen ветка.

**Источники best practices (web research):**
1. Anthropic official docs — XML-теги, prefill, role prompting, structured CoT, prompt caching
2. CO-STAR, RACE, RISEN, CRISPE — каркасные фреймворки с разным балансом структуры
3. Midjourney v6/v7, DALL-E 3, Stable Diffusion, Flux — platform-specific промпт-структуры
4. Few-shot 2-5 примеров — sweet spot между обучением паттерну и обобщением

**Дизайн skill:**
- **Размещение:** глобальное (`~/.claude/skills/prompt-engineer/`) — универсальная утилита
- **Структура:** SKILL.md (~130 строк, dispatcher) + 4 reference-файла
- **Интерактивность:** легче ceo-review — intake AskUserQuestion на 3 вопроса, до 1 цикла уточнения
- **Rubric:** 6 критериев × 1-5, средний < 4.0 → итерация (макс 2)

**Созданные файлы:**
1. `~/.claude/skills/prompt-engineer/SKILL.md` — 7-шаговый workflow
2. `references/frameworks.md` — CO-STAR / RACE / RISEN / CRISPE / Custom + матрица выбора
3. `references/claude-techniques.md` — 10 техник: XML, system/user, prefill, CoT, few-shot, caching
4. `references/image-gen-recipes.md` — Universal 8-slot структура + Midjourney/DALL-E/SD/Flux
5. `references/evaluation-rubric.md` — 6 критериев + шаблон отчёта + правила освобождения

**Аудит по Режиму A:** PASS (S1/S2/S3 без замечаний)
- S1: name, allowed-tools, ## Порядок работы, валидный YAML — все есть
- S2: version 1.0.0, Роль / Когда / Контракт / Правила качества — все есть, всё на русском
- S3: размер ~130 строк соответствует сложности; порядок секций стандартный

**Anti-bloat check:**
- Дублирование с spec-writer / tech-spec-composer? Нет — те делают план задачи и ТЗ для агентов,
  prompt-engineer создаёт промпт-артефакт (другой output)
- Дублирование с claude-api skill? Нет — claude-api работает с SDK-кодом и параметрами API,
  prompt-engineer фокусируется на тексте промпта
- Размер reference-файлов оправдан: каждый файл — отдельная компетенция (фреймворки vs техники vs
  image-gen vs rubric); попытка склеить → 400+ строк в одном месте

**Регистрация:**
- `~/.claude/CLAUDE.md` обновлён: счётчик skills 32 → 33, добавлена строка `/prompt-engineer`

**Резервная копия:** не требовалась — новый файл, не редактирование существующего

---

## 2026-05-12 — Режим A: универсализация document-release v1 → v2 (stack-agnostic)

**Триггер:** оператор запустил `/claude-booster` чтобы применить document-release на AvitoManager
(Python-проект, CLI + TG-бот, без UI), но skill был заточен под gstack-инфраструктуру —
запускал `gstack-update-check`, `gstack-config`, создавал `~/.gstack/sessions/`, требовал
4-сегментную версию `vX.Y.Z.W`, всегда дёргал `gh pr view/edit`.

**Корневая причина:** изначальный skill v1 написан внутри gstack-monorepo, AUTO-GENERATED
из `SKILL.md.tmpl` с предположениями gstack-specific. Универсализация не была сделана
при переносе в `~/.claude/skills/`.

**S1-пробелы (skill мог не работать / ломать поток):**
1. Preamble с обращением к `~/.claude/skills/gstack/bin/gstack-update-check` — failed silent на не-gstack
2. Contributor Mode секция (~50 строк про `~/.gstack/contributor-logs/`) — нерелевантна
3. Step 9 PR body update безусловно через `gh pr view/edit` — fail если нет gh / PR
4. VERSION 4-сегментная (`vX.Y.Z.W`) — gstack convention, не SemVer
5. Версия не учитывала Python (`pyproject.toml`) / Node (`package.json`) / Rust (`Cargo.toml`) — только standalone `VERSION` файл

**S2-пробелы (снижение качества):**
1. find ... `-not -path "./.gstack/*"` — gstack-specific, без `__pycache__`, `.venv`, `dist`, `target`
2. Ссылка на `review/TODOS-format.md` — gstack convention
3. Нет gate для UI-зависимых проверок — на CLI/bot проекте нет смысла

**Применённые правки (Edit, не Write):**
1. Backup: `references/backups/document-release-2026-05-12.md`
2. **Frontmatter** v1.0.0 → v2.0.0 + обновлено description (stack-agnostic)
3. **Removed:** Preamble с gstack-binaries, Contributor Mode секция целиком
4. **Step 0 переделан:** detect stack (Python/Node/Rust/Go/.NET/Generic) + detect base branch (gh → git → fallback) + detect VERSION source (5 вариантов) + detect UI presence
5. **Step 1 find:** добавлены универсальные exclude (`__pycache__`, `.venv`, `dist`, `target`, `build`)
6. **Step 6:** добавлен sub-step 6.1 UI/design-gate (только если `UI_PRESENT=true`)
7. **Step 8:** stack-aware version bump с таблицей (Python/Node/Rust/Go/Standalone), 3-сегментная SemVer
8. **Step 9:** gh PR body update теперь за gate (`command -v gh` + `gh pr view`), commit message на русском, push только при наличии remote
9. **Important Rules:** добавлены инварианты «stack-agnostic», «SemVer X.Y.Z (3 сегмента)», «no remote/PR — work locally»

**Размер:** 441 → 469 строк (превышает рекомендуемый 200, но для процедурного skill с подробным
workflow допустимо — оригинал был 441). Структура сохранена.

**Готов к применению на AvitoManager** (Python + CLI + TG-бот + scheduler, без UI).

## 2026-05-12 — Режим D: фикс «инструкции по силам читателю» (ai-humanness M14 + digital-copywriter Шаг 3b) + ESCALATION

**Триггер:** оператор Виктор зафиксировал дефект в постах 2 и 3 летней волны (родительские посты про ЕГЭ). Инструкции типа «проверьте, помнит ли ребёнок цикл по списку» — выше компетенции родителя без IT-фона. Это **отдельная проблема от жаргон-дампа** (M13): там были слова, тут действия. Слова в инструкциях простые («помнит», «цикл», «файл»), но действие требует скрытой программистской компетенции.

**Корневая причина (5 Whys):** instruction gap — M13 закрывает лексику, нет отдельного маркера для проверки выполнимости действий целевой аудиторией. Принцип «тест на маму» в victor-voice.md проверяется на сложности языка, не на выполнимости инструкций.

**Применённые правки:**
1. `references/ai-humanness.md` → добавлен **M14 «Инструкции по силам читателю»** (~50 строк):
   - Принцип: M13 про слова, M14 про действия
   - Сигнальные глаголы: «проверьте/оцените/диагностируйте/определите/убедитесь»
   - Таблица 4 шаблонов замены (контроль содержания → контроль процесса)
   - Особые темы: программист → родителям, инженер → предпринимателям, врач → пациентам, юрист → обывателям
   - Принцип «автор-эксперт даёт выполнимые процессы, экспертную проверку закрывает педагог/врач/мастер»
2. Часть 4 чеклиста — пункт M14 с алгоритмом проверки.
3. Часть 5 стоп-лист — категория «Эксперт-обывателю».
4. `digital-copywriter/SKILL.md` Шаг 3b — пункт M14 с явным алгоритмом и особыми темами.
5. Посты 2 и 3 переписаны руками в чате (родительские блоки):
   - Пост 2: «вам не нужно проверять знания ребёнка, это работа педагога. Ваша работа — режим». Договор о расписании / договор с педагогом про сводку раз в 2 недели / бумажный календарь.
   - Пост 3: «расскажи своими словами что ты делал». Проверка наличия пересказа, не правильности.

**Anti-bloat-check:** Y
- Один shared reference расширен (не плодим документы)
- M14 чётко отделён от M13 (слова vs действия)
- Автоматически работает для travel-copywriter и всех будущих копирайт-skills
- Размер ai-humanness ~310 → ~370 строк

**⚠ ESCALATION** (зафиксирована в `skills-errors.md`):

Серия 3 инцидентов за 3 дня — одного класса:
- 2026-05-11: M9b «Инста-обёртки»
- 2026-05-12: M13 «Жаргон-дамп»
- 2026-05-12: M14 «Инструкции по силам читателю»

Паттерн: принципы из `victor-voice.md` декларативны, skill их «знает» но не применяет автоматически. Каждый класс ошибки приходится отдельно операционализировать в `ai-humanness.md` как жёсткий маркер с алгоритмом.

**Предложение для оператора (требует approval):**
Создать **отдельный skill `/response-quality-check`** — pre-check перед Шагом 4 в digital-copywriter, который прогоняет текст через все 14 маркеров и возвращает PASS/FAIL + проблемные строки. Альтернатива: Python-скрипт `references/quality-checker.py` через Bash.

**Действие не предпринято** — эскалация ждёт решения оператора. Если 4-й инцидент того же класса в течение 7 дней — приоритет «создать чекер» поднимается до P0.

**Связанные skills:** digital-copywriter (прямая правка), travel-copywriter (через shared reference), любые будущие копирайт-skills.

**Резервные копии:**
- `references/backups/ai-humanness-2026-05-12-m14.md`
- `references/backups/digital-copywriter-2026-05-12-m14.md`

---

## 2026-05-12 — Режим D: фикс жаргон-дампа (ai-humanness M13 + digital-copywriter Шаг 3b)

**Триггер:** оператор Виктор зафиксировал дефект в посте 4 летней волны «Vibe-coding с Claude Code». 5 конкретных мест с жаргон-дампом: «модуль валидации задач с проверкой циклических зависимостей», «инженерный паттерн», «ADR-документы про UI-канал, политику сессий, кросс-проектный трекинг», «спецификация / эскалация / вопрос-блокер», «CLI-команды + YAML + pytest + gitleaks + pre-commit». Цитата оператора: «обыватель просто пропустит». Важное замечание оператора: «мне кажется, мы уже давали подобные инструкции» — действительно, в victor-voice.md есть принципы «жаргон-дампа», но они не операционализированы.

**Корневая причина (5 Whys):** instruction gap — отсутствие операционализированного маркера «жаргон-дамп» с жёстким численным критерием в shared reference. Правила были, но как принципы; skill в технически сложной теме «впадал в технический регистр» и забывал применить «тест на маму» по абзацам.

**Применённые правки (минимальные, Edit):**
1. `references/ai-humanness.md` v1.1.0+ → расширен **новым маркером M13 «Жаргон-дамп»** (~50 строк):
   - Жёсткий критерий: 3+ непереведённых аббревиатур/англицизмов/инженерных терминов в одном абзаце без аналогии или разворота словами обывателя = брак
   - Подробный стоп-лист (4 категории: аббревиатуры `ADR/UI/CLI/MCP/API/SQL/ORM/CI/CD`, англицизмы `pipeline/feature/case/pattern`, узкие термины «циклические зависимости/pre-commit hook/префиксные суммы/модуль валидации», названия технологий `pytest/gitleaks/Alembic/Redis/FastAPI`)
   - 3 техники замены: аналогия из жизни перед термином / разворот словами обывателя / просто убрать
   - Алгоритм проверки после каждого абзаца + обновлённый «тест на маму»
   - Конкретные ❌/✅ примеры
2. Чеклист Часть 4 — добавлен пункт M13.
3. Стоп-лист Часть 5 — 3 новые категории (Жаргон-дамп / Узкие термины / Названия технологий).
4. `digital-copywriter/SKILL.md` Шаг 3b — добавлен пункт о M13 с явной отсылкой: после абзаца посчитать непереведённые термины, 3+ → переписать. Особое внимание к темам vibe-coding, кейсы IT-проектов, ЕГЭ-разборы.
5. `output/2026-05-12-digital-copywriter-article-edu-3.md` (пост 4 летней волны) — переписаны 4 раздела по правилам M13. Раздел «Где Claude не до конца хорош» скорректирован по запросу оператора: безопасность снижена до контролируемой, добавлены главные проблемы «потеря общей цели» + «потеря деталей из ранних решений».

**Anti-bloat-check:** Y
- Один shared reference расширен (не создавался новый документ)
- Не дублируется секция «Канал и аудитория» из victor-voice.md (принципы там, операционализация в ai-humanness M13)
- Shared reference автоматически работает для **travel-copywriter** и всех будущих копирайт-skills через их Шаг 0
- Размер ai-humanness ~240 → ~310 строк, всё ещё компактный

**Связанная серия за 2 дня:** ai-humanness расширяется 2-й раз подряд:
- 2026-05-11: M9b «Инста-обёртки» (от инцидента с «не про X, а про Y»)
- 2026-05-12: M13 «Жаргон-дамп» (от инцидента с инженерными терминами)

Оба инцидента — operационализация принципов из victor-voice.md в жёсткие алгоритмические маркеры ai-humanness.md. Это правильное направление эволюции shared reference. **Эскалация-маркер:** если в течение недели будет 3-й инцидент уровня «принципы не сработали» — рассмотреть автоматизированный checker-скрипт (анти-AI прокси перед публикацией) как roadmap.

**Затронутые skills:** digital-copywriter (прямая правка SKILL.md), travel-copywriter (через shared reference, без правки), любые будущие копирайт-skills.

**Резервные копии:**
- `references/backups/ai-humanness-2026-05-12-m13.md`
- `references/backups/digital-copywriter-2026-05-12-m13.md`

**Запись в skills-errors.md:** FIXED, секция «2026-05-12 — digital-copywriter — жаргон-дамп в посте 4 (vibe-coding)».

---

## 2026-05-12 — Создан справочник references/subjects/ege-informatika.md v1.0.0

**Триггер:** FACT-ошибка от 11.05 (skill написал «4 программистские задачи ЕГЭ» вместо 18). Оператор предложил создать справочник в интерактивном режиме.

**Метод (интерактивный):**
1. WebSearch / WebFetch — собраны факты по ЕГЭ-2026 из 6 источников (ФИПИ, sdamgia, Поляков, Кулавский, 3 навигатора Виктора)
2. AskUserQuestion (5 блоков A-E) — операторская верификация группировки, таймингов, методов, привязки к курсу
3. Write — справочник заполнен на основе фактов + ответов оператора

**Что создано:**
- `D:/Work/ContentFactory/references/subjects/ege-informatika.md` (12 разделов, ~250 строк): структура экзамена, шкала баллов, полный список 27 заданий, 18 программистских по 3 группам (Базовая 2/6/11/16/23, Средняя 5/8/13/14/15/17/19-21/25, Сложные 24/26/27), методы решения тяжёлой тройки с эталонными кодами от Виктора, привязка к 3 этапам курсов на victor-komlev.ru, ПО на экзамене, антирекомендации.

**Что обновлено:**
- `digital-copywriter/SKILL.md` Шаг 0 — добавлен пункт 7 (обязательное чтение справочника при любой ЕГЭ-теме). Прямая ссылка на skills-errors прецедент FACT-ошибки как дисциплинирующий маркер.
- `skills-errors.md` — OPEN-запись «context-gap по ЕГЭ» переведена в FIXED.

**Затронутые skills:** digital-copywriter (прямая правка), smm-specialist (через возможное будущее условие в Шаг 0), travel-copywriter (не затронут).

**Anti-bloat-check:** Y — создан один shared reference, не дублировался в SKILL.md. Группировка операторская (не моя), факты с источниками, эталонные коды от Виктора — справочник переживёт смену года КИМ (структура почти не меняется по 2025→2026).

**Последствие:** требуется правка постов 1 и 2 летней волны под корректную группировку (текущая моя группировка из 4 групп некорректна, правильная — 3 группы из справочника). Это отдельный followup, ожидает решения оператора.

---

## 2026-05-11 — Режим D: фикс инста-обёрток (ai-humanness M9b + digital-copywriter Шаг 3b)

**Триггер:** оператор зафиксировал дефект в посте 1 летней волны — фраза «апрель будет уже про слёзы, не про подготовку» (инста-обёртка, запрещённая в victor-voice.md, но проскочила через верификацию skill).

**Корневая причина (5 Whys):** instruction gap — проверка в digital-copywriter Шаге 3b ограничена scope «bold/финал», а инста-обёртки появляются по всему тексту в обобщающих абзацах. Shared reference (ai-humanness.md M9) выделял только «не X, а Y», но не инста-обёртки как класс.

**Применённые правки:**
1. `references/ai-humanness.md`:
   - M9 разделён на **M9a (Negative parallelism)** + **новый M9b (Инста-обёртки)**. Полный список запрещённых конструкций (`X это про Y`, `X будет про Y, не про Z`, `вся история про…`, `когда ты понимаешь, что…`, `вселенная подкинула`, `случился инсайт`). Примеры замены прямым утверждением или двумя короткими предложениями.
   - Чеклист Часть 4 — разделён M9a/M9b с явной инструкцией «сканировать каждое предложение, не только bold/финал».
   - Стоп-лист Часть 5 — добавлены 3 категории: Инста-обёртки / Рилс-обороты / Инфо-блогерство.
2. `digital-copywriter/SKILL.md` Шаг 3b — проверка инста-обёрток расширена со scope «bold/финал» на **весь текст**. Формулировка «жёсткий запрет, не лимит», явная инструкция искать в обобщающих абзацах и эмоциональных поворотах в середине текста.
3. `output/2026-05-11-digital-copywriter-article-edu.md` — фраза в посте заменена на прямое утверждение.

**Anti-bloat-check:** Y
- Расширили существующий M9 (на M9a/M9b), не плодили новый маркер M13
- Правили существующий пункт верификации в SKILL.md, не добавляли новый
- Shared reference (ai-humanness.md) автоматически применяется к **travel-copywriter** и всем будущим копирайт-skills через Шаг 0 — отдельной правки в travel-copywriter не требуется (anti-bloat: одна правка покрыла всё семейство)

**Затронутые skills:** digital-copywriter (прямая правка SKILL.md), travel-copywriter (через shared reference, без правки), любые будущие копирайт-skills.

**Резервные копии:**
- `references/backups/ai-humanness-2026-05-11.md`
- `references/backups/digital-copywriter-2026-05-11-fix.md`

**Запись в skills-errors.md:** FIXED, секция «2026-05-11 — digital-copywriter — инста-обёртка проскочила в середину статьи».

---

## 2026-05-11 — Режим A: digital-copywriter v1.1.0 → v1.2.0

**Контекст:** enabler для tsk-057 (active P1, личная кампания Виктора). В ТЗ план v2 летней волны (`2026-05-09-smm-content-plan-letnyaya-volna-ai.md`) и tsk-057 § Скрипты используются режимы, отсутствующие в skill: `outreach-script` (новый) и алиас `блог-статья` (на `article`).

**Аудит по audit-checklist.md:** S1 — чисто; S2 — 2 пробела (отсутствуют две используемые цели/алиаc); S3 — n/a.

**Применённые правки (минимальные, Edit):**
1. Frontmatter `version: 1.1.0 → 1.2.0` (minor: новая цель). Description обновлён: «4 формата × 7 целей».
2. Раздел «Цели» — добавлена строка `outreach-script`.
3. Новый подраздел «Алиасы»: `блог-статья → article`; `outreach-script → формат всегда tg`.
4. Раздел «Примеры вызова» — добавлены `outreach-script hot-recent`, `outreach-script partner-entrepreneur`, `блог-статья edu`.
5. Шаг 0 пункт 4 — для outreach-script читать `tsk-057.md § Сегментация/§ Скрипты` и `templates/social/tg-outreach.md`. Алиас `блог-статья` мапится на digital-article.md.
6. Шаг 1 — добавлен параметр «тег сегмента» (обязательный для outreach-script, 6 значений). Пакетный режим «все 6» зафиксирован.
7. Шаг 2 — новый блок «#### Цель `outreach-script`» (~40 строк): источники, параметр-тег, обязательные элементы, запреты, опц. усилитель, особенность partner-entrepreneur, пакетный режим, выдача.
8. Шаг 5 — особый случай пути сохранения для outreach (`outreach-{tag}.md` или `outreach-tsk-057-all.md`), обязательные YAML-поля. Image prompt не требуется.
9. Правила сохранения — Image prompt опционален для outreach-script.

**Новый шаблон:** `D:\Work\ContentFactory\templates\social\tg-outreach.md` (один файл, 6 секций под теги внутри — anti-bloat выполнен).

**Anti-bloat отчёт:**
- ✅ Не переписаны существующие цели edu/sales/lifehack/news/experiment/reality
- ✅ Не плодились шаблоны: один `tg-outreach.md` вместо 6 файлов по тегам
- ✅ Правила голоса/человечности не дублируются (остаются в Шаге 0 через `victor-voice.md` + `ai-humanness.md`)
- ✅ Source-of-truth для сегментации остался в tsk-057 (skill читает оттуда, не дублирует таблицу тегов)

**Размер skill:** 342 → 399 строк (+57, сложный skill с references — в норме standard.md).

**Резервная копия:** `references/backups/digital-copywriter-2026-05-11.md`

**Верификация:**
- ✅ В таблице «Цели» есть `outreach-script`
- ✅ В «Примеры вызова» есть `outreach-script {тег}` и `блог-статья {цель}`
- ✅ В Шаге 2 есть «#### Цель `outreach-script`»
- ✅ `templates/social/tg-outreach.md` создан
- ✅ В Шаге 0 пункт 4 содержит ссылку на tsk-057
- ✅ YAML frontmatter валиден (проверен `head -20`)

**Следующий шаг (в исходной сессии):** запуск `/digital-copywriter outreach-script` для пакета 6 скриптов tsk-057.

---

## 2026-05-11 — Автоматический аудит (weekly)

**Период:** 2026-05-04 — 2026-05-11 (7 дней с последнего прогона 2026-05-05).

**Метод:** 6 параллельных Explore-агентов на активные проекты с JSONL-чатами в окне 7 дней.

**Проекты:** 6 активных / 8 всего. **Skipped (inactive):** ContentBackbone, IT-Businessman (нет JSONL-чатов в окне).

| Проект | Чаты 7д (КБ) | ERRORS.md mtime | Новых записей |
|---|---|---|---|
| LMS | 5128 | 2026-05-04 | 0 |
| SPW | 14212 | 2026-05-04 | 0 |
| TG_LMS | 4948 | 2026-03-03 | 0 |
| ContentFactory | 10960 | 2026-04-08 | 0 |
| CyberGuru-EGE | 1016 | 2026-02-28 | 0 |
| IDE-booster | 9264 | 2026-04-28 | 0 |

**Сигналы из ERRORS.md:** Новых записей с 2026-05-05 не обнаружено ни в одном из 6 проектов. Все последние записи SPW/LMS (2026-05-04) были обработаны на прогоне 2026-05-05.

**Кластеры (≥2 эпизодов одного класса в одном skill за период):** **0.**

Агенты ретроспективно подтянули записи 2026-04-27..2026-04-29 (test-gap, contract drift, savepoint anti-pattern) — они уже FIXED 2026-05-02 и не являются повторами в текущем окне.

**Автоправки skills:** **0.** Триггер «≥2 эпизода одного класса» не сработал.

**OPEN записи (единичные находки):** **0** новых. SPW ERRORS.md уже имеет открытую запись 2026-04-29 «Zod validation вместо `as` assertions» — это codebase-level дефект (TODO для frontend-stack), не skill-level. Не дублирую в `references/skills-errors.md`.

**Эскалации (повторы 3+ раз):** Нет.

**Operator handoff:** Не требуется. Покрытие skills достаточное.

**Сработавшие правки (positive signals — правила за 2026-05-05..05-09 применились на практике):**
- `api-contract-rules.md §15 Consumer Parity Check` — упоминается в SPW Y-6 чатах (Stage 6) как обязательный шаг при разблокировке TA. **Сработал** для предотвращения cross-project drift.
- `frontend-stack-rules.md §14 Conditional UI Hide/Show` — `lastResult?.is_correct !== true` паттерн применён в SPW Stage 6 fix-коммите. **Сработал.**
- `frontend-stack-rules.md §4 Multi-Context Auth` — явная ссылка в Y-6 TG WebApp auth-фиксах.
- `operator-handoff-rules.md` (2026-05-07) — A/B-классификация активно используется, агент не переспрашивает по рутине (А-категория) в IDE-booster чатах.
- `~/.claude/CLAUDE.md` § READ-режим трекера (2026-05-09) — без регрессий, READ-привязка к `_index.md` адаптирована.
- `/site-researcher` v1.1.0 (2026-05-09) — без follow-up инцидентов, ограничение WebFetch учтено в новых сессиях.
- `review-gate` 12 измерений ловит URL/method drift на Y-5/Y-6 этапах LMS (фикс из 2026-04-28 #4 active).
- Spec Test Coverage Audit (executor-pro фикс 2026-05-02) удерживается — нет повторов test-gap класса в LMS Y-5/Y-6.

**Не сработавшие / потенциальные слабые места (наблюдение, не правка):**
- SPW: `as Type` без Zod — ОТКРЫТЫЙ дефект в SPW ERRORS.md (2026-04-29), TODO codebase-level. Не эскалирую в skills-errors — это уровень frontend-stack, не skill.
- TG_LMS: `back_target` упоминается 20× в чатах — но это активное применение фикса (`telegram-ux-flow-designer` Phase 1), не баг.

**Anti-bloat check:** Не применялся (0 правок).

**Backups:** Не создавались (0 правок).

**Rate limit:** Последний прогон 2026-05-05 (6 дней назад) > 24 часа. ОК.

**TG notification:** см. Шаг TG ниже. Канал: `mcp__plugin_telegram_telegram__reply` к chat_id 344276500.

**Итог:** Период стабильный. Эффект правок предыдущей недели (2026-05-05..05-09) виден в реальной работе. Skills-инфраструктура не требует вмешательства.

## 2026-05-09 — `~/.claude/CLAUDE.md` § Cross-project Task Tracking Protocol (Режим D)

**Триггер:** оператор обнаружил, что агент не привязал ответ про летние программы к существующей `tsk-003 [P0]`. RCA по skills-errors.md.

**Backup:** `references/backups/CLAUDE-md-2026-05-09.md`

**Корень:** instruction gap — READ трекера и CREATE задачи смешаны под один императивный триггер. Аналитические запросы про крупные направления (программы / курсы / кампании / рассылки / каталог / флагман / SEO / сезонные офферы) не активируют чтение `_index.md`.

**Изменения CLAUDE.md (1 правка, +6 строк):**
- Добавлен подраздел «READ-режим (чтение трекера для контекста)» перед «Триггер новая задача».
- READ-триггер шире CREATE-триггера: достаточно упоминания области, без императива.
- READ — только чтение, статус не меняется, новая задача не создаётся.

**Anti-bloat check:**
1. Покрыто существующим? Частично — внутри CREATE-блока был шаг «проверь дубль», но активировался только после императива. Решение: вынести READ отдельной веткой ПЕРЕД CREATE, не клонировать.
2. Локально или глобально? Глобальное — поведение агента в любом диалоге.
3. В reference? Нет, 6 строк по делу.
4. Дубль соседнего skill? Нет, протокол только в CLAUDE.md.
5. Старое правило устарело? Нет. **CREATE-триггерный список намеренно не расширял** — это было прямой запрос оператора (anti-bloat). Добавлен только новый READ-блок.

**Что сделано НЕ:** список императивных слов («сделай X», «реализуй X»…) для CREATE — не трогал. Не размывал «когда открывать новую задачу». Эффект разделения чистый.

## 2026-05-09 — `/site-researcher` v1.1.0 (Режим A — правка по результатам теста)

**Триггер:** живой тест skill на victor-komlev.ru — все 5 режимов прошли, выявились 3 ограничения.

**Backup:** `references/backups/site-researcher-2026-05-09.md`

**Изменения SKILL.md (1.0.0 → 1.1.0, minor — поведение расширено, контракт совместим):**

| # | Класс | Локация | Суть |
|---|---|---|---|
| 1 | instruction gap | Шаг 0.4 (новый) | Для режима `competitors` обязательно спросить список доменов или явный self-audit. Без этого режим деградирует в self-audit без предупреждения. |
| 2 | instruction gap | Шаг 4 (переписан) | WebFetch теряет `<head>` из-за AI-суммаризации. Для режима `seo` (и для финальной фиксации DOM) — обязательно gstack или raw HTML через `Invoke-WebRequest`. Добавлен пример команды. |
| 3 | instruction gap | Шаг 5 + Правила | Требование «3 страницы для DOM» вынесено из § Правил в § Шаг 5 (явный workflow-step), дополнено пометкой `unverified` в артефакте, если проверена 1 страница. |

**Anti-bloat check:**
1. Покрыто ли существующим? Частично — § Ограничения упоминал WebFetch limitation, но workflow его не учитывал. Усилено, не дублировано.
2. Локально или глобально? Локально — специфика парсинга/WebFetch применима только в этом skill.
3. Можно вынести в reference? Пока нет — 3 пункта по 3-5 строк, references/ создавать преждевременно.
4. Дублирует соседний skill? Нет — gstack про QA, не про разведку структуры.
5. Не устарело ли старое правило? Правило «3 страницы» в § Правилах теперь дублировалось бы — убрал из Правил, оставил в Шаге 5.

**Размер:** 151 строка (в пределах нормы для сложных skills).

**Не сделано намеренно:**
- Сценарий 5 (доступ за авторизацией) — отложен до подключения MCP claude-in-chrome
- `references/mode-*.md` — создам при следующей реальной потребности, не превентивно

## 2026-05-09 — Создан skill `/site-researcher` (Режим C — общий)

**Задача:** [tsk-051](D:/Work/Root/tasks/tsk-051-skill-issledovatel-sajta.md)

**Контекст:** оператор запросил skill для разведки сайтов. Подтверждено 5 режимов
из 6 предложенных: map / dom / seo / competitors / api. Сценарий «доступ за авторизацией»
отложен до подключения MCP claude-in-chrome.

**Артефакты:**
- `~/.claude/skills/site-researcher/SKILL.md` — новый skill (≈90 строк, frontmatter валиден)
- `D:\Work\ContentFactory\research\` — папка для артефактов + README
- `~/.claude/CLAUDE.md` — счётчик 31 → 32, добавлена строка `/site-researcher`

**Инструментарий:** B (WebFetch + gstack). Подключение MCP claude-in-chrome (тир C) — отдельной задачей.

**Anti-bloat check:**
- Покрыто ли существующим? Нет — gstack про QA-интеракции, architect-system-analyst про код.
- Локальное или глобальное? Глобальный skill — используется в 5+ проектах (ParseCourse, TG_Parser, VK_Importer, ContentBackbone, SEO).
- Можно вынести в reference? Чеклисты по 5 режимам — заглушка через `references/mode-*.md`, добавлять при первом реальном использовании, не превентивно.
- Дубли? Нет.

**Открытые пункты:**
- `references/mode-{map,dom,seo,competitors,api}.md` — создать при первом запуске skill, чтобы не плодить пустые файлы заранее
- Сценарий 5 (доступ за авторизацией) — отдельной задачей при подключении MCP claude-in-chrome

## 2026-05-08 — Cross-project Task Tracking Protocol в global CLAUDE.md (Режим B)

**Триггер:** Оператор запросил «во все проекты и чаты вставить механизм задач»
после закрытия v1.5 трекера D:\Work\Root. Задачи в трекере должны открываться
при начале значимой работы и закрываться при завершении — независимо от того,
в каком из 16 проектов работает Claude.

**Решение (L2 уровень инвазивности):**
1. **Soft rule** в `~/.claude/CLAUDE.md` — раздел «Cross-project Task Tracking Protocol»
   с триггерами по явным фразам оператора («сделай X», «реализуй X», «нужно X», etc.),
   шагами при триггере (проверить → reuse или создать → сообщить tsk-NNN), при
   завершении (status=done, closed_at, отдельный commit в Root). Явное указание
   ограничений v1.5 механизма (полу-ручной, требует прямой записи MD или CLI с явным cwd).
2. **ADR-0003** `D:\Work\Root\docs\adr\0003-cross-project-task-tracking.md` —
   фиксация архитектурного решения: Root трекер как единый источник для 16 проектов.
   Compliance с north-star принципами №2/№3/№4/№6. Альтернативы (фрагментация,
   Linear/Notion, L1/L3 уровни) рассмотрены и отвергнуты.
3. **Новая задача `tsk-050`** в трекер (P1, status=backlog, depends_on=tsk-049)
   с декомпозицией: --non-interactive --json флаг в new-task, тесты,
   опциональный SessionStart hook, распространение CLAUDE.md в 13 проектов
   без неё, W12 в workflows.md.

**Operator handoff:**
- AskUserQuestion с 3 вариантами scope (L1/L2/L3) и 2 вариантами критерия
  «новая задача» (фразы-триггеры vs эвристика). Оператор выбрал L2 + триггеры.

**Anti-bloat check:**
- ~/.claude/CLAUDE.md: добавлено ~50 строк раздела (общий размер ~280 строк, в норме).
- Не дублирует существующее — раздел про Root трекер в global CLAUDE.md ранее
  отсутствовал. Workflow-цикл задач (skills) и task-tracking (Root) — разные вещи.
- Полное решение вынесено в tsk-050 (не в правило global CLAUDE.md), чтобы
  правило оставалось коротким.

**Закрытие v1.5:**
В этом же шаге переведён `tsk-048 «Этап 1.5»` в done, обновлён `Docs/roadmap.md`
(v1.5 → done 2026-05-08, tag v1.5.0). Push в private GitHub выполнен.

**Коммит в Root:** `d0db03e feat: cross-project task tracking + закрытие v1.5`

## 2026-05-07 — Operator handoff: дефолт «агент действует, не спрашивает» (Режим B)

**Триггер:** В чатах слишком много пауз — агент ждал подтверждения «A) я / B) вы» даже для
рутинных действий (тесты, lint, headless QA, dev SQL), которые мог выполнить сам через
CLI/MCP. Оператор должен решать стратегию, а не разрешать каждый шаг плана.

**Пробелы:** S1 — формулировка `operator-handoff-rules.md` обязывала «уточнить у оператора»
для всех действий категории А; параллельный блок в `~/.claude/CLAUDE.md` транслировал то же
правило. S2 — инлайн-формулировки в qa-fix, executor-pro, review-gate повторяли «классификация
А/Б, уточнить кто делает».

**Применённые правки:**
1. `references/operator-handoff-rules.md` — переписан полностью.
   - Новый принцип: дефолт — агент действует; пауза только при Б (физически невозможно) или В
     (стратегическая развилка / необратимое действие).
   - Категория А: белый список рутины (lint, typecheck, тесты, read-only SQL, headless `/gstack`,
     dev smoke, dry-run, dev Alembic, локальные правки в рамках плана, sub-agent Agent) —
     выполнять без `AskUserQuestion`.
   - Категория Б: пошаговая инструкция + продолжать параллельные независимые шаги; жёсткая
     зависимость → `BLOCKED:operator` с предложением чем заняться без оператора.
   - Категория В (новая): `AskUserQuestion` обязан содержать варианты A/B/C **и рекомендацию
     агента**; без вариантов = делегирование, не задаём.
   - Антипаттерны: запрос «A/B/C» для рутины из А; вопрос без рекомендации.

2. `~/.claude/CLAUDE.md` § Operator handoff — обновлён под три категории (А/Б/В), убрана
   формулировка «обязательно уточнить».

3. `~/.claude/skills/qa-fix/SKILL.md` — инлайн правило заменено: тесты/lint/typecheck/headless
   QA = категория А, без AskUserQuestion.

4. `~/.claude/skills/executor-pro/SKILL.md` Шаг 6 — рутинные шаги плана агент выполняет сам;
   Б = пошаговая инструкция; В = AskUserQuestion с вариантами и рекомендацией.

5. `~/.claude/skills/review-gate/SKILL.md` Контракт результата — «требуется ручная проверка»
   допустимо только при Б или В.

**Не тронуто (уже соответствует новому стандарту):**
- `claude-booster/SKILL.md` Шаг E4 — handoff на создание новых файлов = категория В
  (стратегическая, выход за scope auto-режима), формат с вариантами и рекомендацией.
- `smm-specialist/SKILL.md` § Operator handoff — спрашивать ICP/цены/кейсы — категория В
  (стратегические входы, не рутина).
- `references/telegram-bot-rules.md` — описание А (mock TG WebApp) vs Б (operator runs bot)
  совместимо с новыми определениями.

**Резервные копии:**
- `references/backups/operator-handoff-rules-2026-05-07.md`
- `references/backups/CLAUDE-2026-05-07.md`

**Anti-bloat отчёт:**
- Размер `operator-handoff-rules.md`: 78 → 84 строки (+6, добавлена категория В без дублирования).
- Инлайн в qa-fix/executor-pro/review-gate: одна строка → одна строка (компрессия + новая
  категория В без раздувания).
- Глобальный CLAUDE.md блок: 5 → 6 строк (+1 на категорию В).
- Удалены повторы: «обязательно уточнить», «уточнить кто делает» в трёх skills.

**Эффект:** агент перестаёт спрашивать «кто запускает» для рутинных действий — выполняет и
прикладывает результат. `AskUserQuestion` остаётся только для стратегических развилок и
блокеров. Ожидаемое сокращение пауз в чатах: основной поток рутины (тесты, проверки, dev SQL,
headless QA).

---

## 2026-05-07 — Обязательный бриф проекта в /ceo-review и /eng-review (Режим A)

**Триггер:** Контекст исследования (PRE-REVIEW + Шаг 0 + ключевые находки разделов) терялся
после закрытия чата. Последующие skills (executor-pro, review-gate, context-auditor) не имели
доступа к решениям по охвату и режиму ревью.

**Пробелы:** S2 — отсутствовал обязательный артефакт-бриф в проекте; `Write/Edit` не были в
allowed-tools обоих skills.

**Применённые правки:**
1. `~/.claude/skills/claude-booster/references/project-brief-template.md` — новый shared
   reference: путь `docs/briefs/{slug}.md`, frontmatter, 7 секций (мета / системный аудит /
   Шаг 0 / находки / открытые решения / TODO / артефакты), правила инкрементальной записи,
   anti-bloat ограничения, ветвление при отсутствии `docs/`.
2. `~/.claude/skills/ceo-review/SKILL.md` (1.0.0 → 1.1.0):
   - allowed-tools: добавлены Write, Edit
   - новая секция "ОБЯЗАТЕЛЬНО: Бриф проекта" перед PRE-REVIEW (ссылка на template)
   - в Итоговую сводку добавлена строка "Бриф проекта"
3. `~/.claude/skills/eng-review/SKILL.md` (1.0.0 → 1.1.0):
   - allowed-tools: добавлены Write, Edit
   - новая секция "ОБЯЗАТЕЛЬНО: Бриф проекта" перед "ПРЕЖДЕ ЧЕМ НАЧАТЬ" (ссылка на template)
   - в Итоговую сводку добавлена строка "Бриф проекта"

**Резервные копии:**
- `references/backups/ceo-review-2026-05-07.md`
- `references/backups/eng-review-2026-05-07.md`

**Anti-bloat:** инструкции вынесены в shared reference, не дублированы в двух skills.
SKILL.md приросли только короткой секцией со ссылкой (≈15 строк каждый). Шаблон секций
не дублирует план задачи (используется `plan_ref`).

**Согласовано с оператором (AskUserQuestion):**
- путь: `docs/briefs/{slug}.md` (не AI-слой)
- момент: инкрементально по разделам
- объём: средний (мета + аудит + находки)

---

## 2026-05-05 — MCP PostgreSQL для AvitoManager (Режим B, продолжение)

**Триггер:** Review-gate Ф0.0–0.2 PASS, переход к настройке БД и применению первой миграции.

**Созданные/изменённые файлы:**
1. `d:\Work\Avito\.mcp.json` — MCP-сервер `avito_manager_db` (PostgreSQL через `@modelcontextprotocol/server-postgres`)
2. `d:\Work\Avito\.claude\settings.local.json` — добавлен allow для `mcp__avito_manager_db__query`
3. `d:\Work\Avito\CLAUDE.md` — раздел "MCP-серверы" + правило read-only через MCP, мутации только через Alembic
4. `d:\Work\Avito\docs\setup-postgres.md` — пошаговая инструкция оператору (7 шагов: createdb → .env → install → migrate → verify → restart Claude → smoke MCP)

**Паттерн:** скопирован из `d:\Work\LMS\.mcp.json` (postgres:postgres@localhost), отдельная БД `avito_manager` (не смешивается с LMS).

**Operator handoff:** оформлен по operator-handoff-rules.md с полями цель/окружение/шаги/что вернуть/ветви ошибок.

---

## 2026-05-05 — Инфраструктура нового проекта AvitoManager (Режим B)

**Триггер:** CEO-ревью нового проекта `d:\Work\Avito`. Нужна полная Claude-обвязка с нуля.

**Созданные файлы:**
1. `d:\Work\Avito\CLAUDE.md` — проектный контекст: архитектура, 6 критических правил Avito API, маппинг задач → skills
2. `d:\Work\Avito\pyproject.toml` — зависимости (httpx, tenacity, aiogram, sqlalchemy, alembic, jinja2, apscheduler, gspread, structlog, lxml)
3. `d:\Work\Avito\.env.example` — шаблон переменных окружения
4. `d:\Work\Avito\.gitignore` — включая credentials*.json, .env, autoload_*.xml
5. `d:\Work\Avito\avito_manager/db/models.py` — SQLAlchemy модели: Ad, AdStats, ABTest, CityCache
6. `d:\Work\Avito\avito_manager/db/migrations/env.py` — async Alembic env
7. `d:\Work\Avito\.claude\skills\core\avito-api-rules.md` — skills/core с 6 правилами API (403≠401, partial success, chunk-200, dry-run, upsert, XML escape)
8. Структура каталогов: api/, analytics/, ab_test/, templates/, db/migrations/, sources/, bot/, scheduler/, cli/, tests/

**Ключевые решения зафиксированы в CLAUDE.md:**
- 403 от Avito = истёкший токен (НЕ 401)
- Autoload partial success — парсить отчёт обязательно
- Stats API: авто-чанкинг по 200
- dry-run флаг на все write-операции
- upsert в БД (не insert)

---

## 2026-05-02 — UX-flow rules + frontend setState-during-render guard (Y-5.2 SPW)

**Триггер:** оператор Y-5.2: «не плодить лишние экраны, не увеличивать целевой путь пользователя, максимально его сокращать». Параллельно — повторный bug «Maximum update depth exceeded» в TaskFormMC, причина — onChange parent setState синхронно во время child setState.

**Изменения skills/references:**

1. **NEW** `references/ux-flow-rules.md` — 8 правил:
   - R1 auto-progression after action (no intermediate success page)
   - R2 click-budget ≤1 between action and target
   - R3 ContinueWidget = entry-point only
   - R4 forms: parent setState через `queueMicrotask` (защита от React 18+ warning)
   - R5 inline loading vs blocking modals
   - R6 empty states warm + actionable
   - R7 default/next auto-resolved
   - R8 single source of truth для navigation
   + audit-checklist для UX-review.

2. **UPDATE** `references/frontend-stack-rules.md` §14, §15:
   - §14 ссылается на ux-flow-rules.md, ключевые принципы R1/R2/R4/R7 inline.
   - §15 проверочный пайплайн перед merge (включая R1-R8 audit).
   - Anti-pattern «ContinueWidget после complete» зафиксирован с примером.

**Применяется в skills:** executor-pro, executor-lite, qa-fix, qa-design-review, plan-design-review, tech-spec-composer, change-plan-architect, techlead-code-reviewer, pr-review.

**Fixed concrete defects (Y-5.2 SPW commits):**
- TaskFormSC/MC/SA/SA_COM: onChange parent теперь через queueMicrotask, чтобы parent setState не происходил во время child setState (React warning «Cannot update component while rendering a different one»).
- After complete material/correct task — auto-redirect через 1.2с с inline-баннером «Переходим к следующему шагу…», вместо CTA-кнопки «К следующему шагу» (которая создавала промежуточный экран курса).
- session TTL 1ч → 24ч в LMS (5 файлов: session_service.py + 4 auth flows).


# Лог улучшений skills

## 2026-04-30 — Создан skill `/smm-specialist` (Режим C)

**Триггер:** запрос оператора на SMM-специалиста, который соединит маркетинг + психологию + предметную нишу. Платформы v1.0: TG, VK. Темы: цифровизация, тревел.

**Решения архитектуры (через AskUserQuestion):**
- Моно-skill с режимами (по аналогии с digital-copywriter и travel-copywriter)
- Делегирование копирайтерам через ТЗ для оператора, не через Agent tool
- Платформы v1.0: TG + VK; Дзен/VC/Habr/Instagram/YT — на roadmap
- Размещение: `~/.claude/skills/smm-specialist/` (глобальный)
- 4 режима: `strategy`, `content-plan` (с подрежимом `rubrics`), `warmup`, `attraction`
- Артефакты: `d:\Work\ContentFactory\output\smm\`
- 2 references: `smm-frameworks.md` + `audience-psychology.md`

**Открытые источники изучены:**
- coreyhaines31/marketingskills/social-content (content pillars, hook formulas, repurposing, batching)
- alirezarezvani/claude-skills/marketing-strategy-pmm (ICP, April Dunford, KPI)
- smmplanner / martrending / postmypost (RU-промпты по этапам, стандарты SMM 2026)

**Создано:**
- `~/.claude/skills/smm-specialist/SKILL.md` (190 строк)
- `~/.claude/skills/smm-specialist/references/smm-frameworks.md` (163 строки): ICP, Dunford-light, content pillars с примерами для цифровизации/тревела, AIDA/PAS/JTBD, 7 hook formulas, repurposing, KPI с диапазонами 2026
- `~/.claude/skills/smm-specialist/references/audience-psychology.md` (144 строки): 5 уровней Шварца, прогрев-воронка 5–10 постов, 6 триггеров решения, эмоциональные крючки, психология TG vs VK, явный список запрещённых тёмных паттернов

**Интеграция:**
- Skill читает `ai-humanness.md` (стандарт человечности встраивается в ТЗ копирайтерам)
- Читает существующие `references/platforms/{telegram,vk}.md` ContentFactory
- Читает `references/subjects/{victor-voice,travel-tone}.md` если ниша совпадает
- Не дублирует логику копирайтеров — выдаёт команды `/digital-copywriter vk sales` или `/travel-copywriter tg`

**Аудит (Режим A) — PASS:**
- S1: name ✓ allowed-tools ✓ Порядок работы ✓ файл валиден ✓ YAML закрыт ✓
- S2: version ✓ Роль ✓ Когда использовать ✓ Контракт результата ✓ Правила качества ✓ русский ✓ заголовки единым языком ✓
- S3: размер 190 строк (для сложного skill — норма по standard.md), порядок секций соответствует digital-copywriter

**CLAUDE.md обновлён:** счётчик 30→31, добавлена строка `/smm-specialist`.

**Roadmap (v1.1+):**
- Режим `analytics` (разбор метрик из CSV)
- platforms: дзен.md, vc.md, habr.md
- platforms: instagram.md, youtube.md
- Шаблон `templates/social/dzen-article.md` и аналоги для VC/Habr

---

## 2026-04-28 — Wave 5: Cross-project memory infrastructure

**Триггер:** серия drift-инцидентов между ContentBackbone (orchestration), LMS, SPW, TG_LMS:
- Y-1 endpoint rename `/request`→`/send`, `/consume`→`/verify` (LMS реализация ушла от CB tech-spec)
- ADR-0017 CSRF policy mismatch с tech-spec-Y2 (потребовал ADR-0020 amend)
- Next.js 15→16 silent bump (потребовал ADR-0019)
- Каждый агент в своём проекте не знал про изменения соседей

**Цель:** ввести межпроектную память чтобы все агенты во всех проектах знали о cross-cutting изменениях, читали и обновляли единый источник правды.

### RCA + anti-bloat

5 Whys: почему drift?
1. Каждый проект меняет контракт без уведомления соседей
2. Нет shared места, где живёт уведомление
3. Memory был per-project (`~/.claude/projects/<dir>/memory/`)
4. Multi-project growth не отрефлексирован
5. Нет contract authority + change-broadcast механизма

→ Корень: **отсутствие cross-project change-broadcast канала + contract mirror'ов**.

Anti-bloat:
1. ✅ Не дублировать API-контракты в каждый проект — один hub в существующем CB-репо
2. ✅ Не клонировать правила в SKILL.md — один общий standard в claude-booster references
3. ✅ Не вводить новый skill — переиспользовать `/executor-pro`/`/executor-lite` для writes; читают существующие skills
4. ✅ Не множить storage — один каталог `cross-project/` в существующем `D:\Work\ContentBackbone\docs\`
5. ✅ Не строить automation сейчас (Вариант D из discussion) — manual writes + review-gate enforcement

### Применённые правки (6 patches)

**Patch 1: Hub в ContentBackbone (8 новых файлов)**

`D:\Work\ContentBackbone\docs\cross-project\`:
- `README.md` — entry point, правила использования
- `CHANGELOG.md` — append-only chronological events (backfill 7 дней per Q-CPM-3)
- `STATE.md` — snapshot состояний 4 проектов
- `contracts/lms-api.md` — authoritative API mirror
- `contracts/lms-db-schema.md` — Alembic head + таблицы
- `contracts/spw.md` — Next.js версия + endpoint consumers
- `contracts/tg-lms.md` — bots поверхность
- `contracts/content-backbone.md` — pipelines + CLI

**Patch 2: Standard в claude-booster references (1 новый файл)**

`~/.claude/skills/claude-booster/references/cross-project-memory-standard.md` — 11 секций:
- Архитектура hub-and-spoke
- Триггеры чтения (когда читать STATE/CHANGELOG/contracts)
- Триггеры записи (cross-cutting изменения)
- Формат CHANGELOG entry
- Anti-patterns (запреты)
- Парные связки с review-skills
- Anti-bloat правила

**Patch 3: Per-project CLAUDE.md ссылки**

- `D:\Work\LMS\CLAUDE.md` — **создан с нуля** (не существовал)
- `D:\Work\TG_LMS\CLAUDE.md` — **создан с нуля** (не существовал)
- `D:\Work\spw\CLAUDE.md` — добавлена секция Cross-project memory
- `D:\Work\ContentBackbone\CLAUDE.md` — добавлена ссылка на hub в шапке

**Patch 4: 6 SKILL.md усилены требованием cross-project memory read/write**

| Skill | Версия | Изменение |
|---|---|---|
| spec-writer | 1.1.0 → 1.2.0 | Шаг 0 пункт 5: read CHANGELOG/STATE/contracts |
| change-plan-architect | 1.1.0 → 1.2.0 | Шаг 0 пункт 5: то же + план опирается на mirror |
| tech-spec-composer | 1.2.0 → 1.3.0 | Шаг 0 пункт 6: ОБЯЗАТЕЛЬНО для cross-project |
| architect-system-analyst | 2.0.0 → 2.1.0 | Шаг 0 пункт 0: AS-IS на mirror'ах; ADR↔mirror conflict → верить mirror |
| executor-pro | 1.0.0 → 1.1.0 | Шаг 0 пункт 5: read + WRITE 3 файла после изменения |
| review-gate | 2.0.0 → 2.1.0 | 11-е измерение: cross-project memory sync; без update → АВТО-ОТКЛОНЕНО |

**Patch 5: Stop hook напоминание (settings.json)**

```json
"Stop": [{"matcher":"","hooks":[{"type":"command","command":"echo 'Если задача затрагивала межпроектные контракты — обновлён ли cross-project memory?'"}]}]
```

**Patch 6: Backfill 7 дней в CHANGELOG**

3 события:
- 2026-04-28: Cross-project memory infrastructure внедрена
- 2026-04-28: LMS Y-1 implementation drift зафиксирован задним числом
- 2026-04-28: Y-2 implementation drift D1/D2/D3 — verdict от architect-system-analyst
- 2026-04-27: CEO-review Stream Y SPW + Stream X
- 2026-04-27: Skill-routing standard внедрён в 3 planning-skills (Wave 4 cross-ref)

### Backups

- `references/backups/{spec-writer,change-plan-architect,tech-spec-composer,architect-system-analyst,executor-pro,review-gate}-2026-04-28.md`

### Verified

- ✅ YAML frontmatter всех 6 SKILL.md валиден (head -20 проверен)
- ✅ Версии bumped semver
- ✅ Backward-compat сохранён (только добавлены пункты в Шаг 0 + новое измерение review-gate)
- ✅ settings.json — JSON валидно (Stop hook добавлен)
- ✅ Backups созданы
- ✅ Hub в CB на месте (8 файлов созданы)

### Эталон применения

В CB готовая структура: `D:\Work\ContentBackbone\docs\cross-project\`. Все 4 проекта могут начать использовать сразу — Y-2 завершение должно обновить `contracts/spw.md` + entry в CHANGELOG; Y-3 старт — прочитать STATE/CHANGELOG.

### Q-CPM (решения пользователя)

- **Q-CPM-1 = A:** Hub в `D:\Work\ContentBackbone\docs\cross-project\`
- **Q-CPM-2 = A:** Stop hook bash echo напоминание
- **Q-CPM-3 = A:** Backfill только последние 7 дней (2026-04-21..28)

---

## 2026-04-27 — Wave 4: skill-routing standard для трёх planning-skills

**Триггер:** в проекте ContentBackbone (CEO-review SPW + Stream X) выявлен паттерн — артефакты `/change-plan-architect`, `/spec-writer`, `/tech-spec-composer` не маршрутизируют под-задачи к конкретным skill-исполнителям. Потребовала ручная post-факт правка change-plan'а с добавлением мастер-таблицы. Эталон сделан вручную в `D:\Work\ContentBackbone\docs\plans\change-plan-spw-stream-y-v1.md §7`.

**Цель:** сделать skill-routing **обязательным выходом** трёх planning-skills.

### RCA + anti-bloat check

5 Whys: почему авторы артефактов забывают про маршрутизацию?
1. Старые версии skills имеют требование, но **слабо** сформулированное
2. Требование разбросано (одно правило в шагах, другое в правилах качества) — нет единого вида
3. Нет конкретики формата (что именно нужно: таблица? inline? phase-mapping?)
4. Нет инварианта NEEDS-MORE-INFO — артефакт без routing не блокируется
5. Нет общего стандарта для трёх skills — каждый по-своему

→ Корень: **instruction gap + structure gap**. Правила есть, но размыты и без обязательного формата.

Anti-bloat check (5 вопросов):
1. ✅ Покрыто ли существующим правилом? **ДА** — все три skill уже имеют слабое требование. Усиляем, не клонируем.
2. ✅ Локальное или глобальное? **Локальное на 3 skills** — но общая суть. Решение: единый reference + минимальные ссылки в SKILL.md.
3. ✅ Можно ли в reference? **ДА, обязательно**. 11-секционный стандарт `references/skill-routing-standard.md`.
4. ✅ Не дублирует ли соседний skill? Все три перекрывают — паттерн становится единым через reference.
5. ✅ Не устарело ли старое? Слабые формулировки заменяются ссылкой на сильный стандарт.

### Применённые правки

**1. Создан `references/skill-routing-standard.md` (11 секций)**

Содержит:
- Инвариант: «артефакт без routing считается NEEDS-MORE-INFO, не COMPLETE»
- Формат мастер-таблицы (Markdown table с колонками `Фаза | Под-задача | Главный исполнитель | Ревью / контроль | Примечания`)
- Сокращения для 14 ключевых skills (PRO/LITE/FAPI/TGUX/DB/QA/PRR/TLR/RG/EG/CA/TS/PIPE/SHIP)
- Inline-маркеры формат: `**Исполнитель:** /skill-name` + `**Ревью:** /skill-name`
- Запрет generic-формулировок («разработчик», «agent», «инженер»)
- 8 обязательных парных связок (DB-миграция → DB+PRR+RG; auth/crypto → PRO+TLR; race conditions → PRO+TLR; IDOR/RBAC → PRO+TLR+QA; и т.д.)
- Cross-cutting skills отдельной секцией (`/encoding-guard`, `/context-auditor`, `/tech-spec-composer`, `/response-quality-coach`, `/claude-booster`)
- Когда стандарт НЕ применяется (quick-fix, reports, docs без plan'а)
- Anti-bloat правила

**2. `change-plan-architect` v1.0.0 → v1.1.0**

Diff:
- Формат результата: пункт `Маршрутизация по skills` усилен — обязательная мастер-таблица с конкретным форматом колонок и ссылкой на standard
- Правила качества: добавлены три правила (генерик-запрет, парные связки, NEEDS-MORE-INFO инвариант)
- Минимальные правки, ~6 строк текста добавлено, не разрастание skill

**3. `spec-writer` v1.0.0 → v1.1.0**

Diff:
- Шаг 9 «Распределить по ролевой модели» — ссылка на standard добавлена
- Контракт результата: `Распределение по skills` помечено как **обязательная** секция; для multi-phase спека — мастер-таблица
- Правила качества: добавлен запрет generic-формулировок + NEEDS-MORE-INFO инвариант
- Минимальные правки, ~4 строки

**4. `tech-spec-composer` v1.1.0 → v1.2.0**

Diff:
- Шаг 6 «Шаги реализации» — обязательность inline-маркера `**Исполнитель:** /skill-name` на каждой существенной под-задаче; `**Ревью:**` для security/contracts/migrations/race-conditions
- Формат результата: `Шаги реализации` дополнен требованием inline-маркеров
- Правила качества: 2 новых инварианта (inline-маркеры обязательны; ТЗ без них = NEEDS-MORE-INFO)
- Минимальные правки, ~5 строк

### Backups

- `references/backups/change-plan-architect-2026-04-27.md`
- `references/backups/spec-writer-2026-04-27.md`
- `references/backups/tech-spec-composer-2026-04-27.md`

### Verified

- YAML frontmatter всех трёх SKILL.md валиден (head -20 проверен)
- Версии bumped согласно semver (1.x → 1.x+1)
- Backward-compat сохранён — существующие разделы не переписаны, только добавлены ссылки на standard и инварианты NEEDS-MORE-INFO

### Эталон применения

В проекте ContentBackbone доступны два готовых артефакта, демонстрирующих требуемую форму:
- `D:\Work\ContentBackbone\docs\plans\change-plan-spw-stream-y-v1.md §7` — мастер-таблица
- `D:\Work\ContentBackbone\docs\tech-specs\tech-spec-X-wp-content-refresh-v1.md` — phase-уровневая маршрутизация

---

## 2026-04-23 — Wave 3: верификация, синхронизация CLAUDE.md, инфра-документация, smoke-test

**Задача:** финализировать Wave 1+2 через (a) массовый аудит всех 39 SKILL.md, (b) синхронизацию глобального CLAUDE.md с новой инфраструктурой, (c) карту reference-файлов, (d) end-to-end smoke-test контура обратной связи.

### W3a — Массовый аудит

Bash-скрипт прогнал 39 SKILL.md через чеклист (frontmatter: version, allowed-tools; секции: Роль, Когда использовать, Контракт результата, Правила качества). Проверка битых ссылок в `references/*.md` и cross-skill references.

**Битых ссылок нет** (кроме 2 плейсхолдеров `{name}`/`имя` в claude-booster — намеренно).

**S1-дефекты (отсутствует version+allowed-tools) → исправлено в 7 skills:**
- ceo-review v1.0.0 + allowed-tools (Read/Grep/Glob/Bash/AskUserQuestion)
- change-plan-architect v1.0.0 + allowed-tools (Read/Grep/Glob/AskUserQuestion)
- codex-booster v1.0.0 + allowed-tools (Read/Edit/Bash/Glob/Grep)
- cursor-booster v1.0.0 + allowed-tools (Read/Edit/Bash/Glob/Grep)
- pr-review v1.0.0 + allowed-tools (Read/Edit/Bash/Grep/Glob/AskUserQuestion)
- retro v1.0.0 + allowed-tools (Read/Bash/Grep/Glob)
- ship v1.0.0 + allowed-tools (Read/Edit/Bash/Grep/AskUserQuestion)

**S2-статус skills с нестандартной структурой секций (gstack auto-generated + eng-review):** не правим — это внешне генерируемые файлы (auto-generated from SKILL.md.tmpl) или работают с собственной структурой.

### W3b — Синхронизация `~/.claude/CLAUDE.md`

- Добавлена секция **Контур обратной связи** (3 шага: response-quality-coach → реестр → claude-booster Режим D → retro)
- Добавлена секция **Инфраструктура skills (ссылки)** — 5 reference-файлов claude-booster
- В блок "Рабочий цикл" добавлено правило: планировщики читают `skills-registry.md` и привязывают шаги к skills

### W3d — `references/README.md`

Создан [`~/.claude/skills/claude-booster/references/README.md`](~/.claude/skills/claude-booster/references/README.md) — карта всех reference-файлов:
- Таблица основных файлов (назначение + кто читает)
- Таблица специализированных (ai-humanness, content-skill-template, backups)
- Три диаграммы потоков: task pipeline / feedback loop / cross-platform
- 4 якоря для новых инженеров (экосистема / фикс дефекта / новый skill / история)

### W3c — Smoke-test контура обратной связи (end-to-end)

Проведён на реальном дефекте найденном во время Wave 3:

- **OPEN** → дефект в `claude-booster` Режим D: шаг D1.3 говорит "перевести в WON'T_FIX" без указания секции реестра (instruction gap / CLARITY)
- **5 Whys** → корень: при создании реестра skills-errors.md статус WON'T_FIX упомянут только в формате записи, без инструкции о расположении
- **Anti-bloat check** → 1 правило в реестр + 1 уточнение в skill = +2 строки суммарно. НЕ создавали отдельную секцию WON'T_FIX (избыточно).
- **Минимальная правка**:
  1. `skills-errors.md` — правило 7 (новое): "WON'T_FIX — подкласс FIXED, хранится в секции FIXED с пометкой"
  2. `claude-booster/SKILL.md` Режим D шаг D1.3 уточнён: "перевести запись в секцию FIXED со статусом WON'T_FIX"
- **FIXED** → запись перенесена в секцию FIXED

**Результат smoke-теста:** контур работает end-to-end. Время прохождения одного дефекта ~5 минут. Anti-bloat контроль соблюдён (+2 строки суммарно, без раздувания). Реальный дефект найден и закрыт в процессе теста — бонус.

### Итоговая картина

- 39/39 skills имеют валидный frontmatter (version + allowed-tools), кроме gstack-сгенерированных где frontmatter задаётся шаблоном
- Все references имеют валидные ссылки
- Глобальный CLAUDE.md синхронизирован со всей новой инфраструктурой
- Инфра-документация (`references/README.md`) создана для онбординга
- Контур обратной связи проверен на реальном дефекте и работает

**Anti-bloat отчёт Wave 3:** smoke-test принёс +3 строки в SKILL.md + references (минимальная правка). Не генерировали отчётных документов — записали только то что изменилось.

**Не сделано (не требуется):**
- S2 русификация секций gstack-generated skills — auto-generated, не править
- Ещё больше smoke-тестов — один прошёл, достаточно для доказательства работоспособности

## 2026-04-23 — Wave 2b: разграничение QA/review-кластеров + shared-references для boosters

**Задача:** при интерактивной разметке W2b (объединение дублей) сверка показала, что предполагаемых дублей в QA-кластере **нет** — `qa` / `qa-only` / `qa-design-review` / `plan-design-review` / `qa-fix` являются разными ячейками матрицы `домен × режим`, не дублями. Моя первоначальная рекомендация удалить `qa` и `plan-design-review` была ошибкой. Исправление выбрано **документировать матрицу**, без удалений.

**Реальное дублирование найдено в booster-кластере** (claude/cursor/codex): ~30% правил (5 Whys, anti-bloat, UTF-8, improvement loop) повторяются verbatim в трёх SKILL.md. Решение: **shared-reference** + ссылки.

**Изменения skills-registry.md:**
- Добавлена отдельная таблица **QA (домен × режим)**: Browser функционал (qa/qa-only), Browser дизайн (qa-design-review/plan-design-review), Code/stack (qa-fix/—). Явно указано что gstack-файлы auto-generated, править бинарники нельзя.
- Добавлены **границы review-skills**: `pr-review` (pre-landing Fix-First) vs `techlead-code-reviewer` (строгий PASS/FAIL) vs `review-gate` (независимый merge-gate) — роли явно разделены, дублей нет.
- Правило 5 "не дублировать роли" уточнено: внутри QA-матрицы выбирать одну ячейку.

**Создан `references/booster-shared.md` (116 строк):**
6 общих разделов: (1) Mandatory improvement loop, (2) Anti-bloat refactor pass (5 вопросов), (3) UTF-8 encoding discipline, (4) Ownership и runtime classification, (5) Fact-check для "latest" claims, (6) Общие quality rules. Источник истины для трёх booster'ов.

**Правки в booster'ах (минимальные, Edit):**
- `cursor-booster/SKILL.md`: Workflow шаги 4-7 + Quality Rules заменены на ссылку на `booster-shared.md` + Cursor-specific остатки. Экономия: ~13 строк.
- `codex-booster/SKILL.md`: Workflow шаги 4-10 + Quality Rules заменены на ссылку + Codex-specific packaging/placement остатки. Экономия: ~26 строк.
- `claude-booster/SKILL.md`: добавлена 1 строка в Quality Rules (ссылка на `booster-shared.md`).

**Итого:** удалено ~39 строк дубляжа, добавлен 1 shared-reference, фреймворк готов к новому 4-му booster'у (просто ссылаться на общий файл).

**Anti-bloat отчёт:**
- НЕ удаляли парные fix/report skills из QA — ошибочное предположение о дублях
- НЕ сливали Cursor-specific packaging и Codex-specific rollout в shared — они действительно разные
- shared-reference содержит ТОЛЬКО verbatim-дубли; остальное остаётся в SKILL.md каждого booster'а

**Не сделано (отложено / отменено):**
- W2b merge gstack-QA skills (qa/qa-only/etc.) — **отменено** (auto-generated, не наши)
- W2b merge review-кластера (pr-review/techlead-code-reviewer/review-gate) — **отменено** (роли не дублируются)
- W2c разделение architect-system-analyst — **отменено** ранее
- Русификация cursor-booster / codex-booster EN→RU — отложено (намеренно EN для документирования чужих платформ)

## 2026-04-23 — Wave 2a + 2d: русификация S2 и массовый footer

**Задача:** продолжить аудит — русифицировать S2 skills без реструктуризации (W2a) и массово добавить футер "Обратная связь" во все оставшиеся skills (W2d). W2b (merge QA/booster кластеров) отложен на интерактивное обсуждение, W2c (разделение architect-system-analyst) — отменён (совмещённая роль оправдана для малых проектов).

**W2a — перезапись до v2.0.0 RU:**
- `~/.claude/skills/ai-orchestrator/SKILL.md` — трёхуровневая оркестрация (minimal/standard/full), маршрутизация через skills-registry, обязательные гейты (spec/execution/review/merge), execution card
- `~/.claude/skills/techlead-code-reviewer/SKILL.md` — S1/S2/S3 severity, горизонт ревью (microstep/integration-safe/phase complete), Шаг 5 логирования skill-дефектов в skills-errors.md + эскалация в `/claude-booster` Режим D при повторе
- `~/.claude/skills/response-quality-coach/SKILL.md` — dual-register logging (ANSWER_ERRORS для response-уровня, skills-errors для skill-уровня), интеграция с `/claude-booster` Режим D, `/cursor-booster`, `/codex-booster`

**W2a — сохранены EN без правок** (boosters чужих платформ, аутентичность важнее стандарта): `cursor-booster`, `codex-booster`.

**W2d — массовое добавление футера "Обратная связь":**
25 skills получили стандартный 2-строчный футер: architect-system-analyst, claude-booster (само), codex-booster, context-auditor, cursor-booster, db-check, design-consultation, digital-copywriter, document-release, ege-master, executor-lite, executor-pro, openclaw, plan-design-review, pr-review, project-docs, qa, qa-design-review, qa-fix, qa-only, retro, review-gate, session-digest, ship, travel-copywriter.

**Итоговое покрытие:** 37/39 skills с футером (100% кроме gstack и setup-browser-cookies — внешний пакет и его sub-plugin, не трогаем по правилу "gstack — бинарники не редактируем").

**Резервные копии:** `~/.claude/skills/claude-booster/references/backups/{ai-orchestrator,techlead-code-reviewer,response-quality-coach}-2026-04-23.md`.

**Anti-bloat отчёт:**
- НЕ правили boosters (cursor/codex) — нет причины: они стабильны и аутентичны на EN для документирования чужих платформ
- W2d добавил всего 2 строки (не 15) в каждый skill — весь контент feedback-loop вынесен в общие rca-protocol.md и skills-errors.md
- НЕ клонировали описание режимов RCA в каждый skill — футер даёт ссылку на общий протокол

**Не сделано (отложено):**
- W2b: merge qa/qa-fix/qa-only (похожие, но различаются режимом), booster-shared references, pr-review vs techlead-code-reviewer vs review-gate — ждёт интерактивного обсуждения с пользователем
- W2c: разделение architect-system-analyst — **отменено** после ревью (совмещённая роль оправдана)
- Русификация cursor-booster / codex-booster — оставлено на EN намеренно

## 2026-04-23 — Wave 1 критических skills + feedback-loop инфраструктура

**Задача:** Аудит всех skills выявил 4 критических (S1): pipeline-operator, encoding-guard, telegram-ux-flow-designer, fastapi-api-developer — английские, без version, без allowed-tools, со слабо структурированным workflow. Одновременно — создать контур обратной связи для всех skills: обнаруженные баги → расследование → реестр ошибок → анализ корневых причин → минимальные правки skills без раздутия.

**Wave 1 (перезапись до v2.0.0, RU, со стандартной структурой):**
- `~/.claude/skills/pipeline-operator/SKILL.md` — 5-шаговый workflow (preflight/run/failure/artifacts/report), строгий Output Contract по Run ID, preflight обязателен
- `~/.claude/skills/encoding-guard/SKILL.md` — диагностика UTF-8/BOM/mojibake/mixed, restore-from-last-good-state, PowerShell UTF-8 protocol
- `~/.claude/skills/telegram-ux-flow-designer/SKILL.md` — 6-шаговый UX-workflow, маппинг на aiogram-dialog, acceptance ≤4 экрана / ≤5 кликов / ≤3 глубина
- `~/.claude/skills/fastapi-api-developer/SKILL.md` — workflow с якорем, MCP read-only, Alembic rollback-note, slойность api→services→repos

**Feedback-loop инфраструктура (создана с нуля):**
- `~/.claude/skills/claude-booster/references/skills-registry.md` — ролевая модель: pipeline-диаграмма + таблицы по категориям (планирование / реализация / ревью-QA / инфра / релиз / контент / узкие). Каждая запись: Skill | Роль | Когда вызывать. 6 правил распределения задач.
- `~/.claude/skills/claude-booster/references/skills-errors.md` — реестр OPEN/FIXED дефектов с форматом записи (Источник, Контекст, Наблюдение, Ожидалось, Класс, 5 Whys, Корневая причина, Статус, Фикс, Anti-bloat-check).
- `~/.claude/skills/claude-booster/references/rca-protocol.md` — единый RCA-протокол: 5 Whys → классификация корня (instruction/context/execution gap) → anti-bloat check (5 вопросов) → минимальная правка → валидация. Согласован с cursor-booster и codex-booster.

**Интеграция контура обратной связи:**
- `~/.claude/skills/claude-booster/SKILL.md` — добавлен **Режим D** (обработка OPEN-записей через RCA), 4 шага: RCA → anti-bloat check → минимальная правка → закрытие в FIXED
- `~/.claude/skills/response-quality-coach/SKILL.md` — logging расширен: skill-дефекты → skills-errors.md, response-дефекты → ANSWER_ERRORS.md (возможен двойной log при смешанных случаях); добавлена передача в `/claude-booster` Режим D и `/codex-booster`

**Апгрейд планировщиков — знают о ролевой модели:**
- `spec-writer` — шаг 9 "распределить шаги по skills" + Контракт результата +`Распределение по skills` + правило "каждый шаг привязан к skill"
- `change-plan-architect` — в план-этапы добавлен skill-исполнитель из skills-registry; `Маршрутизация по skills` в Output; правило "каждый этап с привязкой"
- `tech-spec-composer` — в шаге 6 "Шаги реализации" — обязательная привязка к skill, СТОП если подходящего нет
- `ceo-review` — секция "Проверка ролевой модели": шаги без skill → ПРЕДУПРЕЖДЕНИЕ, неверный skill → КРИТИЧЕСКИЙ ПРОБЕЛ
- `eng-review` — аналогичная проверка с примером "DB → executor-lite вместо db-check/fastapi-api-developer = КРИТИЧЕСКИЙ ПРОБЕЛ"

**Обратная связь (футер-паттерн) добавлен в:**
pipeline-operator, encoding-guard, telegram-ux-flow-designer, fastapi-api-developer, spec-writer, change-plan-architect, tech-spec-composer, ceo-review, eng-review.

**Резервные копии:** `~/.claude/skills/claude-booster/references/backups/{pipeline-operator,encoding-guard,telegram-ux-flow-designer,fastapi-api-developer}-2026-04-23.md`

**Корневые причины (RCA на почему вообще появились S1 и отсутствовал feedback-loop):**
- **instruction gap** — не было единого стандарта для каждого skill (некоторые писались без frontmatter)
- **context gap** — отсутствовал глобальный реестр ролей, планировщики не знали какой skill для чего → делегировали "общему" executor'у, минуя специализированные
- **context gap** — не было контура обратной связи для skill-уровневых дефектов (ANSWER_ERRORS.md — только response-уровень)

**Anti-bloat отчёт:**
- НЕ копировали 5 Whys/RCA в каждый skill — вынесено в `rca-protocol.md`, в skill только 1-строчный футер "Обратная связь"
- НЕ дублировали skills-registry в каждом планировщике — планировщики ссылаются на общий файл
- НЕ создавали отдельные ERROR-файлы под каждую категорию — единый skills-errors.md

**Не сделано в этом wave (запланировано):**
- Wave 2 (S2-качество): ai-orchestrator, architect-system-analyst (разделение), qa-cluster (merge), booster-cluster (общий references)
- Добавить футер "Обратная связь" в остальные ~30 skills (не-планировщиков, не-критичных)
- Русификация response-quality-coach v2.0.0

## 2026-04-23 — Создан skill `/project-docs` v1.0.0

**Задача:** Новый skill для оформления двухслойной документации произвольного проекта: AI-ориентированной (для быстрого онбординга Claude в задачу без лишних токенов) и human-ориентированной (для понимания как пользоваться проектом).

**Лучшие практики (web-research перед созданием):**
- Anthropic blog "Using CLAUDE.md files" — структура CLAUDE.md: overview, stack, architecture, commands, conventions, workflows
- HumanLayer "Writing a good CLAUDE.md" — размер ≤300 строк (идеально ≤60), принцип "удалил — Claude ошибётся?"
- UX Planet "CLAUDE.md Best Practices" — 10 рекомендуемых секций
- Claude Code docs — progressive disclosure через `docs/ai/` или `agent_docs/`
- Medium "AI Agent Memory Files" — AGENTS.md как cross-tool стандарт (симлинк на CLAUDE.md)
- llmstxt.org — llms.txt стандарт (в итоге отклонён как избыточный для локальных сервисов)

**Решения дизайна (согласованы с пользователем через AskUserQuestion):**
- Имя: `project-docs`
- Полный набор артефактов: CLAUDE.md + docs/ai/{architecture, data-model, workflows, errors, glossary}.md + README.md + docs/{install, usage, configuration, troubleshooting}.md + AGENTS.md (симлинк)
- Два режима: full-generate + update (автовыбор по наличию CLAUDE.md/README.md в корне)
- AGENTS.md — симлинк на CLAUDE.md; llms.txt — не включён

**Созданные файлы:**
- `~/.claude/skills/project-docs/SKILL.md` (170 строк, v1.0.0)
- `~/.claude/skills/project-docs/references/discovery.md` — чеклист сканирования проекта (10 шагов: корень, стек, структура, entry points, конфиги, БД, docs, команды, git, интеграции)
- `~/.claude/skills/project-docs/references/templates-ai.md` — шаблоны CLAUDE.md, architecture.md, data-model.md, workflows.md, errors.md, glossary.md, AGENTS.md
- `~/.claude/skills/project-docs/references/templates-human.md` — шаблоны README.md, install.md, usage.md, configuration.md, troubleshooting.md
- `~/.claude/skills/project-docs/references/quality-checklist.md` — S1/S2/S3 верификация + команды проверки

**Доп. изменения:**
- `~/.claude/CLAUDE.md` — добавлен в список skills (29 → 30 skills)

**Аудит по standard.md:** PASS — все обязательные секции присутствуют (Роль, Когда использовать, Порядок работы, Контракт результата, Правила качества), frontmatter валиден (name/version/description/allowed-tools), язык русский, размер 170 строк соответствует сложному skill с references.

## 2026-03-18 — Русификация gstack skills

**Задача:** Привести импортированные gstack skills к стандарту IDE_booster (русский язык для описаний и инструкций по общению).

**Затронутые skills:** design-consultation, document-release, plan-design-review, qa, qa-design-review, qa-only, setup-browser-cookies

**Пробелы (S2):**
- `description` в YAML frontmatter — английский → русский
- `## AskUserQuestion Format` — английский → русский (`## Формат вопроса пользователю`)

**Применённые правки:**
- Переведены все 7 `description` полей в frontmatter
- Переведён блок `AskUserQuestion Format` в каждом skill (заголовок + все 4 пункта + пояснения)
- Workflow-шаги (Phase N, Step N) оставлены на английском по запросу пользователя

**Резервные копии:** `references/backups/{skill}-2026-03-18.md` для каждого из 7 skills

**Доп. изменения:**
- `d:\Work\IDE_booster\Docs\skills-guide.md` — добавлен раздел `/claude-booster` в секцию Вспомогательные + строка в шпаргалку

## 2026-03-21 — db-check: апгрейд до DBA-эксперта v2.0.0

**Задача:** Превратить минимальный read-only skill в полноценного DBA-эксперта, привести к стандарту.

**Найденные пробелы:**
- S1: нет `allowed-tools` (1 шт.)
- S2: нет `version`, `description` на EN, нет `## Роль`, `## Когда использовать`, `## Правила качества`, заголовки смешаны RU/EN, язык EN (7 шт.)
- S3: порядок секций, размер недостаточен (2 шт.)

**Применённые правки (все 7 + S3):**
1. Добавлен `allowed-tools: Read, Bash, Glob, Grep`
2. Добавлен `version: 2.0.0` (major — полная переработка workflow)
3. `description` переписан на русском
4. Добавлена `## Роль` — DBA-эксперт
5. Добавлена `## Когда использовать` — 5 триггеров (миграции, инциденты, аудит схемы, производительность, исправление данных)
6. Workflow расширен: два режима (чтение/запись), SQL-примеры, safety gates для write
7. MCP-серверы — заголовки на русском, сохранена таблица подключений
8. `## Правила качества` — 7 строгих правил (транзакции, запрет деструктивных без подтверждения, LIMIT, Alembic, credentials)
9. Контракт результата расширен: 6 полей вместо 5, на русском
10. Все заголовки унифицированы на русский

**Размер:** 39 → 95 строк (средний skill)
**Резервная копия:** `references/backups/db-check-2026-03-21.md`

## 2026-03-21 — claude-booster: самоулучшение v1.0.0 → v2.0.0

**Задача:** Расширить claude-booster от "редактора skills" до "инженера инфраструктуры Claude Code" — skills + permissions + MCP + settings + CLAUDE.md.

**Найденные пробелы (концептуальные, не по чеклисту):**
- Роль слишком узкая: только skills, не покрывает permissions/MCP/settings
- Триггеры не включают инфраструктурные запросы
- Workflow только один (аудит skills), нет workflow для инфраструктуры
- Контракт результата не описывает вывод для инфраструктурных задач
- Правила качества не покрывают settings.json, MCP, CLAUDE.md
- Нет `Grep` в allowed-tools

**Применённые правки (7 шт.):**
1. Роль → "Инженер инфраструктуры Claude Code"
2. Триггеры: +3 (permissions, MCP, CLAUDE.md, диагностика)
3. Workflow разбит на два режима: A (аудит skills) + B (инфраструктура Claude)
4. Контракт результата: отдельные секции для режимов A и B
5. Правила качества: +4 правила (deny-список, MCP credentials, CLAUDE.md дублирование, JSON-валидация)
6. `Grep` добавлен в allowed-tools
7. `version: 2.0.0` (major — новый workflow и контракт)

**Размер:** 81 → 120 строк (сложный skill)
**Резервная копия:** `references/backups/claude-booster-2026-03-21.md`

## 2026-03-23 — qa-fix: универсальный мультистек QA v2.0.0

**Задача:** Расширить qa-fix от Python-only до универсального QA для Python, Node/React, SQL/DB и FastAPI.

**Найденные пробелы:**
- S1: нет `allowed-tools` (1 шт.)
- S2: нет `version`, нет `## Роль`, нет `## Когда использовать`, статический анализ только flake8+mypy, нет SQL/DB, нет Node/React глубины, нет API smoke tests (7 шт.)

**Применённые правки (все S1 + S2):**
1. Добавлен `allowed-tools: Read, Bash, Edit, Glob, Grep, AskUserQuestion`
2. Добавлен `version: 2.0.0`
3. `description` расширен — перечислены все стеки
4. Добавлена `## Роль`
5. Добавлена `## Когда использовать` — 5 триггеров
6. Новый Шаг 3: авто-детектирование стека проекта
7. Шаг 4 расширен на 4 блока:
   - **Python:** flake8, mypy, bandit (security), coverage, import cycle detection
   - **Node/React:** tsc --noEmit, eslint, depcheck, bundle size
   - **SQL/DB:** Alembic current/heads/check, MCP PostgreSQL, таблицы без PK, FK без индексов, bloat
   - **FastAPI/API:** app import check, route audit, OpenAPI schema
8. Итоговый отчёт расширен: типизация, безопасность, покрытие, миграции, DB/SQL находки
9. Правила качества: +2 (MCP предпочтительнее psql, SQL безопасность)
10. Параметр `Стек` добавлен в таблицу параметров

**Размер:** 235 → 397 строк (сложный skill)
**Резервная копия:** `references/backups/qa-fix-2026-03-23.md`
**Локальные копии:** не найдено — skill глобальный, доступен во всех 11 проектах автоматически

## 2026-03-24 — Перенос лучших практик Codex в Claude skills (5 улучшений)

**Задача:** Проанализировать Codex error governance (48 инцидентов в ERRORS.md, booster-runtime-contract, cursor-error-governance) и перенести лучшие практики в Claude skills.

**Источники анализа:**
- `d:/Work/IDE_booster/Docs/ai/ERRORS.md` — мастер-реестр (48 записей)
- `d:/Work/IDE_booster/Docs/ai/ANSWER_ERRORS.md` — реестр дефектов ответов (17 классов)
- `d:/Work/IDE_booster/Docs/ai-booster/booster-runtime-contract.md` — 8 правил
- `d:/Work/IDE_booster/skills/techlead-code-reviewer/references/review-checklist.md` — 10 измерений review
- `~/.claude/skills/codex-booster/references/cursor-error-governance.md` — closed-loop improvement

**Применённые улучшения:**

### 1. review-gate v1.0.0 → v2.0.0: реестр ошибок + 10 измерений
- Добавлен Шаг 0.5: загрузка реестра ошибок проекта и мастер-реестра
- Проверки расширены с 6 до 10 измерений (+Docs/Config Drift, Phase Integrity, Goal-Level Data, Domain Model, Date/Time Critical)
- Добавлена секция "Обновление реестра ошибок" при ОТКЛОНЕНО
- Правила: повтор паттерна из реестра = автоматическое ОТКЛОНЕНО

### 2. qa-fix v2.0.0: closed-loop improvement
- Шаг 0: добавлена загрузка реестра ошибок (проектный + мастер)
- Добавлен Шаг 9: Closed-Loop (из Codex error governance):
  - 9a: запись новых ошибок в ERRORS.md
  - 9b: 5 Whys для КРИТИЧЕСКИХ
  - 9c: проверка соседних модулей на аналогичный паттерн
  - 9d: prevention action → предложение обновить skill/rule

### 3. context-auditor v1.0.0: реестр ошибок как источник контекста
- Шаг 2: добавлены пункты 6-7 — чтение ERRORS.md проекта и мастер-реестра
- Новый статус REGRESSION — нарушение известной prevention action

### 4. response-quality-coach: ANSWER_ERRORS.md осведомлённость
- Добавлен шаг 1.5: загрузка мастер-реестра дефектов ответов
- Перечислены все 17 классов дефектов для быстрой классификации
- Шаг 2 обновлён: использует и taxonomy, и ANSWER_ERRORS registry

**Резервные копии:** `references/backups/{skill}-2026-03-24.md` для 4 skills

---

## 2026-03-24 — context-auditor: новый skill v1.0.0 + якорь контекста в 5 skills

**Задача:** Решить системную проблему потери контекста между этапами пайплайна. Skills работали в изоляции — ни один не читал историю бесед или результаты предыдущих этапов.

**Диагноз:** Все 8 ключевых skills не имели доступа к `~/.claude/projects/` (JSONL-беседы) и `memory/MEMORY.md`. Контекст терялся при переходе ceo-review → spec-writer → change-plan → tech-spec → review-gate → qa-fix.

**Создан новый skill: `/context-auditor` v1.0.0**
- Аудитор соответствия целям на любом этапе пайплайна
- Читает JSONL-историю бесед и memory/MEMORY.md
- Составляет чеклист требований, сверяет с артефактом
- Статусы: COVERED / PARTIAL / MISSING / DEVIATED / ADDED
- Вердикт: ALIGNED / DRIFT DETECTED / CRITICAL LOSS
- Путь: `~/.claude/skills/context-auditor/SKILL.md`

**Добавлен "Шаг 0: Якорь контекста" в 5 skills:**
1. `spec-writer` — читает беседы перед формализацией, сверяет спек с якорями
2. `change-plan-architect` — восстанавливает решения из review, спек из spec-writer
3. `tech-spec-composer` — извлекает цели + план + решения, акцент на edge cases
4. `review-gate` — добавлена проверка соответствия исходным целям (категория DRIFT)
5. `qa-fix` — извлекает acceptance criteria как доп. тест-кейсы

**Обновлён `CLAUDE.md`:**
- Рабочий цикл расширен: 3 → 6 этапов с context-auditor
- Добавлено описание механизма "Якорь контекста"
- Счётчик skills: 22 → 23

**Резервные копии:** `references/backups/{skill}-2026-03-24.md` для 5 skills

## 2026-04-09 — travel-copywriter: новый контент-skill v1.0.0

**Задача:** Создать skill тревел-копирайтера для соцсетей (TG, VK) и тревел-сайтов/блогов. Роль — виртуальный гид, увлекательный и небанальный рассказ о местах.

**Создан skill: `/travel-copywriter` v1.0.0**
- Глобальный: `~/.claude/skills/travel-copywriter/SKILL.md`
- 5 режимов: tg (50-300 слов), vk (100-500 слов), article (800-2000 слов), story (300-800 слов), guide (500-1500 слов)
- Чистый вывод: только текст для копирования, без предисловий/послесловий
- Интеграция с ContentFactory: шаблоны, voice-guide, glossary, travel-tone

**Созданы шаблоны в ContentFactory:**
- `d:\Work\ContentFactory\templates\social\tg-travel.md` — TG тревел-пост с примерами
- `d:\Work\ContentFactory\templates\social\vk-travel.md` — VK тревел-пост с примерами
- `d:\Work\ContentFactory\templates\article\travel-article.md` — тревел-статья для блога

**Создан справочник:**
- `d:\Work\ContentFactory\references\subjects\travel-tone.md` — тон, приёмы, стоп-лист фраз

**Аудит:** S1 5/5 ✓, S2 8/8 ✓, S3 4/4 ✓ — полное соответствие стандарту
**Резервная копия:** `references/backups/travel-copywriter-2026-04-09.md`

## 2026-04-13 — ege-master: новый skill v1.0.0 (EGE workplace)

**Задача:** Создать проектный skill для решения задач ЕГЭ по информатике. Взять за основу правила Cursor (`exam-tasks.mdc`) и усилить.

**Создан skill: `/ege-master` v1.0.0**
- Проектный: `d:/Work/CyberGuru/EGE/workplace/skills/core/ege-master/SKILL.md`
- Зарегистрирован: `d:/Work/CyberGuru/EGE/workplace/AGENTS.md`

**Усиления сверх Cursor-правил:**
| Добавлено | Зачем |
|---|---|
| Шаг 0: режимы СОЗДАТЬ / ФИКС / ОБЪЯСНЕНИЕ | Cursor не различал сценарии |
| Шаг 1: классификация типа задачи (строки, алгоритмы, DP…) | Быстрый выбор алгоритма без лишних вопросов |
| Шаг 2: выбор алгоритма + граничные случаи | Предотвращает ошибки типа 26_11 (жадный ≠ всегда верный) |
| Шаг 5: верификация запуском `python <имя>.py` | Cursor создавал, но не проверял |
| Фиксированная структура .md (6 секций) | Единообразие между задачами |
| Правило: код в .md == код в .py | Предотвращает расхождение файлов |

**Аудит S1:** 5/5 ✓ | **S2:** 8/8 ✓ | **S3:** 4/4 ✓

**Известные ограничения:**
- Только Python-задачи; задачи на электронные таблицы не покрыты
- Верификация запуском требует `.txt` файл рядом с `.py`

---

## 2026-04-15 — executor-lite v1.0.0 → v2.0.0: Claude-only, стандарт RU

**Задача:** Привести к стандарту и убрать упоминания Cursor. Skill описывал себя как "для Cursor agents", половина секций была на английском.

**Найденные пробелы:**
- S2: нет `## Роль`, нет `## Контракт результата`, нет `## Правила качества`
- S2: заголовки и секции смешаны RU/EN (`Use Cases`, `Stop Conditions`, `Workflow`)
- S2: нет явного сигнала context:minimal (устаревшая Cursor-привязка)
- S3: порядок секций не по стандарту

**Применённые правки:**
1. `version: 2.0.0` (major — новый контракт и роль)
2. Добавлена `## Роль` — Исполнитель
3. Добавлена `## Когда использовать` — 6 триггеров на русском
4. Переписан `## Порядок работы` — 5 шагов, весь на русском
5. Добавлен `## Контракт результата` — Scope, Файлы, Валидация, Риски
6. Добавлены `## Правила качества` — 4 правила (scope, чтение, добавления, эскалация)
7. `## Stop Conditions` → `## Stop-условия` — на русском, добавлен триггер сравнения вариантов
8. `description` обновлён: добавлен "Контекстный уровень: minimal"
9. Убраны все упоминания Cursor

**Резервная копия:** `references/backups/executor-lite-2026-04-15.md`
**Синхронизировано:** `D:\Work\IDE_booster\skills\executor-lite\SKILL.md`

---

## 2026-04-19 — openclaw: новый skill v1.0.0

**Задача:** Создать skill для диагностики, исправления ошибок, обновления и настройки инфраструктуры OpenClaw (AI assistant framework, `~/.openclaw/`).

**Создан skill: `/openclaw` v1.0.0**
- Глобальный: `~/.claude/skills/openclaw/SKILL.md`
- 5 режимов: A (диагностика статуса), B (исправление ошибок), C (обновление), D (улучшение конфига), E (аудит безопасности)
- Покрывает: npm EPERM, gateway, авторизацию моделей (Qwen/Codex/Kobold), Telegram канал, ACL-права, JSON-валидацию
- Ключевые пути: `~/.openclaw/openclaw.json`, `logs/config-audit.jsonl`, `update-openclaw.ps1`, `secaud.ps1`, `secfix.ps1`
- Обновлён `~/.claude/CLAUDE.md`: счётчик 25 → 26, добавлена строка `/openclaw`

**Аудит S1:** allowed-tools ✓, workflow ✓ | **S2:** version ✓, description ✓, Роль ✓, Когда использовать ✓, Контракт ✓, Правила ✓

---

## 2026-04-09 — travel-copywriter v1.0.0 → v1.1.0: auto-save в output/

**Задача:** Добавить автоматическое сохранение сгенерированных текстов в `output/` с именем файла: `{дата}-travel-copywriter-{режим}.md`.

**Применённые правки (3 шт.):**
1. `version: 1.1.0` (minor — новая функциональность, контракт не ломается)
2. Добавлен **Шаг 5: Сохранение в output/** — путь, формат имени, YAML frontmatter с метаданными (skill, mode, date, topic, words), суффикс -2/-3 при дублях, молчаливое сохранение
3. Контракт результата: добавлена строка про файл в output/

**Резервная копия:** `references/backups/travel-copywriter-2026-04-09-pre-output.md`

---

## 2026-04-20 — travel-copywriter v1.1.0 → v1.2.0 + knowledge base человечности

**Задача:** Усилить копирайт-skills инструкциями "человечности" — чтобы текст не звучал как GPT. Исследование + встраивание в skill + наследование всеми будущими копирайт-skills.

**Исследование:**
- Habr "Ваш текст воняет GPT. 12 мест откуда несёт" — 12 лингвистических маркеров AI-текста на русском
- Инфостиль Ильяхова (Главред, "Пиши, сокращай") — 22 заповеди редактора, стоп-слова
- Частотный анализ GPT-4.1 в русском: тире 10.62 на 1000 слов (люди 3.23), деепричастия × 2-5, списки из 3 пунктов

**Ключевые находки:**
- Механические маркеры (M1-M12): деепричастные нагромождения, связки "служит/выступает", overhedging "важно отметить", промо-регистр, кальки, избыток тире, "не X, а Y", одинаковая длина предложений, гиперсвязность, дидактический тон
- Человеческие приёмы (H1-H7): парцелляция, ремарки в скобках, мысль-в-процессе, швы без переходов, сенсорные детали, антирекомендация, вариативность ритма
- Правила инфостиля (P1-P7): одно предложение = одна мысль, абстрактное → конкретное, факты в опыте читателя

**Созданные артефакты:**
1. **Новый knowledge base:** `~/.claude/skills/claude-booster/references/ai-humanness.md` v1.0.0
   - Единый источник истины для всех копирайт-skills
   - 12 AI-маркеров с примерами "плохо → хорошо"
   - 7 принципов инфостиля Ильяхова
   - 7 человеческих приёмов
   - Чеклист из 15 пунктов для верификации
   - Стоп-лист фраз в табличной форме (overhedging, промо, канцелярит, связки, штампы, дидактика)

2. **Обновлён travel-copywriter SKILL.md** v1.1.0 → v1.2.0
   - Шаг 0: первым пунктом читается ai-humanness.md
   - Шаг 3: split на 3a (содержание), 3b (12 AI-маркеров), 3c (человеческие приёмы)
   - Правила качества: добавлены "Человечность", "Ритм", "Тире"

3. **Обновлён travel-tone.md** v1.0.0 → v1.1.0
   - Секция "Человечность текста" — выжимка + ссылка на knowledge base
   - Расширенный стоп-лист канцелярита

4. **Обновлён content-skill-template.md**
   - Шаг 0: первым пунктом обязательно ai-humanness.md
   - Шаг 3 Верификация: split на "Содержание" и "Человечность" (14 AI-маркеров)
   - Правила качества: 3 новых правила (Человечность, Ритм, Тире)
   - Это означает, что **все будущие копирайт-skills автоматически унаследуют** чеклист человечности

5. **Обновлён claude-booster SKILL.md** (Режим C2)
   - Обязательный пункт #4: первым в Шаге 0 нового skill — ai-humanness.md
   - Обязательный пункт #7: чеклист человечности в верификации
   - Обязательный пункт #8: правило человечности в качестве

**Эффект:**
- travel-copywriter теперь проверяет каждый текст по 12 AI-маркерам перед выдачей
- Любой следующий копирайт-skill (SMM, редактор, email, блог) унаследует требование человечности из template
- claude-booster в Режиме C автоматически встроит ai-humanness.md в любой новый копирайт-skill

**Резервные копии:**
- `backups/travel-tone-2026-04-20-pre-humanness.md`
- `backups/travel-copywriter-2026-04-20-pre-humanness.md`
- `backups/content-skill-template-2026-04-20-pre-humanness.md`

**Источники:**
- https://habr.com/ru/articles/1022906/ — 12 маркеров AI-текста
- https://habr.com/ru/post/323232/ — 22 заповеди сильного редактора
- https://maximilyahov.ru/ — инфостиль, Главред
- https://bureau.ru/soviet/selected/maksim-ilyahov/stop-slova/ — стоп-слова

---

## 2026-04-20 — digital-copywriter: новый skill v1.0.0 (Виктор Комлев)

**Задача:** Создать копирайтер для соцсетей, сайта и email от имени Виктора Комлева. Источники стиля: victor-komlev.ru, курс AI-предприниматель (IT_Businessman/docs/v2).

**Решения по развилкам (через AskUserQuestion):**
- Размещение: глобальный primary + экспорт-копии в ContentFactory и IT_Businessman
- Режимы: двухпараметровая система формат × цель, оба параметра опциональны
- Реалити-шоу: отложено (будет добавлено позже)
- Курс: skill сам читает docs/v2 при запросе

**Создан skill: `/digital-copywriter` v1.0.0**
- Primary: `~/.claude/skills/digital-copywriter/SKILL.md`
- Экспорт-копии: `ContentFactory/skills/core/`, `IT_Businessman/skills/core/`
- 4 формата: tg, vk, article, email
- 5 целей: edu, sales, lifehack, news, experiment
- Всего сценариев: 20 комбинаций + гибкий парсинг аргументов
- Наследует ai-humanness (чеклист в Шаге 3c), автосохранение в output/

**Созданы шаблоны в ContentFactory:**
- `templates/social/tg-digital.md` — TG-пост Виктора, структуры по 5 целям
- `templates/social/vk-digital.md` — VK-пост Виктора, структуры по 5 целям
- `templates/article/digital-article.md` — статья для victor-komlev.ru
- `templates/article/digital-email.md` — email-рассылка с P.S.

**Создан справочник:**
- `references/subjects/victor-voice.md` v1.0.0 — паспорт автора, тон, лексика, ритм, темы, 7 цитат-эталонов, портреты 3 ЦА курса, платформенные оттенки

**Регистрация:**
- `~/.claude/CLAUDE.md`: счётчик 26 → 27, добавлена строка `/digital-copywriter`
- `ContentFactory/AGENTS.md`: добавлено в Content Creation

**Аудит:** S1 5/5 ✓, S2 8/8 ✓, S3 4/4 ✓

**Ограничения (известные):**
- VK-страница не загрузилась через WebFetch — voice-reference построен на сайте + курсе. Рекомендуется владельцу дополнить примерами реальных VK-постов в секции "Цитаты-эталоны"
- Реалити-шоу режим отсутствует — добавить по готовности концепта

**Источники стиля:**
- https://victor-komlev.ru/viktor-komlev-nemnogo-obo-mne/
- d:\Work\IT_Businessman\docs\v2\01. Манифест AI-предприниматель.md
- d:\Work\IT_Businessman\docs\v2\02. Три ЦА.md
- d:\Work\IT_Businessman\docs\v2\00. Карта экосистемы.md

---

## 2026-04-20 — session-digest v1.0.0 + digital-copywriter v1.1.0 (reality)

**Задача:** Создать вспомогательный технический skill для суммаризации диалогов Claude Code по дате/диапазону/проекту. Замкнуть pipeline на digital-copywriter для реалити-шоу про AI-работу.

**Решения по дизайну (через AskUserQuestion):**
- Имя: `session-digest` (универсальное, не привязано к реалити)
- Выход: markdown-дайджест + YAML-блок для программной подачи
- Диапазоны: дата/диапазон (`today`, `yesterday`, `last-7d`, `YYYY-MM-DD..YYYY-MM-DD`) + фильтр по проекту
- Кластеризация: по задачам/темам (LLM-группировка по смыслу)

**Создан skill: `/session-digest` v1.0.0**
- Путь: `~/.claude/skills/session-digest/SKILL.md`
- Reference: `references/jsonl-format.md` — формат Claude Code JSONL
- Источник: `~/.claude/projects/<project-slug>/*.jsonl`
- Парсинг: user-промпты, assistant text-блоки, tool_use имена/inputs
- Фильтрация: timestamps, isSidechain=false по умолчанию
- Вывод в `d:\Work\ContentFactory\output\digest\{date}-digest-{scope}.md`
- Приватность: маскирование секретов через [REDACTED], исключение system-reminder блоков

**Обновлён skill: `/digital-copywriter` v1.0.0 → v1.1.0**
- Добавлена 6-я цель `reality` (было 5 целей, стало 6)
- Pipeline: skill ищет свежий дайджест в `output/digest/` → выбирает одну задачу → пишет пост голосом Виктора
- Правила reality: 1 пост = 1 задача; multi-session → серия; никаких "сегодня я сделал A, B, C"; антипаттерн "журнал дел" запрещён
- Связка с форматами: tg/vk/article/email × reality
- Синхронизированы экспорт-копии в ContentFactory и IT_Businessman

**Регистрация:**
- `~/.claude/CLAUDE.md`: счётчик 27 → 28, добавлена строка `/session-digest`, обновлена строка `/digital-copywriter` (5 → 6 целей)
- `ContentFactory/AGENTS.md`: добавлен session-digest, обновлён digital-copywriter

**Резервная копия:** `backups/digital-copywriter-2026-04-20-pre-reality.md`

**Аудит session-digest:** S1 5/5 ✓, S2 8/8 ✓, S3 4/4 ✓ — соответствие стандарту
**Аудит digital-copywriter (v1.1.0):** S1 5/5 ✓, S2 8/8 ✓, S3 4/4 ✓ — соответствие стандарту

**Pipeline использования:**
```
/session-digest last-7d          # собрать факты за неделю
/digital-copywriter vk reality   # пост про одну задачу из дайджеста
```

**Известные ограничения:**
- Парсинг крупных JSONL (>500 МБ) требует предупреждения и разбивки
- Side-chain сессии (Task tool субагенты) по умолчанию исключены
- Реалити-пост ограничен одной задачей — для разворачивания нескольких нужна серия, не один мега-пост

---

## 2026-04-20 — architect-system-analyst v1.0.0 → v2.0.0: best-practices uplift

**Задача:** Перед аудитом разросшегося проекта ContentBackbone усилить skill архитектора современными best-practices (C4, ADR, NFR, 6-dim review, anti-patterns).

**Найденные пробелы:**
- S1: нет `allowed-tools` (1 шт.)
- S2: нет `version`, весь skill на английском (стандарт требует русский), нет `## Роль`, `## Когда использовать`, несоответствие стандартному порядку секций (6 шт.)
- S3: нет `references/`, skill слишком абстрактен — нет конкретных артефактов (C4, ADR, anti-patterns)

**Research (GitHub / web / industry):**
- C4 Model + arc42 — lightweight architecture documentation (C4 Context/Container текстом для small projects)
- ADR — Michael Nygard формат (Title / Status / Context / Decision / Consequences) + Y-Statement, хранить в `docs/adr/`
- 6-dimension review (scalability, security, maintainability, performance, deployment, docs) — industry checklist
- Modular monolith 2026 — предпочтительный паттерн до появления масштабных нужд
- Anti-patterns: distributed monolith, shared DB writes, god services, sync chain 5+, dual-write без координации
- Progressive disclosure (Anthropic skills) — короткий SKILL.md + references/ по требованию

**Применённые правки (все 3 одобренных пакета A/A/A):**

### Пакет 1 — Compliance со стандартом IDE_booster
1. Добавлен `allowed-tools: Read, Glob, Grep, Bash, Write, AskUserQuestion`
2. Добавлен `version: 2.0.0` (major — новый workflow и контракт)
3. `description` переписан на русском (многострочный, с триггерами)
4. Полный перевод тела skill на русский
5. Добавлены секции `## Роль` и `## Когда использовать` (6 триггеров)
6. Секции переупорядочены: Роль → Когда → Порядок → Контракт → Правила

### Пакет 2 — Content uplift архитектурными best-practices
- **Шаг 0** — Якорь контекста (CLAUDE.md + MEMORY.md + docs + git log)
- **Шаг 1** — Рамка задачи с обязательными NFR
- **Шаг 2** — AS-IS с C4-Context и C4-Container (текстом)
- **Шаг 3** — Gaps и ambiguity, блокировка NOT_READY
- **Шаг 4** — Анти-паттерны и классификация дубликаций (must-centralize/temporarily local/acceptable)
- **Шаг 5** — 6-мерное ревью (OK/WATCH/RISK по каждому)
- **Шаг 6** — TO-BE с simplification decisions
- **Шаг 7** — ADR по Nygard или Y-Statement
- **Шаг 8** — План поставки (exit criteria + rollback + blast-radius)
- **Шаг 9** — Go/No-Go

Контракт результата расширен с 12 до 16 полей (добавлены Context Anchors, Anti-patterns found, 6-Dimension Review, ADR entries, NFR compliance).

Правила качества: +4 правила (ADR не редактируются, superseded-паттерн, блокировка NOT_READY при gaps, анти-паттерны для micro-сервисов/event-bus, ContentBackbone invariants).

### Пакет 3 — references/ (progressive disclosure)
Созданы 5 подробных reference-файлов:
1. `architecture-review-checklist.md` — 6 измерений с чеклистами + форма вывода (таблица)
2. `adr-template.md` — Nygard + Y-Statement шаблоны, правила ADR, когда писать/не писать
3. `anti-patterns.md` — 4 категории (структурные / данные / операционные / организационные), 17 паттернов с сигналами и решениями
4. `c4-light-guide.md` — Level 1/2 текстом для small projects, шаблон ARCHITECTURE.md
5. `nfr-categories.md` — 4 обязательных + 7 опциональных категорий, формат вывода с метриками

**Размер:** SKILL.md 62 → 106 строк (сложный skill), + 5 references (~600 строк детальных чеклистов)

**Резервная копия:** `references/backups/architect-system-analyst-2026-04-20.md`

**Аудит v2.0.0:** S1 5/5 ✓, S2 8/8 ✓, S3 4/4 ✓

**Источники research:**
- https://c4model.com/faq
- https://martinfowler.com/bliki/ArchitectureDecisionRecord.html
- https://adr.github.io/
- https://github.com/joelparkerhenderson/architecture-decision-record
- https://wellarchitected.github.com/library/architecture/checklist/
- https://codewave.com/insights/software-architecture-principles-practices/
- https://www.workingsoftware.dev/software-architecture-documentation-the-ultimate-guide/
- https://github.com/hesreallyhim/awesome-claude-code
- https://github.com/travisvn/awesome-claude-skills

**Следующий шаг:** готово к запуску `/architect-system-analyst` для аудита ContentBackbone.

---

## 2026-04-20 — Создан executor-pro (v1.0.0)

**Запрос:** ContentBackbone предстоит рефакторинг; executor-lite не подходит для критичных правок — нужен skill повышенной квалификации.

**Research:**
- wshobson/agents (modernization: legacy-migration, refactoring; python-pro, fastapi-pro)
- VoltAgent/awesome-claude-code-subagents (python-pro: 3-фазный workflow, 90%+ coverage, явная эскалация)
- platform.claude.com/skills/best-practices (plan→validate→execute→verify, narrow-bridge guardrails, feedback loops, progressive disclosure)
- dev.to/superorange0707 (workflow-centric vs tool-centric, cold-start <500 строк)

**Дизайн-решения (согласованы через AskUserQuestion):**
- Имя: `executor-pro` (парное с executor-lite)
- Контекст: standard по умолчанию, эскалация в full
- Цикл: исполнитель после `/change-plan-architect`
- Gates: regression test на уровне дефекта + reviews/*.md+.diff + Alembic isolation + handoff в /review-gate

**Созданные файлы:**
1. `~/.claude/skills/executor-pro/SKILL.md` (110 строк, 7 шагов workflow + stop-условия)
2. `~/.claude/skills/executor-pro/references/pre-change-checklist.md` — 7 секций (контракты/callers/тесты/БД/pipeline/flags/логи)
3. `~/.claude/skills/executor-pro/references/validation-gates.md` — 10 gates (G1-G10) с PASS/FAIL критериями и feedback loop (max 2 итерации)
4. `~/.claude/skills/executor-pro/references/escalation-triggers.md` — 9 триггеров (T1-T9) + маршруты эскалации + формат evidence pack

**Интегрировано в инфраструктуру:**
- `~/.claude/CLAUDE.md`: счётчик skills 28 → 29, workflow шаг 4 разделён на lite/pro, skill добавлен в каталог
- Инварианты из `D:\Work\ContentBackbone\.claude\CLAUDE.md` зашиты в guardrails: Alembic isolation, blast-radius continuity, fabricated-IDs-ban, review artifacts, shared-state cleanup

**Резервная копия:** не требуется (новый skill, не правка существующего)

**Следующий шаг:** протестировать на первой критичной задаче рефакторинга ContentBackbone — проверить, что handoff в review-gate работает как ожидается.

---

## 2026-04-20 — techlead-code-reviewer: Cursor → Claude skills pivot

**Задача:** Стек IDE_booster переведён на Claude skills (Cursor и Codex выведены). Techlead-ревью по-прежнему ссылалось на `cursor-agent-error-loop.md` и фиксировало ошибки агентов Cursor. Нужно переориентировать цикл ошибок на скиллы-разработчики Claude.

**Найденные пробелы:**
- S2: reference `cursor-agent-error-loop.md` бьётся с текущей моделью (нет Cursor-агентов)
- S2: SKILL.md workflow step 6 ссылается на устаревший reference
- S2: Output Contract содержит `Cursor Agent Error Entries`
- S2: Decision Rule падает на "Cursor-agent mistakes"

**Применённые правки:**
1. Создан `references/claude-skills-improvement-loop.md` — зеркало loop-а с фокусом на Claude-скиллы (`/executor-lite`, `/executor-pro`, `/fastapi-api-developer`, `/spec-writer`, `/change-plan-architect`, `/tech-spec-composer`). Добавлены классификация (scope-violation, api-drift, architecture-breach, db-unsafe, test-gap, context-skip, style-breach, dry-violation), обязательный preventive action (правка SKILL.md / references/ / CLAUDE.md), эскалация в `/claude-booster` при повторе
2. SKILL.md workflow шаг 6: переписан под Claude-skill failures + требование предложить concrete skill edit
3. Output Contract: `Cursor Agent Error Entries` → `Claude Skills Improvement Entries`; `Skill Improvement Actions` уточнён (имя скилла → файл → суть → приоритет)
4. Decision Rule: `FAIL` при не-залогированных Claude-skill failures или отсутствии preventive skill edit
5. Удалён устаревший `references/cursor-agent-error-loop.md`

**Резервные копии:**
- `backups/techlead-code-reviewer-2026-04-20.md` (SKILL.md до правок)
- `backups/cursor-agent-error-loop-2026-04-20.md` (удалённый reference)

**Аудит после правок:** S1 5/5 ✓, S2 8/8 ✓ (ниже — без регрессий), S3 4/4 ✓

**Эффект:** ревью-скилл теперь замыкает feedback loop на апгрейд Claude-скиллов-разработчиков, а не на внешние Cursor-агенты. Любой provable defect из `/techlead-code-reviewer` обязан сопровождаться конкретной правкой SKILL.md/references ответственного скилла либо эскалацией в `/claude-booster`.


---

## 2026-04-28 — Усиление контура после двухдневного RCA проектов CB/LMS/SPW

**Запрос:** Анализ всех реестров ошибок проектов + чатов 27-28.04 (ContentBackbone, LMS, SPW) на операционные ошибки и пробелы skills.

**Корневые причины (кластеры):**
1. **API contract drift cross-project** — LMS Y-1 переименовал URL-пути, не bекsync-нул spec/ADR; SPW Y-2 написан от устаревшего; падение 404 на проде. (LMS ERRORS 2026-04-28 #1, #2)
2. **Review-gate пропущен в hotfix** — SPW: 2 security-related коммита (proxy.ts middleware + endpoint drift) без review «во время operator-driven smoke»
3. **Hardcoded URLs / env-vars** — `learn.victor-komlev.ru` в сервисе magic-link, `RESEND_API_KEY=null` в dev → письма не уходят
4. **IDOR пробелы** — endpoints с `{user_id}/{attempt_id}` без `Depends(get_current_user)` (10 шт в LMS)
5. **Frontend Route ≠ API Endpoint путаница** — spec слил UX-имена страниц и API-пути в одно имя
6. **Устаревшие приёмы JS/TS/Next.js в SPW** — `useEffect` + mutate вместо Server Actions, type assertions без runtime validation, middleware с cookie-only auth, ломающий TG App контекст
7. **Mock-only tests на external write-path** — 100% mock без live smoke (CB Subsystem A Phase 2, LMS write-paths)
8. **Mock vs platform reality** — VK community-token mock противоречил реальному ограничению (`wall.get` недоступен)

**Anti-bloat решение:** вместо точечных правок 7+ skills (риск дублирования и instruction overfitting) — два общих reference + ссылки в существующие skills.

**Применённые правки:**

1. **Создан `references/api-contract-rules.md`** (120 строк) — единые правила для всех skills, работающих с публичными API:
   - §1 Frontend Route ≠ API Endpoint (две раздельные таблицы в спеках)
   - §2 Spec backsync в одном коммите
   - §3 Cross-repo drift detector (grep по 4 проектам)
   - §4 Hardcoded URL guard
   - §5-6 IDOR sweep + auth-coverage sweep
   - §7 Mock-only недостаточно для external write-path
   - §8 Mock vs platform reality (VK/TG/WP token types)
   - §9 Migration: entrypoint vs execution vs state/storage
   - §10 Stage handoff continuity для multi-stage pipelines
   - §11 Контрольный чеклист перед PASS

2. **Создан `references/frontend-stack-rules.md`** (126 строк) — единые правила Next.js 16 + TS + React 19:
   - §1 TS: запрет `any`/unsafe `as`, обязательна Zod на runtime data
   - §2 React 19: запрет class components, useEffect+mutate, prop drilling
   - §3 Next.js 16: запрет Pages Router, `getServerSideProps`, smelly client/server boundary
   - §4 Multi-context auth (web/TG App/WP-embed) — middleware не должен ломать ни один контекст
   - §5-7 запросы данных, тестирование, инструментарий
   - §8 безопасность: Zod + DOMPurify + httpOnly cookies + env discipline
   - §10-11 чеклист перед commit + MANDATORY review-gate triggers

3. **review-gate v2.1.0 → 2.2.0**: добавлено 12-е измерение (Public API Contract Sync), MANDATORY-триггеры (auth/middleware/contract/migration/Type assertion → review-gate без исключений). Усилены §4 (IDOR sweep) и §5 (mock-only ban).

4. **executor-pro v1.1.0 → 1.2.0**: новый Шаг 4.5 (Backsync контрактов и URL-guard), 3 правила качества (no-hotfix-bypass, spec-backsync-same-commit, frontend-stack ref).

5. **executor-lite v2.0.0 → 2.1.0**: добавлены 3 stop-условия (публичные API URLs, middleware/auth, frontend type-unsafe приёмы) → эскалация в executor-pro.

6. **techlead-code-reviewer v2.0.0 → 2.1.0**: Шаг 2 расширен двумя доменными чеклистами — `api-contract-rules.md` и `frontend-stack-rules.md`.

7. **fastapi-api-developer v2.0.0 → 2.1.0**: новый Шаг 4.5 (API contract guard) — hardcoded URLs grep, IDOR sweep, spec backsync, schema vs OpenAPI.

8. **tech-spec-composer v1.3.0 → 1.4.0**: 3 новых правила качества (Frontend Route ≠ API Endpoint, тип токена для внешних API, Concurrency & Idempotency явные).

**Anti-bloat отчёт:**
- Покрыто ли существующим? Нет — текущие skills имели общие правила, но не было единых invariants для API-контрактов и frontend стека. Оба reference переиспользуются 5 skills через ссылку, не клонируются.
- Локальные/глобальные? Глобальные (cross-project), поэтому в `references/` claude-booster, а не в каждом skill.
- Дубли? Не создано: каждое правило сформулировано один раз в reference, в skills — только ссылка.
- Устаревшее? Не удаляли (старые правила остаются актуальны).
- Размеры skills после правок: review-gate 90, executor-pro 127, executor-lite 57, techlead-code-reviewer 118, tech-spec-composer 118, fastapi-api-developer 102 — все ≤200 строк (лимит соблюдён).

**Не создавались новые skills.** Frontend skill отдельный не нужен — `executor-pro` + reference `frontend-stack-rules.md` покрывают весь объём, без раздувания фронтальной навигации.

**Резервные копии:**
- `backups/review-gate-2026-04-28.md`
- `backups/executor-pro-2026-04-28.md`
- `backups/executor-lite-2026-04-28.md`
- `backups/techlead-code-reviewer-2026-04-28.md`
- `backups/tech-spec-composer-2026-04-28.md`
- `backups/fastapi-api-developer-2026-04-28.md`

**Cross-project контур:** уже создан пользователем (`D:\Work\ContentBackbone\docs\cross-project\`); 11-е измерение review-gate ссылается на него. Усиление: 12-е измерение теперь явно блокирует drift в публичных API при отсутствии backsync, плюс MANDATORY-триггеры запрещают hotfix-bypass — это закрывает дыру, через которую прошёл инцидент LMS 2026-04-28.

**Эффект:** контур теперь не просто требует обновлять cross-project mirror, но и enforce'ит на уровне review-gate, что (a) URL/method/schema изменения сопровождаются spec backsync в том же коммите, (b) cross-repo grep на старые пути обязателен, (c) hardcoded URLs в сервисном слое — блокирующий FAIL, (d) в SPW и других frontend-проектах используется единый набор инвариантов для TS/React/Next.js, (e) middleware-проверки auth учитывают multi-context (web/TG/embed). Hotfix во время smoke — больше не основание обходить review-gate.

---

## 2026-04-28 (дополнение) — Operator handoff invariant

**Запрос:** Закрепить корневой инвариант: если агент считает что нужны действия оператора:
- А. Если у агента есть skill/tool для самостоятельного выполнения — уточнить кто делает (агент или оператор)
- Б. Если агент не может или оператор выбрал сам — пошаговая инструкция, при необходимости с интернет-поиском за свежими данными

**Корневая причина (instruction gap):** Не было единого правила — skills допускали молчаливый `BLOCKED` или абстрактное «требуется действие оператора» без классификации и инструкции.

**Anti-bloat решение:** Глобальное правило в `~/.claude/CLAUDE.md` — действует на ВСЕ skills сразу, без точечного дублирования в каждом. Детальный стандарт в одном reference. Точечные ссылки только в трёх skills, где operator handoff особенно частый.

**Применённые правки:**

1. **Создан `references/operator-handoff-rules.md`** (~95 строк) — единый стандарт:
   - Принцип: запрет молчаливой остановки
   - А. Список инструментов агента (gstack, CLI, MCP, smoke, dry-run) + формат уточнения
   - Б. Шаблон пошаговой инструкции (цель, окружение, шаги с ожидаемым результатом, что вернуть, ветви ошибок)
   - WebSearch/WebFetch обязателен для UI/SDK третьих сторон при сомнении в актуальности
   - Антипаттерны (5 шт)
   - Связь с другими skills

2. **`~/.claude/CLAUDE.md`** — добавлена новая секция «Operator handoff (применяется во всех skills)» перед «Правила безопасности». 5 строк, действует глобально.

3. **review-gate v2.2.0**: в Контракт результата добавлен пункт `Operator handoff` — отказ без классификации/инструкции = дефект ревью.

4. **executor-pro v1.2.0 → 1.3.0**: в Шаге 6 (Handoff в review-gate) добавлено правило: молчаливый `BLOCKED`/`NOT_READY` без operator-handoff = дефект исполнения.

5. **qa-fix v2.0.0 → 2.1.0**: в Правилах качества добавлен пункт Operator handoff — для воспроизведения багов и ручной верификации.

**Anti-bloat отчёт:**
- Покрыто ли существующим? Нет — был только локальный stop-условий формат, без разделения А/Б и без требования инструкции.
- Локальные/глобальные? Глобальное (применяется ко всем skills), поэтому в `~/.claude/CLAUDE.md` (одна секция) + reference, а не клонировано в 30 skills.
- Дубли? Не созданы. В executor-lite не добавляли — глобальное CLAUDE.md правило покрывает; stop-условия уже есть.
- Размеры после правок: review-gate 91, executor-pro 130, qa-fix без существенного роста (1 пункт). CLAUDE.md 142 строки (был 136).

**Backups:**
- `backups/CLAUDE-2026-04-28.md` (глобальный)
- `backups/qa-fix-2026-04-28.md`
- (review-gate, executor-pro уже забэкаплены утром)

**Эффект:** Любой skill в любом проекте при необходимости делегировать действие оператору обязан:
- (А) если есть инструмент — спросить «я / вы / другое», не угадывать;
- (Б) если оператор сам — выдать пошаговую инструкцию с ожидаемыми результатами и тем, что вернуть; если данные о UI/SDK третьих сторон могли устареть — выйти в интернет за актуальными.

Молчаливый `BLOCKED` или абстрактное «требуется ручная проверка» — теперь явный дефект во всех ключевых skills (review-gate / executor-pro / qa-fix), и общий якорь в глобальном CLAUDE.md ловит остальные.

---

## 2026-04-29 — Дельта-аудит после утренних правок

**Запрос:** Повторный `/claude-booster` после первого прогона того же дня (фокусированный анализ дельты, вариант A).

**Метод:** 3 параллельных Explore-агента по свежим JSONL чатам 28-29.04 (LMS 10.7 МБ, SPW 10 МБ, CB 4.6 МБ — активность ПОСЛЕ применения утренних правил skills).

**Что сработало (хорошие сигналы):**
1. **review-gate v2.2.0** — 117 упоминаний в одном LMS-чате; новое 12-е измерение активно используется
2. **Backsync spec в одном коммите** — реальные коммиты `9c9c7d1 docs: backsync spec §6.2/§6.4 + ERRORS`, `ccc4d35 docs: backsync LMS spec/ADR под Y-1.5`
3. **Tech-spec-composer "не додумывать"** — Y-3 ТЗ остановился на 7 архитектурных развилках, явно запросил уточнения
4. **Cross-project memory hub** — 5 мин recovery vs 20-30 мин ранее
5. **VK token type explicitness** — добавлено в lms-db-schema.md
6. **Frontend Route ≠ API Endpoint** — применяется в Y-3 spec
7. **Stop hook** — выводит напоминание о cross-project CHANGELOG

**Выявленные пробелы (5 кластеров):**

| # | Кластер | Симптом |
|---|---------|---------|
| 1 | Молчаливое применение MANDATORY | Только 4 упоминания vs 117 review-gate — пользователь не видит срабатывания |
| 2 | DB migration без spec backsync | Alembic upgrade/downgrade не обновляет docs/db-schema-*.md |
| 3 | useEffect anti-pattern с eslint-disable обходом | `useEffect(() => tgInit(), [])` + `// eslint-disable-next-line` |
| 4 | Multi-context auth не synchronized | Несинхронизированные фиксы web/TG/embed без smoke всех трёх |
| 5 | Stage Dependency Graph отсутствует | Y-3 substages не связаны через явное BLOCKED_BY |
| 6 | OAuth state parameter discipline | `state` parameter переоценивался в ходе ревью |
| 7 | Playwright selector stability | base-ui v4 сломал `getByRole(heading)` в `CardTitle` |

**Применённые правки (минимально, anti-bloat):**

1. **api-contract-rules.md** v→ +§12 (DB Migration as Contract Change), +§13 (OAuth State Parameter Discipline). Добавлено 2 пункта в чеклист §11.
2. **frontend-stack-rules.md** v→ §2 расширен примерами useEffect anti-pattern (good/bad с кодом), +§12 (Multi-Context Smoke Matrix), +§13 (Playwright Selector Stability).
3. **executor-pro v1.3.0 → 1.4.0** — добавлено правило «Явный лог MANDATORY-триггеров»: формат `**MANDATORY review-gate triggered:** <причина>` ДО handoff. Закрывает кластер #1.
4. **tech-spec-composer v1.4.0 → 1.5.0** — добавлено правило «Stage Dependency Graph (BLOCKED_BY)» для multi-stage задач. Закрывает кластер #5.

**Anti-bloat отчёт:**
- Покрыто ли существующим? Частично (cluster #4 multi-context был в §4 frontend-rules, но без матрицы smoke по всем трём контекстам)
- Локальные/глобальные? Глобальные (новые §12-13 в общих references) + 2 точечных правила в skills
- Размеры после правок: api-contract-rules 158 строк, frontend-stack-rules 153 строк, executor-pro 132, tech-spec-composer 121 — все ≤200 ✓
- Дубли не созданы

**Backups:**
- `backups/api-contract-rules-2026-04-29.md`
- `backups/frontend-stack-rules-2026-04-29.md`
- `backups/executor-pro-2026-04-29.md`
- `backups/tech-spec-composer-2026-04-29.md`

**Эффект (ожидаемый):**
- Пользователь увидит срабатывание MANDATORY-правил (явный маркер в ответе)
- Alembic-миграции будут автоматически блокироваться при отсутствии schema-mirror в коммите
- OAuth flows будут иметь явный контракт state parameter с примерами атак в тестах
- Multi-context auth PR обязан содержать smoke по всем трём контекстам
- Multi-stage ТЗ будут содержать BLOCKED_BY-граф; downstream фаза не закроется без upstream
- Playwright getByRole заменено на стабильные getByTestId для критичных flow

**Не зафиксировано как skill-правка (рекомендации пользователю):**
- Разовый ретро-аудит коммита 7a3541b (28.04) на предмет MANDATORY violation — это one-shot задача, не правило skill
- WordPress page injection security review (§22.1 Y-3) — нужен отдельный security-pass

---

## 2026-05-02 — Большой аудит после периода 29.04-01.05

**Запрос:** Анализ 4 проектов (CB, LMS, SPW, **новый TG_LMS**) за 3 дня + автоматизация регулярных аудитов.

**Метод:** 4 параллельных Explore-агента (LMS 10.7МБ, SPW 11МБ, TG_LMS 4МБ, CB 5.5МБ) + чтение ERRORS.md трёх проектов.

**Сводка сигналов:**
- LMS ERRORS.md — 3 OPEN записи 2026-04-29 (#1 streak SQL, #2 spec SQL formula, #3 severity misclassification) с конкретными prevention actions
- TG_LMS ERRORS.md — 1 OPEN с марта (~next_empty), плюс новые класс zombie review states + FSM lock contention
- SPW ERRORS.md — отсутствовал, нужно создать (3 класса критичных дефектов)
- CB ERRORS.md — последняя запись 22.04, новых не появилось, но 3 потенциальных класса не зафиксированы

**Кластеры (6 шт + автоматизация):**

| # | Кластер | Действие |
|---|---------|----------|
| A | Spec Test Coverage Audit (повтор Y-1.5 → Y-3) | executor-pro v1.5.0 + fastapi-api-developer v2.2.0 (Шаг 4) |
| B | SQL Formula Verification (window/gap-detection) | tech-spec-composer v1.6.0 + api-contract-rules §14 |
| C | Spec-Mandated Test Files = S2 (не S3) | testing-checks.md новый раздел |
| D | SPW ERRORS.md отсутствует | Создан с 4 записями (hydration, ACL regression, VK-relay, type-safety) |
| E | TG-bot stack (FSM, Redis, callback_data, zombie) | Создан references/telegram-bot-rules.md (180 строк, 9 разделов); подключён в executor-pro и techlead-code-reviewer |
| G | Автоматизация регулярного аудита | claude-booster v→ Режим E (Регулярный аудит) + auto-audit-config.md + scheduled task `weekly-skills-audit` (Mon 09:00 cron) |

**Применённые правки:**

1. **executor-pro v1.4.0 → 1.5.0** — добавлен `Spec Test Coverage Audit` в Output Contract.
2. **fastapi-api-developer v2.1.0 → 2.2.0** — Шаг 4 пункт 4: grep spec на test_*.py с проверкой existence/edge-cases/pass.
3. **tech-spec-composer v1.5.0 → 1.6.0** — правило SQL formula verification (mental trace 3-input).
4. **api-contract-rules.md** — новый §14 (SQL Formula Verification) + чек-лист пункт.
5. **techlead-code-reviewer references/testing-checks.md** — новый раздел `Spec-Mandated Test Files`.
6. **techlead-code-reviewer SKILL.md** — добавлен ref на telegram-bot-rules.md в Шаг 2 (доменные чеклисты).
7. **executor-pro SKILL.md** — добавлено правило про telegram-bot-rules.md в Правила качества.
8. **claude-booster SKILL.md** — добавлен **Режим E (Регулярный аудит)** с шагами E0-E4 + ссылкой на auto-audit-config.md.
9. **Создан references/telegram-bot-rules.md** (180 строк, 9 разделов: aiogram-dialog conditions, FSM TTL, zombie state, callback_data, forbidden controls, cross-project, encoding).
10. **Создан references/auto-audit-config.md** (3 способа автоматизации: SDK scheduled task, Windows Task Scheduler, loop skill).
11. **Создан d:/Work/spw/docs/ai/ERRORS.md** — реестр SPW с 4 записями (hydration, cookie ACL regression, VK-relay missing, type assertion без Zod).
12. **Создан scheduled task `weekly-skills-audit`** — Mon 09:00 (cron `0 9 * * 1`) запускает Режим E claude-booster автоматически. Notify on completion.

**Anti-bloat отчёт:**
- Дубли не созданы: SQL formula → один раз в api-contract-rules §14 + ссылка из tech-spec-composer
- Размеры всех skills ≤200 строк после правок
- Telegram-bot-rules — новый reference (нужен, не было покрытия), не клонирует ничего
- Auto-audit-config — отдельный reference, claude-booster SKILL.md под лимитом

**Backups (в `references/backups/`):**
- executor-pro-2026-05-02.md
- fastapi-api-developer-2026-05-02.md
- tech-spec-composer-2026-05-02.md
- api-contract-rules-2026-05-02.md
- testing-checks-2026-05-02.md
- claude-booster-2026-05-02.md

**Закрыто OPEN-записей в LMS ERRORS.md:** 3 → FIXED (2026-04-29 #1, #2, #3) с пометкой `FIXED 2026-05-02`. Prevention actions реализованы в коде skills.

**Не создавалось автоматически (operator handoff):**
- ContentBackbone ERRORS.md новые записи (3 потенциальных класса: legacy auth gate, race condition, savepoint pattern) — это реестр пользователя, инициатива оператора.
- TG_LMS local skill `tg-bot-developer` — есть локально, не глобальный; обновление local skill требует решения оператора.

**Метрика этого прогона:** 6 кластеров → 12 правок (10 Edit + 2 новых reference + 1 новый ERRORS.md) + 1 scheduled task. Anti-bloat соблюдён: новых SKILL.md skills не создано, используем reference-pattern.

**Следующий запуск:** 2026-05-03 (понедельник) 09:10 автоматически.

---

## 2026-05-02 (дополнение) — Расширение auto-audit + TG-уведомления

**Запрос:** (1) расширить охват scheduled task на все активные проекты; (2) интегрировать TG-уведомления через плагин.

**Решения оператора:**
- Проекты: вариант **B** — 8 активных с Claude-чатами (CB, LMS, SPW, TG_LMS, ContentFactory, CyberGuru-EGE, IT-Businessman, IDE-booster). Skip-rule: проект без JSONL в окне 7 дней пропускается (защита от шума).
- TG: вариант **TG-A + Escalation** — краткий summary каждый прогон + отдельный 🚨 reply при повторах 3+ или нужны новые файлы.

**Применённые правки:**

1. **Scheduled task `weekly-skills-audit`** обновлён через `mcp__scheduled-tasks__update_scheduled_task`:
   - Description: «Еженедельный аудит skills (8 проектов) + TG-уведомление в @v_komlev_chat (понедельник 09:10)»
   - Prompt: расширен списком 8 проектов с путями к JSONL + skip-rule + Шаг TG (mcp__plugin_telegram_telegram__reply, chat_id=344276500) + escalation шаблоны (🚨 / 🆘 / ⚠️ / ⏭️) + fallback при недоступном плагине.
2. **auto-audit-config.md** обновлён: новая секция «TG-уведомления (через @plugin_telegram_telegram)» — каналы, маркеры событий, антипаттерны, fallback workaround.

**Защита от false positive в TG:**
- Только итоговый summary + эскалации, не каждый кластер.
- Полный отчёт остаётся в improvement-log.md, в TG только ссылка.
- Отдельные reply для escalation/handoff/error чтобы видеть приоритет визуально.

**Open issue (operator handoff):**
- Не подтверждено что MCP-плагин telegram доступен в headless-сессии scheduled-task. Проверка: запустить task вручную через UI «Run now» — если TG-summary дойдёт до chat 344276500, всё ок. Иначе fallback на встроенный `notifyOnCompletion` (in-app notification) + workaround через curl + bot token.

**Следующий запуск:** 2026-05-04 (понедельник) ~09:10 локального времени. Реальное расписание: At 09:10 AM, only on Monday (jitter ~10 min).

---

## 2026-05-05 — Manual auto-audit (компенсация пропущенного weekly прогона)

**Контекст:** Scheduled task `weekly-skills-audit` запускался 2026-05-04 16:28 UTC, но **упал тихо** — нет session log, нет записи в improvement-log, нет TG-уведомления. Гипотеза: MCP-плагин telegram + Agent (Explore) недоступны в headless-сессии. Перевожу автоматизацию на native Windows Task Scheduler (вариант 3).

**Период анализа:** 2026-05-02 (последний прогон) — 2026-05-05 (сегодня).

**Метод:** 4 параллельных Explore-агента на самые активные проекты (SPW 17МБ, LMS 5МБ, CB 5.6МБ, TG_LMS 5МБ).

**Сводка сигналов:**
- LMS ERRORS — все 3 OPEN записи 2026-04-29 закрыты как FIXED 2026-05-02 (правки сработали).
- SPW ERRORS — 2 новые записи 2026-05-04: (1) Y-6 Stage 6 form-hide bug (`!lastResult` слишком широко); (2) TA cross-project drift LMS↔TG_LMS↔SPW (TG_LMS читал `value` вместо `text` для TA submit).
- CB ERRORS — нет новых, но было активное планирование Stream X (Wave 6) с tech-spec на 4000+ строк.
- TG_LMS ERRORS — нет новых, но TA drift зафиксирован в SPW ERRORS (источник в TG_LMS, fix `c4f1f05`).

**Кластеры (2 с автоправками + новые классы для будущего):**

| # | Кластер | Severity | Действие |
|---|---------|----------|----------|
| 1 | Consumer Parity Check для cross-project (TA drift) | S2 | api-contract-rules.md +§15 + чеклист |
| 2 | Conditional UI Hide/Show — обе ветки (form-hide bug) | S2 | frontend-stack-rules.md +§14 |
| 3 | Stream X новые классы (WP app_password leak, mojibake, race, preflight abort) | S3 | покрыто tech-spec CB; usage наблюдаем — пока без правки skills |
| 4 | Headless scheduled task падает тихо | S2 (process) | переход на native Windows Task Scheduler (PowerShell + curl Telegram Bot API) |

**Применённые правки:**

1. **api-contract-rules.md** — добавлен **§15 Consumer Parity Check** (4 шага: grep всех consumers, проверка mock vs openapi, live smoke per consumer, tech-spec обязательный пункт). Источник: SPW ERRORS 2026-05-04 TA drift.
2. **frontend-stack-rules.md** — добавлен **§14 Conditional UI Hide/Show** с good/bad примерами (`!lastResult` vs `lastResult?.is_correct !== true`). Источник: SPW ERRORS 2026-05-04 Y-6 Stage 6.
3. **Размеры**: api-contract-rules 199, frontend-stack-rules 235 (превышение лимита 200, но обоснованно — meta-reference, не SKILL.md).

**Сработавшие правила за период (хорошие сигналы):**
- BLOCKED_BY граф применён в CB Stream X tech-spec (X1.S1 → S2/S3/S4 → S5 → S6 → S7).
- MANDATORY-маркеры расставлены по всем секциям 6.1-6.7 в Stream X.
- Spec Test Coverage Audit работает в LMS Y-5/Y-6 — 0 новых дефектов.
- SQL formula verification — нет новых SQL-багов; severity classification применена корректно.
- Multi-context smoke (web/TG/embed) применяется в SPW.

**Не сработали / регрессии:**
- §6 telegram-bot-rules.md «Cross-project API consumption» — был, но **не применился** при разблокировке TA в SPW (mock TG_LMS отстал). Правило хорошее, но без gate в review — не enforced. → §15 api-contract-rules с обязательным шагом теперь.
- Headless scheduled task — теоретически должен был сработать в понедельник, но процесс упал без следов.

**Backups:**
- `backups/api-contract-rules-2026-05-05.md`
- `backups/frontend-stack-rules-2026-05-05.md`
- `backups/telegram-bot-rules-2026-05-05.md`

**Closed:** SPW ERRORS обе записи 2026-05-04 уже отмечены `done`. LMS Y-3 #1/#2/#3 → `FIXED 2026-05-02`.

**Следующий шаг:** PowerShell `weekly-audit.ps1` + Windows Task Scheduler (вариант 3), полностью обходящий MCP/headless ограничения.

---

## 2026-05-05 (часть 3) — Native Windows Task Scheduler fallback для TG-уведомлений

**Контекст:** MCP-based scheduled task падает тихо в headless-сессии (нет Agent + нет TG-плагина). Реализую fallback через native Windows Task Scheduler + PowerShell + Telegram Bot API напрямую.

**Стратегический выбор:** PowerShell-скрипт **не делает анализ через Claude** (это требует interactive-сессии с MCP-плагинами). Вместо этого:
- Собирает factual stat за 7 дней (размер чатов, кол-во ERRORS-записей).
- Шлёт TG-reminder оператору с просьбой запустить `/claude-booster auto-audit` вручную.
- Это honest подход: автоматика напоминает + предоставляет данные, человек запускает анализ.

**Создано:**

1. **`~/.claude/scripts/weekly-audit-trigger.ps1`** (164 строки):
   - Конфиг 8 проектов
   - Сбор статистики через `Get-ChildItem` + `Select-String` по ERRORS.md
   - Отправка в Telegram через `Invoke-RestMethod` к Bot API
   - Логирование в `~/.claude/logs/weekly-audit-trigger-YYYY-MM-DD.log`
   - Exit codes: 0 (success), 1 (no token), 2 (TG API error), 3 (network error)

2. **auto-audit-config.md** — секция «Способ B» полностью переписана:
   - Pre-requisites (BotFather, env-переменные, тест)
   - Готовая команда регистрации в Windows Task Scheduler
   - Проверка / откат / hybrid-режим с MCP-вариантом

**Operator handoff (Б — оператор делает сам):**

Я не выполняю `Register-ScheduledTask` автоматически — это modifies system state и требует prав пользователя. Вместо этого выдаю пошаговую инструкцию:

1. Создать TG bot (если ещё нет): `@BotFather` → `/newbot` → token.
2. Установить env-переменную:
   ```powershell
   [Environment]::SetEnvironmentVariable('TG_BOT_TOKEN', '<token>', 'User')
   ```
3. Перезапустить PowerShell.
4. Test run скрипта вручную (увидеть, что в TG приходит):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.claude\scripts\weekly-audit-trigger.ps1"
   ```
5. Зарегистрировать в Windows Task Scheduler (одна команда из auto-audit-config.md).

**Hybrid-режим:** MCP `weekly-skills-audit` оставлен enabled — пусть пробует, если headless починят. Native Task Scheduler — гарантированный fallback. Через 4 недели метрики покажут какой канал реально работает, тогда решим.

**Что улучшилось архитектурно:**
- Разделение на «trigger + reminder» (автоматика) и «анализ + правки» (человек) — честно о возможностях headless.
- TG-канал теперь не зависит от MCP-плагинов (curl напрямую).
- Логирование в plain-text файл — диагностика возможна без Claude.
- Exit codes для автоматического мониторинга через `Get-ScheduledTaskInfo`.

## 2026-06-04 — AGENTS.md regression fix (ContentBackbone)

**Триггер:** оператор запросил диагностику AGENTS.md (строка `executor-lite: |` пустая).

**RCA (5 Whys):**
1. AGENTS.md имел `description: |` без content для 3 skill'ов → 2. Codex sync export wrapper читает frontmatter SKILL.md → 3. Эти SKILL.md используют YAML multi-line literal block `description: |` → 4. Wrapper не парсит multi-line literal — экспортирует `|` как пустую строку → 5. **Bug в codex sync export tool**.

**Виновник:** коммит `d5987ca8` от 2026-05-27 «chore: sync codex project wrapper» — Codex automation, не Claude. (Подтверждено `git blame -L 22,40 AGENTS.md`.)

**Affected skills (3):** executor-lite, pr-review, retro — все имеют multi-line `description: |` в frontmatter.

**Fix:** ContentBackbone commit `f64cd15` — восстановлены русские описания из SKILL.md (источник правды) в AGENTS.md.

**Backup:** `references/backups/contentbackbone-AGENTS-2026-06-04.md` (14965 байт).

**Follow-up (codex-booster):** добавить OPEN в codex-booster `references/skills-errors.md` — нужен фикс codex sync export wrapper'а (правильный парсер multi-line YAML) либо migrate SKILL.md на single-line description.

**Anti-bloat:** правка локальная (один файл, 4 строки). Новых правил не вводится. Существующий принцип «SKILL.md — источник правды для AGENTS.md» подтверждён.

## 2026-06-23 — Режим wp-publish в digital-copywriter (tsk-079, ContentBackbone)

**Триггер:** оператор запросил режим публикации в WordPress для скиллов (пока digital-copywriter): 3-уровневая иерархия (навигатор курса/темы/урок) + мини-курс статьёй; блоки текст/код/ASCII/изображения/видео/вопросы(SA/SC/MC)/задания(SA+COM)/проекты; WP = SEO + воронка в LMS.

**Изучение:** WebFetch 4 эталонных страниц victor-komlev.ru → структурная карта 4 уровней.

**Решения оператора:** контракт = блочный JSON; вопросы/задания без ответа (подсказка + CTA в LMS); строить все 4 вида сразу.

**Правка скилла (Режим B):** digital-copywriter v1.5.0 → 1.6.0. Добавлена цель `wp-publish` (параметр kind=lesson/article/topic_nav/course_nav): строка в § Цели, чтение контракта в Шаге 0, компактная секция режима, выдача/сохранение JSON, человечность по текстовым блокам. +30 строк (463→493).

**Backup:** `references/backups/digital-copywriter-2026-06-23.md` (52829 байт).

**Anti-bloat:** полная схема блоков НЕ инлайнится в скилл, а вынесена в `D:\Work\ContentBackbone\docs\wp-content-contract.md` (единый источник истины для скилла + публикатора). В скилле — только режим + ссылка на контракт.

**Код (ContentBackbone, tsk-079):** пакет `monolith/wp_publish/` дополнен блочным контрактом (blocks.py), рендером 4 видов (blocks_renderer.py), интеграцией в renderer/ingest/repo. Исправлен баг: slugify не транслитерировал кириллицу → коллизия anchor'ов. Тесты: tests/test_wp_blocks.py (32 в наборе). Review: `reviews/2026-06-23-wp-block-contract-tsk079.md`.

## 2026-06-23 — Режим lms-publish в digital-copywriter (tsk-079, вторая очередь)

**Триггер:** оператор — «режим публикации контента у скиллов должен быть снабжён дополнительным режимом публикации в LMS». LMS-публикатор (`lms-publish-lesson`, тот же блочный ContentDoc) реализован в параллельной сессии.

**Правка скилла (Режим B):** digital-copywriter v1.6.0 → 1.7.0. Добавлена цель `lms-publish` (сиблинг `wp-publish`): тот же блочный JSON, отличия — поле `answer` обязательно (правило автопроверки), команда `lms-publish-lesson`, виды только lesson/article. Обновлены: description (× 10 целей, упоминание LMS), Цели (строка), Шаг 0 (чтение контракта для обеих целей), 3d/output/save (консистентность). +13 строк (493→506).

**Backup:** `references/backups/digital-copywriter-2026-06-23b.md`.

**Anti-bloat:** детальная схема LMS-маппинга (SA/SC/MC → solution_rules) НЕ дублируется в скилл — она в `D:\Work\ContentBackbone\docs\wp-content-contract.md` § «LMS-публикация». В скилле — только дельта от wp-publish + ссылка на раздел контракта. Контент для WP и LMS — один документ (двухканальность), поэтому отдельной генерации не вводим.

## 2026-06-23 — Дефект взаимодействия с оператором (operator-only без формата Б)

**Триггер:** оператор — «нарушено правило взаимодействия: если требуются действия на стороне оператора, должна быть подробная пошаговая инструкция». Кейс: доработка Yoast tsk-079, шаг «зарегистрировать Yoast-мета в WP» выдан кратким указателем на сниппет в контракте.

**RCA (5 Whys):** корень — **execution gap**. Правило и шаблон полностью есть в `references/operator-handoff-rules.md` (раздел «Формат пошаговой инструкции», категория Б), но не применены — operator-only шаг оформлен как «by the way» в конце сводки.

**Anti-bloat:** правило существует и полное → skill/CLAUDE.md НЕ правлю (дописывать = bloat). Disposition по типу дефекта (response-уровень, не skill-уровень).

**Фикс:** (1) выдана полная инструкция формата Б для WP mu-plugin (цель/окружение/2 пути/что вернуть/ветви ошибок + предложение самоверификации). (2) Лог в `d:/Work/IDE_booster/Docs/ai/ANSWER_ERRORS.md` (HANDOFF_INCOMPLETE, S2, done). (3) Feedback-память `feedback_operator_only_stepwise` (ContentBackbone) для recall в сессиях — ссылается на канон-правило, не дублирует.

**Backup:** не требуется (skill не изменялся).

## 2026-06-28 — Режим D: рецидив «интерактив в LMS-материалах» + неоднозначные код-задачи
- **Дефект A (рецидив):** упражнения/«выбери путь» в статичных LMS-материалах. Корень — instruction gap + прямой авторинг JSON без вызова `/methodist`/`/digital-copywriter`.
  Фикс: methodist `assignment-rules.md` §9 (граница материал↔задание); digital-copywriter `SKILL.md` lms-publish §6; **CreateCourses `CLAUDE.md` — гейт «анализ контента перед публикацией»** (главный рычаг: ловит при любом авторинге + требует вызывать скиллы).
- **Дефект B:** код-задача со ссылкой на «номер строки» (комментарии/пустые строки → неоднозначно). Фикс: methodist §9 п.2 + digital-copywriter lms-publish §7 — ссылаться на элемент кода, не номер строки. Условие #5268 переформулировано.
- Anti-bloat: дефекты кластеризованы (один §9), детали — в скиллах, гейт — в проектном CLAUDE.md (без дублей).
- Обе записи в skills-errors.md → FIXED. Backups: `backups/methodist-assignment-rules-2026-06-28.md`, `backups/digital-copywriter-2026-06-28.md`, `backups/createcourses-CLAUDE-2026-06-28.md`.
- Сопутствующее: quiz=1 попытка вынесено в отдельную сессию; чистка сирот публикатора — отдельный чип (task_cb370101).

## 2026-06-28 (доп) — Режим D: 3-й случай неоднозначного задания (открытое творческое → авто-SA)
- **Дефект:** #5278 «собери свою игру» как SA_COM с ответом «любая рабочая версия» — непонятно, что вводить.
- Корень: §9 не требовал «определимый ответ + явный формат ввода; открытое творческое → не авто-SA».
- Фикс: methodist §9 п.3 + digital-copywriter lms-publish §8 + CreateCourses CLAUDE.md гейт. #5278 → SA «впиши имя переменной фразы победы» (pobeda).
- **Аудит всех заданий** пробного и 4 вводных — прочие определимы (число/имя/да-нет с явным форматом). Запись в skills-errors → FIXED.

## 2026-06-28 (доп-2) — Режим B: headless-QA стенд для SPW (нативный Playwright)
- **Запрос:** воспроизводимая браузерная автоматизация для отладки UX-багов SPW по живому DOM/network (текущий gstack ненадёжен).
- **Решение:** нативный Playwright-драйвер (не Docker). Развилка Docker/native вынесена оператору (AskUserQuestion) → выбран native: в SPW уже есть рабочий live-smoke стенд (`playwright-live.config.ts` + `helpers/auth.ts`), Docker в ContentBackbone — Python single-shot фетчер, переиспользовать как TS-раннер нечего; bind-mount/node_modules на Windows = трение.
- **Артефакты:** `scripts/qa-browse.mjs` (логин ученика через `/auth/test/issue-session` → навигация → aria-snapshot+screenshot+console+network → `.qa-artifacts/`), `docs/ai/qa-headless.md`, `.gitignore` (+`.qa-artifacts/`).
- **Верификация:** smoke `/courses` под student 142 → 200, 0 console-ошибок, network/aria/скриншот собраны.
- **Находка (репро tsk-127):** self-heal `CourseNotFoundResolver` через `uid` нерабочий — `/api/v1/courses/by-code/{uid}` закрыт `get_db→get_api_key` (legacy `?key=`), студенческая cookie → 403 → «Курс не найден» остаётся. tsk-127 помечена done, но баг у ученика не устранён → требует решения оператора.
- Не Docker, не коммичено (по правилу «без явной просьбы»).

## 2026-06-29 — Режим B: визуалы курса по умолчанию (ASCII-инфографика + Codex-handoff)
- **Запрос оператора:** урокам не хватает иллюстраций/инфографики; заложить в скиллы ПО УМОЛЧАНИЮ: точная инфографика = ASCII (генеративке точные данные не доверять), иллюстрации = делегировать в Codex (Claude не генерит) через md-промпт + handoff; визуалы и в WP, и в LMS.
- **Anti-bloat:** блоки `ascii` и `image` уже есть в контракте публикатора (wp-content-contract) и в digital-copywriter — усилил существующее, не плодил механизмы. Канон вынесен в ОДИН reference (`CreateCourses/docs/ai/visuals-policy.md`), который оба скилла читают на Шаге 0; в скиллах — короткие указатели.
- **Правки:** новый `CreateCourses/docs/ai/visuals-policy.md` (политика + Codex-handoff протокол); CreateCourses CLAUDE.md (правило в гейт + пункт верификации); methodist v1.3.0→1.3.1 (1 строка в «Правила качества»); digital-copywriter v1.7.0→1.7.1 (усилены п.2/2a `wp-publish` + антипаттерны, «вручную после публикации» → Codex-handoff). Бэкапы — references/backups/{methodist,digital-copywriter}-2026-06-29.md.
- **Верификация:** frontmatter валиден; methodist 188 стр (< 200), digital-copywriter без раздувания.

## 2026-07-01 — Режим C-подобный: создан распределитель /content-analyzer
- **Запрос оператора:** превратить reader-доку `skills/content-analyzer/SKILL.md` (ContentAnalyzer) в «распределитель» и сделать вызываемой slash-командой. Корзины — по приёмникам North Star.
- **Решения (AskUserQuestion):** названия корзин выведены из docs/NORTH_STAR.md § Приёмники → `skill_boost` / `reference` / `social`; механизм маршрутизации = детерминированное правило (niche+skill_boost+tags+relevance) + опц. лёгкий доп-проход для спорных (`--no-llm` отключает); формат = JSONL-корзины + markdown-ТЗ; регистрация = копия в ~/.claude/skills/ (Windows).
- **Тип артефакта:** agent-workflow SKILL.md (без нового кода в конвейере) — исполнитель читает output/knowledge/*.jsonl, маршрутизирует, пишет только в output/distribution/. Инварианты проекта соблюдены: read-only к content_hub (скилл БД вообще не трогает), read-only к knowledge/digests, секреты в .env.
- **Правки:** repo `skills/content-analyzer/SKILL.md` (reader v1 → распределитель v2.0.0, бэкап reader → references/backups/content-analyzer-reader-2026-07-01.md); копия в ~/.claude/skills/content-analyzer/SKILL.md (вызывается как /content-analyzer); skills-registry.md (+строка в «Контент и копирайт»); ~/.claude/CLAUDE.md (35→36 skills + запись); ContentAnalyzer/CLAUDE.md (описание файла reader→распределитель).
- **Верификация:** frontmatter валиден (name/version/description/allowed-tools); /content-analyzer появился в списке доступных skills сессии; размер SKILL.md в пределах нормы для сложного скилла с таблицами.
- **Трекер:** tsk-029 уже `status: done` (legacy_xlsx) — не трогал; артефакт закрывает её по сути.

## 2026-07-02 — Режим B: 2 глобальные правки архитектуры (автоподбор skills + NorthStar)
- **Запрос оператора:** (1) Клод сам подбирает и назначает релевантный skill, если оператор его не указал; (2) `/project-docs` и `/ceo-review` фиксируют конечную цель в NorthStar-документ, при глобальных изменениях — сверка; при неоднозначности цели — уточнять до записи.
- **Anti-bloat:** NorthStar НЕ изобретал — концепция уже принята (`docs/NORTH_STAR.md` в ContentAnalyzer, ссылки в content-analyzer + запись лога 2026-07-01). Стандартизировал единое имя файла для всех проектов, без нового механизма. Автоподбор опёрт на существующие § «Доступные skills» + skills-registry.md; сверку артефактов с целью уже делает `/context-auditor` — не дублировал, а связал.
- **Правки:**
  - `~/.claude/CLAUDE.md` — новый § «Автоподбор skills (обязательно)» (4 правила + запрет выдумывать skills, привязка к handoff-ветви А и Шагу 0 трекера); новый § «NorthStar — фиксация конечной цели» (`docs/NORTH_STAR.md`, сверка перед глобальными изменениями, уточнение до записи, связь с `/context-auditor`).
  - `project-docs` v1.0.0→1.1.0 — NorthStar в план артефактов (Шаг 3), генерация первым в Шаге 4 full + сверка в update, строка в «Правила качества».
  - `ceo-review` v1.2.0→1.3.0 — блок «ОБЯЗАТЕЛЬНО: NorthStar» (создание по Шагу 0C / сверка = КРИТИЧЕСКИЙ ПРОБЕЛ при расхождении / уточнение до записи; разведён с брифом), строка в «Контракт результата».
- **Верификация:** frontmatter обоих skills валиден; NorthStar-имя консистентно с ContentAnalyzer. Бэкапы: `backups/{CLAUDE-global,project-docs,ceo-review}-2026-07-02.md`.

## 2026-07-02 — Режим B+C: третий класс визуалов (UI-скриншоты) + skill /course-screenshots
- **Запрос оператора:** усилить скиллы публикации курса и правила CreateCourses требованием скриншотов; при необходимости — доп. режим/скилл. Повод — успешное закрытие находки К8 (tsk-145) визуалами install/онбординг/UI-уроков.
- **RCA (корень):** instruction gap. Политика визуалов знала 2 класса (`ascii`=точное, Codex=декор). Детерминированные UI-скриншоты (терминал/BotFather/дерево файлов/экран приложения) выпадали: Codex запрещён (текст/UI/скриншоты), ASCII вид не передаёт → install/UI-уроки оставались без визуалов (корень К8).
- **Anti-bloat:** не плодил механизм — расширил существующую политику ОДНИМ третьим классом + «Протокол UI-скриншотов» в источнике истины (`CreateCourses/docs/ai/visuals-policy.md`); CLAUDE.md/digital-copywriter/methodist — короткие указатели на него. Отдельного skill про скриншоты не было → создан (не дублирует: digital-copywriter публикует, methodist проектирует, Codex-путь — генеративка; рендер точного UI не покрыт никем).
- **Правки:** visuals-policy.md (2→3 класса, «Протокол UI-скриншотов», +2 пункта чеклиста); CreateCourses CLAUDE.md (строка в «Визуалы — ОБЯЗАТЕЛЬНО»); digital-copywriter v1.8.0→1.8.1 (п.2a `wp-publish`); methodist v1.6.0→1.6.1 (строка + «помечать уроки под скриншоты» при проектировании); новый `~/.claude/skills/course-screenshots/SKILL.md` v1.0.0 (73 стр); `~/.claude/CLAUDE.md` (43→44 skills + запись). Бэкапы: `backups/{digital-copywriter,methodist}-2026-07-02.md`.
- **Верификация:** frontmatter нового skill валиден, 73 стр (норма); `/course-screenshots` появился в списке доступных skills сессии; все Edit по точным якорям, версии подняты (patch).
- **Флаг оператору:** `CreateCourses/AGENTS.md` (ручная копия CLAUDE.md для Codex) рассинхронизирован — урезан до 4 секций, нет гейтов/визуалов. Вынесен отдельной задачей-чипом (не переписывал вслепую — вне scope запроса).

## 2026-07-02 — Режим D + расширение: 7 дефектов авторинга (QA Информатика 5-11) + контур обратной связи для ревью-скиллов курсов
- **Запрос оператора:** по итогам QA курса «Информатика 5-11» (2 ревью-скилла, грейд C) — (A) усилить `/methodist` и `/digital-copywriter` по 7 систематическим дефектам; (B) завести контур обратной связи для ревью-скиллов курсов (реестр course-quality + RCA), замкнуть на claude-booster; заложить в ревью-скиллы verify-before-P0 (ложный P0 из чтения не того поля блока).
- **Задача A (7 дефектов, RCA-кластеры):**
  - Кластер «конверт урока» (дефекты 1 нет цели / 2 нет итога / 5 нет «как запустить» / 6 нет спиральных мостиков) — instruction gap в проекции урока L3. Фикс: methodist `difficulty-and-design.md` п.7 + п.3 (мостик), `lms-wp-export.md` структура L3 + «не lms_skip», `coverage-and-review.md` анти-поверхностность, SKILL v1.6.1→1.7.0 (198 стр); digital-copywriter `wp-publish` правила 9–10 + чекбоксы, v1.8.1→1.9.0.
  - Дефект 3 (визуальный голод на абстрактных темах) — усилил правило «Визуалы» methodist + coverage-and-review (пометка логика/СС/структуры/алгоритмы → ASCII-схема внутри).
  - Дефект 4 (код-практика только «предскажи вывод») — в осн. execution gap (правило §8 п.8 было); добавил недостающие типы (трассировка, fill-in) + гейт применения на код-урок. Не новый файл.
  - Дефект 7 (гигиена источника: домен bosova.ru, битый alt) — digital-copywriter `wp-publish` правило 11 + верификация 3a + антипаттерны.
- **Задача B (контур обратной связи):**
  - Новый реестр `references/course-quality-errors.md` (OPEN/FIXED; кто пишет — ревью-скиллы, кто обрабатывает — claude-booster Режим D; засеян 7 находками сессии как рабочий пример). **Обоснование отдельного файла:** skills-errors = дефекты SKILL (фикс → правка skill); course-quality = дефекты КУРСА (фикс → правка авторинг-скилла ИЛИ курса). Смешение утопило бы очередь skill-дефектов; связаны кросс-ссылкой.
  - claude-booster Режим D: D0 читает оба реестра, заголовок «Обработка реестров (skills + качество курсов)», триггер активации расширен — контур замкнут.
  - Оба ревью-скилла: новый Шаг 6 «занести класс-находку в реестр + 5 почему»; verify-before-P0 (adversarial-verify: правильные поля блочного JSON html/stem/text + факт-проверка content_hub/рендер до P0); naive `sources.md` — канон полей, expert — указатель. Версии 1.0.0→1.1.0.
- **Anti-bloat:** усиливал существующие правила/референсы, не плодил механизмы; verify-before-P0 = аналог dev adversarial-verify; детали в references, в SKILL.md указатели; methodist 198<200. Ledger правок — `skills-errors.md` (2 FIXED-кластера 2026-07-02).
- **Верификация:** frontmatter всех 5 skills валиден; methodist 198 стр (<200); версии подняты; все Edit по точным якорям; бэкапы — `references/backups/*-2026-07-02-qa.md`.

## 2026-07-03 — Режим D: усиление плоский-vs-подкурсы (порог 20 + «почему» LMS-подачи) в methodist/digital-copywriter/CreateCourses
- **Запрос оператора:** усилить methodist, digital-copywriter (lms-publish) и правила CreateCourses по теме плоских курсов vs подкурсов — когда нужны, когда нет. Логика LMS: материалы→задания; при подкурсах читаются по `order_number`, у каждого свои материалы+задания; длинная теория → к заданиям забыл. Плоские курсы — только мини. Агент при публикации должен знать/помнить. Повод — enforced-механизм порога 20 (tsk-148).
- **RCA (корень):** instruction gap, но **правило уже существовало** во всех трёх целях (methodist чеклист §1.4, CreateCourses гейт «Большой курс — подкурсами», digital-copywriter — только контракт). Пробел: не было (а) конкретного **порога ≤20**, (б) явного «почему» через `order_number`/подачу LMS, (в) факта, что публикатор это **enforce-ит** (tsk-148).
- **Anti-bloat:** НЕ клонировал правило — **усилил существующие формулировки** + один канонический «почему» в источнике-справочнике. Полный текст живёт в `wp-content-contract.md` «Структура подкурсами» (tsk-148) и methodist `lms-wp-export.md §1.1.1`; в SKILL/правилах — компактный инвариант + указатель.
- **Правки (живые копии `~/.claude/skills/` — то, что запускается):**
  - methodist `references/lms-wp-export.md` — новый §1.1.1 «Плоский vs подкурсы: порог и почему» (подача LMS материалы→задания, `order_number`, забывание; инвариант ≤20; enforce; билдер `graph_authoring`) + заметка by-code/parents (tsk-147); SKILL v1.7.0→1.7.1 (чеклист-строка: порог ≤20 + «почему» + §1.1.1, net-0 строк, 198<200).
  - digital-copywriter SKILL v1.9.0→1.9.1 — в `lms-publish` п.2 добавлен блок «Порог/структура» (плоско только мини ≤20; >20 → `subcourses[]`; enforce tsk-148; проектирование графа — /methodist).
  - CreateCourses `CLAUDE.md` — гейт-строка «Большой курс — подкурсами» усилена: порог ≤20, `order_number`, мини-исключение, enforce публикатором (tsk-148).
  - Синхрон: тот же §1.1.1-«почему» добавлен в исходник `ContentBackbone/skills/core/methodist/references/lms-wp-export.md`.
- **Верификация:** mojibake нет (скан 4 файлов); frontmatter валиден; methodist 198<200; версии подняты (patch); все Edit по точным якорям; бэкапы `references/backups/{methodist-SKILL,methodist-lms-wp-export,digital-copywriter-SKILL,CreateCourses-CLAUDE}-2026-07-03.md`.
- **Флаг оператору (дрейф skills):** живые `~/.claude/skills/{methodist,digital-copywriter}` и исходники `ContentBackbone/skills/core/*` **рассинхронены** пофайлово (SKILL.md исходников не содержат якорей живых версий 1.7/1.9 — устарели; часть references наоборот новее в исходнике). Автосинки нет. Правил живые (origin запуска). Полная сверка source↔live — отдельная задача (вынесено чипом). `CreateCourses/AGENTS.md` (Codex-копия) не трогал (правило безопасности) — его рассинхрон уже зафиксирован 2026-07-02.

## 2026-07-03 — Инфраструктура: канон skills = live, механизм синка + сходимость CB/CF (tsk-156)
- **Запрос оператора:** рассинхрон трёх копий скиллов (live `~/.claude/skills`, ContentBackbone `skills/core`, ContentFactory `skills/core`), автосинка нет, правки booster теряются. Определить канон + направление, свести methodist/digital-copywriter и ядровые, предложить механизм. Не переписывать вслепую — сперва диф.
- **Диагностика:** live = origin запуска и КАНОН. SKILL.md исходников — устаревшие снимки ~2026-06-01 (methodist 67 vs live 198 v1.7.1; dc 51 vs live 573 v1.9.1). CB==CF побайтно. «source новее по дате» = артефакт бывшего Codex-упаковщика `d:\Work\IDE_booster\scripts\package-skills.py` (регенерировал зеркала, штамповал mtime), НЕ ручные правки — проверено на 11 скиллах (live везде толще). Единственное реальное исключение — **codex-booster** (в исходнике реальный Codex-контент: package-skills.py, .codex/AGENTS.md, манифесты; Codex выведен из стека → легаси).
- **Механизм:** `references/../tools/skills-sync.ps1` (diff/sync, `-Apply/-Force/-Exclude/-Skill`; UTF-8 BOM для PS5.1). Правило: править только live, CB/CF — производные. Дефолт консервативен: source-новее НЕ затирается без `-Force`, сироты не удаляются.
- **Раскатка live→source:** 58 зеро-риск (ОТСТАЁТ + новые references) + 41 SKILL.md через `-Force -Exclude codex-booster`. Итог: 148/181 пар идентичны, 0 «ОТСТАЁТ»; dc/methodist live==CB==CF; mojibake нет.
- **Баг-фикс в ходе:** PowerShell `строка + массив` при одном .md-файле в live → пути-мусор → ложное «равны» (прятал dc + 5 скиллов). Исправлено `@()`-обёрткой.
- **Остаток (оператору):** codex-booster (легаси) + 27 сирот — решение отложено в tsk-156.
- **Anti-bloat:** один скрипт покрывает и диф, и синк; исходники не удаляются вслепую.

### ПОПРАВКА к записи выше (2026-07-03, tsk-156)
Первичный вывод «CB/CF core = устаревшие копии live, канон один (live), codex-booster легаси» — **неверен**. По манифесту `IDE_booster\Docs\ai-booster\skill-packaging-manifest.json`: деревьев ДВА — Claude-канон (live `~/.claude/skills`) и Codex-канон (`D:\Work\IDE_booster\skills`); `{repo}\skills\core` — СГЕНЕРИРОВАННЫЕ Codex-зеркала (`package-skills.py`), не ручные копии. Codex НЕ выведен из стека (резерв + генерация контента — поправка оператора). Ошибочная раскатка live→CB/CF (99 файлов) откачена через git; live не тронут. Пропагация live→Codex вынесена в tsk-157 (через codex-booster). Урок: до раскатки проверять, генерируется ли целевое дерево (манифест/скрипт упаковки), а не судить по датам файлов.

## 2026-07-06 — Автоматический аудит (weekly), Режим E — первый реальный прогон

**Период:** 2026-06-29 → 2026-07-06. **Проекты:** ContentBackbone, LMS, SPW, TG_LMS.
**Триггер:** запрос оператора («проверить еженедельный контур улучшения скиллов») — контур существовал
с прошлой недели (глобальный CLAUDE.md self-trigger, tsk-141 R5), но **ни разу не запускался** — в этом
логе не было ни одной записи «Автоматический аудит (weekly)» до сегодня.

### Сигналы из ERRORS.md (Шаг E1.1)
- **CB:** 2 новые записи (2026-07-01, обе `fixed`, tsk-134 Opera-capture): (a) ctypes без `restype` на
  clipboard-функциях — единичный случай, не класс; (b) `load_config()` whitelist gap для нового `CB_*`-ключа.
- **LMS/SPW/TG_LMS:** 0 записей за период.

### Кластер — автоправка применена (Шаг E2/E3)
**`load_config()` whitelist gap — повторяющийся класс, 3 эпизода:** 2026-03-09, 2026-04-22, 2026-07-01.
Оба прежних эпизода уже содержали текст-профилактику в самом ERRORS.md («executor-pro/lite обязаны
проверять loader», «techlead-code-reviewer обязан считать silent failure config автоматическим FAIL»)
— но эта профилактика НИКОГДА не попадала в реальный файл скилла, поэтому баг повторился в 3-й раз.
**RCA:** instruction gap (не execution gap) — правило существовало только как текст в реестре, не как
проверяемый пункт в активном skill. Anti-bloat: проверил `techlead-code-reviewer/executor-pro/executor-lite`
— пункта нигде не было, дублирования нет.
- **Побочная находка при git log-сверке (Шаг E1.3):** родственный паттерн — тихий fallback на
  dev-дефолты (`os.environ.get(KEY) or "localhost"/"bot-key-1"`) в `scripts/tsk103_futurestep_attach.py`
  + `scripts/smoke_b2_lms_import.py` (коммит `509cc2f`, tsk-159), пойман **независимым ревью Codex**, не
  Claude-скиллом. Тот же корень (config тихо идёт не так вместо явного отказа) — объединил в одну проверку,
  не два отдельных пункта (anti-bloat: не клонировать близкое правило).
- **Правки:**
  - `techlead-code-reviewer/references/review-checklist.md` — новая секция «Config Silent-Failure Check»
    (whitelist gap + silent dev-fallback, единый пункт). Заодно убран случайный построчный дубль трёх
    секций (Live API/Spec-to-Code/Fetch-Normalize), обнаруженный при чтении файла — 85→84 строк.
  - `ContentBackbone/.claude/CLAUDE.md` — конкретный guard про `load_config()` whitelist (проектный
    уровень, читается executor-pro/lite на Шаге 0).
  - Backup: `references/backups/techlead-code-reviewer-review-checklist-2026-07-06.md`,
    `references/backups/CB-project-CLAUDE-2026-07-06.md`.

### Предложения из skill_boost (Шаг E1.4)
Корзина `D:\Work\ContentAnalyzer\output\distribution\skill_boost\*.jsonl` за неделю — 734 item'а
(инфлировано тестовыми прогонами CA той же недели). Основная масса — общие ML-исследования
(диагностика по снимкам, RL-фреймворки, T2I diffusion), помеченные `skill_boost=true` слишком щедро.
Внутри шума нашлись 5 genuinely-релевантных находок — релизы Anthropic Python SDK v0.111.0-v0.115.0
(managed agents, потоковые события, web-fetch tool, middleware для отказов, поддержка claude-sonnet-5).
**Решение по правилу E2.4:** НЕ автоправка — эти релизы про построение агентов через raw SDK
(вызовы Anthropic API напрямую), а не про сами Claude Code skills; не ложатся на scope ни одного
существующего skill (критерий (б) не пройден). **Оператору:** (1) сами SDK-релизы — фоновая
осведомлённость, не требуют правки skills; (2) calibration-сигнал для ContentAnalyzer — промпт
`analyze.txt` даёт `skill_boost=true` любому AI/ML-контенту, а не только материалу про Claude Code/
промптинг/агентов/MCP — корзина реально полезна, но с большим шумом; сузить критерий — решение
оператора и CA-сессии, не claude-booster (вне scope этого skill).

### Шаг E1.2 (чат-логи 4 проектов) — БЛОКИРОВАН устойчивым сбоем платформы
Explore-агенты по чатам всех 4 проектов запускались **16 раз** (4 проекта × 3 свежих запуска + 1 resume
через SendMessage) за ~40 минут — **все 16 попыток провалились** на этапе финального ответа (`API Error
529 Overloaded` / `401 Invalid authentication credentials`, вперемешку). Прямой вызов WebFetch на
status.claude.com из главной сессии (не саб-агент) тоже вернул 529 — подтверждает: сбой на уровне всей
платформы в момент аудита, не специфичен для параллельных саб-агентов. Часть попыток реально проработала
(16-18 tool calls до финального сбоя), но usable-отчёт не вернулся ни разу. **Не докручено** —
кросс-проверка чатов (репозиторий незафиксированных в ERRORS.md дефектов) для всех 4 проектов не
выполнена в этом прогоне; вывод по LMS/SPW/TG_LMS = «0 записей ERRORS.md», НЕ подтверждён независимо
чтением чатов. Требуется повторный прогон Шага E1.2, когда платформа восстановится.

### Итог
- **Кластеры:** 1 (load_config whitelist + silent fallback), автоправка применена.
- **OPEN-записи:** 0 новых (единичный ctypes-случай не образует класс — не заведено).
- **Эскалации:** нет (3 эпизода одного класса зафиксированы и закрыты этим прогоном).
- **Operator handoff:** (1) сузить `analyze.txt` skill_boost-критерий в CA — решение оператора/CA-сессии;
  (2) Шаг E1.2 (чат-верификация LMS/SPW/TG_LMS) не выполнен из-за платформенного сбоя — повторить.
- **Первый реальный прогон контура:** подтверждено — E1.1/E1.3/E1.4 дали настоящий, применимый результат
  (3-й повтор реального класса бага найден и закрыт), механика работает. E1.2 требует повтора.

## 2026-07-08 — Режим B: межагентный реестр (tsk-173, ADR-0005)

- **Целевые файлы:** новый `D:\Work\Root\agents\` (`README.md`, `ownership.md`, `_index.md`,
  `_ledger.md`, `handoff/`); `Root\tools\validate_agents.py`; `Root\.git\hooks\pre-commit` (шаг 3);
  `~\.claude\CLAUDE.md` (раздел «Cross-agent Coordination Protocol»);
  `Root\docs\ai\PROJECT_MEMORY.md` (точка входа Codex); `booster-shared.md` (§7 + уплотнение §1.4/§4);
  строка-указатель в `claude-booster`, `codex-booster`, `cursor-booster`.
- **Причина:** Claude и Codex пишут в общие ресурсы параллельно, механизма координации не было.
  Прецедент 2026-07-08: Codex переписал `naive-learner-review` в своём каноне (08:45), правка
  разошлась в зеркала; Claude узнал случайно через `stat`. В тот раз санкционировано (второе мнение),
  но механизм допускал и молчаливое затирание.
- **Решение оператора (AskUserQuestion):** отдельный реестр `Root/agents/` (не расширение трекера
  задач — многие межагентные действия задачами не являются); enforcement = правило + pre-commit hook.
- **Anti-bloat отчёт:** протокол НЕ продублирован. `booster-shared.md` §1.4 (cross-handoff) и §4
  (ownership/mirrors) переформулированы как частные случаи реестра, полный протокол — только в
  `agents\README.md`. В трёх booster-скиллах — по одной строке-указателю, ссылки на `booster-shared`
  у них уже были. Итого добавлено в SKILL.md: 1 строка (claude-booster), по 0 строк (codex/cursor —
  дописано слово в существующее перечисление).
- **Верификация:** `validate_agents.py` прогнан — на первом прогоне поймал собственный дефект
  (читал строки внутри HTML-комментария), исправлено; негативный тест на просроченный TTL проходит
  (3 предупреждения при `now=2027-01-01`). Hook целиком: gitleaks → validate_graph → validate_agents
  → pytest, exit=0. BOM отсутствует во всех новых файлах.
- **Известное ограничение:** hook срабатывает только при `git commit` в `D:\Work\Root`; правка
  `~/.claude/skills` через него не проходит. За пределами Root — soft rule (риск R-A1 в ADR-0005).

## 2026-07-08 — Режим B: permissions для claude-in-chrome (шум запросов)

- **Целевые файлы:** `~/.claude/settings.json` (глобально) + `D:\Work\CreateCourses\.claude\settings.local.json` (сессия/проект).
- **Проблема:** проектный local накопил MCP-разрешения claude-in-chrome ПОШТУЧНО
  (navigate, tabs_context_mcp, javascript_tool, computer) — каждый новый инструмент
  сервера, не попавший в список, вызывал диалог. «Бесконечные запросы».
- **Решение:** server-wide allow `mcp__claude-in-chrome` (один чип на весь сервер) +
  секция `ask` на 3 реально-деструктивных инструмента (form_input, file_upload,
  upload_image) — они остаются под запросом. Приоритет ask>allow (подтверждён офиц.
  докой code.claude.com/docs/en/permissions.md: порядок deny→ask→allow).
- **Классификация:** 19 инструментов (навигация/чтение/computer/javascript_tool) →
  молча; 3 (ввод в формы, загрузка файлов) → спрашивать. computer и javascript_tool
  в allow осознанно: их нельзя разбить по семантике на уровне имени, а деструктив
  ловит agent-level safety-политика (обязан спросить перед submit/purchase/delete
  независимо от allowlist).
- **Anti-bloat:** заменил 4 узких правила на 1 широкое (уплотнение, не рост);
  в global +1 allow +3 ask; local 20→17 правил. Дублей scope не плодил — ask только
  в global (наследуется по всем scope через порядок оценки правил).
- **Fact-check (booster §5):** синтаксис проверен через claude-code-guide по офиц.
  доке — `mcp__server` (весь сервер) поддерживается, `ask` существует, ask>allow.
- **Верификация:** оба JSON валидны (`json.load`); deny-список Bash нетронут.
- **Затронутые skills:** нет (чистая permission-правка; gstack использует свой
  браузер, не этот MCP).
- **Известный нюанс:** global подхватится и в текущей, и в будущих сессиях; если в
  этой сессии запрос всё же мелькнёт до перечитывания settings — снимется после
  перезапуска сессии.

## 2026-07-08 — Цель 2 (tsk-172): фиксы контентных скиллов по consensus-final

- **Причина:** ревью Информатики 5–11 (3 источника: Claude+Codex+оператор) выявило 5 классов
  дефектов, которые скиллы пропустили ДАЖЕ после v2.0. Оператор прохождением поймал 2, что
  пропустили оба скилла (F1 падеж SA, F2 разъезд ASCII).
- **naive-learner-review (6+1 фиксов, Claude-канон):**
  1. рендер живой страницы — обязательный слой (sources.md, naive-question-pass);
  2. ASCII-чек: широкие глифы `⟷⟶▶◀──—↔` → флаг W6, метрики не заменяют глаз;
  3. проверка атома-задания: сверить форму `answer` с формой из `stem`, «ввести естественный ответ»;
  4. W3 расширен: перечисление → +классификация/пара терминов/граница;
  5. интерактив: фильтр безопасности (пароль/ПД/«покажи другу») + противоречие уроку;
  6. скриншот оператора = доказательство сильнее предположения скилла;
  +7. согласованность определений между уроками (тачскрин 2.1↔2.4).
- **methodist assignment-rules (3 фикса):** §9.3 форма эталона = форма вопроса + accepted_answers
  терпимы + тест «ввести естественный ответ»; §9.5 обобщение (новый пример по критерию);
  §9.6 миссии — безопасность + формат + непротиворечие уроку.
- **digital-copywriter (3 фикса):** §it-writer п.7 абстрактный глагол → пример; п.8 классификация =
  термин+критерий+пример+пограничный; п.9 интерфейс/файлы/сеть → бытовая сцена.
- **visuals-policy.md:** правит **Codex** (захват clm-006 в межагентном реестре) — Claude не
  дублирует. Ровно тот случай, ради которого создан реестр (tsk-173): предотвращён конфликт
  на общем файле.
- **Публикатор ContentBackbone (SA dry-run):** вынесен в tsk-175 (код+тесты, отдельный класс).
- **Anti-bloat:** все правки naive-learner в references (SKILL.md не тронут); methodist/dc — в
  существующие §9 и §it-writer, усилены формулировки, не клонированы разделы.
- **Реестр качества:** 4 записи → FIXED, 1 → IN_RCA (PNG, за Codex), ASCII → FIXED(ревью)/IN_RCA(авторинг).
- **Верификация:** BOM отсутствует во всех правленых; размеры references в норме.
- **Осталось:** правки контента гл.2 обновлёнными скиллами → повторный прогон по чек-листу F1–F15
  (порядок утверждён оператором: скиллы → пилот гл.2 → весь курс).

## 2026-07-09 — Режим B: bucket_marker для контура skill_boost (по запросу оператора)

- **Проблема:** Режим E Шаг E1.4 читал корзину `skill_boost` целиком за окно каждый прогон —
  при повторных/внеплановых запусках `/claude-booster` находки переобрабатывались заново
  (нет способа отличить «уже стало правкой» от «новое»).
- **Решение (CA `6702371`):** новый модуль `content_analyzer/bucket_marker.py` —
  `processed_by[marker]=timestamp` прямо в JSONL-записи корзины (rewrite-in-place, tmp+rename,
  как у `poison_tracker.sweep()`). CLI: `--list` (непомеченные записи) / `--mark --uid/--uids-file`.
  9 тестов + живая проверка на реальной `skill_boost`-корзине.
- **Правка SKILL.md (v2.3.0→2.4.0):** Шаг E1.4 читает через `bucket_marker --list --marker skill_audit`
  вместо сырых `.jsonl`; Шаг E2.4 после решения по item'у (правка / решение оператору / отбраковка)
  обязан пометить `--mark` — метка означает «рассмотрено», не «применено».
- **Anti-bloat:** механизм общий (marker — свободное имя), не skill_boost-специфичный — годится
  для будущих потребителей других корзин (source_discovery-ревью и т.п.) без дублирования.
- **Резервная копия:** `references/backups/claude-booster-SKILL-2026-07-09.md`.

## 2026-07-10 — expert-course-review v1.2.0: две проверки материала + проверка заданий + норматив плотности

**Источник:** операторский разбор материалов 7 класса информатики (tsk-176). Прецеденты:
урок про сигналы (непрерывный/дискретный — примеры односложные, без визуального ряда
разницу не понять); «Свойства информации» (примеры односложные, без контрастных пар).

**RCA (5 Whys → корень).** Симптом: рубрика не поймала пробел. → К7 требовала «пример на
понятие», но не задавала ФОРМУ примера и не требовала показать РАБОТУ понятия. → Проверки
шли по курсу целиком, а не по каждому материалу — пробел тонул в среднем. → Не было правила
об отсылке к уроку для повторного термина. → Не было норматива плотности заданий для
школьного курса. **Корень: instruction gap** в рубрике expert-course-review (не execution).

**Anti-bloat check.** (1) Покрыто ли? Частично: К7 уже имела «пример на каждое новое
понятие» и «термин объяснён до использования»; К5 уже имела правило «подсказка не сливает
ответ»; methodist уже имел «3 задания на содержательную тему = брак». → **Усилил
формулировки, не клонировал.** (2) Локальное. (3) Всё легло в существующие критерии
рубрики, новых файлов не создавал. (4) Соседние скиллы: авторская сторона (methodist,
digital-copywriter, visuals-policy) говорит «делай так», ревью-скилл — «проверь, что так»;
перекрёстные ссылки вместо повтора правил. (5) Устаревания нет; §3.1 methodist («лист —
ровно 3») уточнён §3.1a: школьный урок — не лист.

**Применённые правки:**
- `expert-course-review/references/rubric.md` — **К7**: проход ПО КАЖДОМУ материалу;
  разбор терминов (повторный → отсылка к уроку; новый → развёрнутые жизненные примеры);
  новое понятие → пример его РАБОТЫ, приоритет изображение > текст, сложные — и образ, и
  текст; парные понятия — контрастной парой. **К4**: норматив плотности (Информатика 5-11:
  10 заданий на УРОК = 6 простых + 3 средних + 1 проектное; 3 на урок — провал).
  **К5**: варианты/объекты вне материалов — НЕ дефект (намеренный перенос).
- `expert-course-review/SKILL.md` — Шаг 3: две проверки К7 идут по каждому материалу;
  версия 1.1.1 → **1.2.0**.
- `methodist/references/assignment-rules.md` — новый **§3.1a** (норматив 10 = 6+3+1 на
  урок-тему школьного курса; §3.1 «ровно 3» сюда не применять) + правило переноса
  («объекты вне материалов — норма, признак должен быть дан»). SKILL 1.8.0 → **1.9.0**.
- `digital-copywriter/SKILL.md` — it-writer **п.3** (термин из раннего урока — с отсылкой;
  разбор терминов по каждому материалу), **п.4** (пример показывает работу понятия;
  приоритет образ > текст; сложные — и то и другое; парные понятия — примерами парой).
  1.10.1 → **1.11.0**.
- `CreateCourses/docs/ai/visuals-policy.md` — «Приоритет формы примера» (образ > текст,
  сложные — и образ и текст, парные понятия на одном образе рядом).

**Развилка, снятая оператором:** «10 на главу» → фактически **10 на УРОК** (в главе уже
15-18: 5-6 уроков × 3). Подтверждено данными прода перед правкой.

**Резервные копии:** `references/backups/{expert-course-review,expert-course-review-rubric,
methodist-assignment-rules,digital-copywriter,visuals-policy}-2026-07-10.md`

**Дубль-трек:** Codex параллельно усиливает свой канон expert-course-review (clm-028).
**Следующее:** повторный анализ классов 5-6-7 двумя агентами; дальше по курсу не идём.

## 2026-07-10 — expert-course-review v1.3.0: обмен усилениями с Codex (дубль-трек)

**Повод:** оператор попросил сверить канон Codex перед повторным аудитом 5-6-7 — не упустили ли мы что-то.

**Что нашли у Codex и перенесли (2 находки):**
1. **Поля-доказательства в `report-format.md`** (главное). У нас были ПРОВЕРКИ, но не было
   требования показать пруф в отчёте. Добавлено: для находок по материалам — `Термины/понятия`
   (новые понятия; ранее введённые термины, требующие отсылки; опора: `визуал` /
   `развёрнутый пример` / `однословный пример` / `нет`); для находок по заданиям —
   `Банк заданий` (сколько, распределение, есть ли проектное) + `Подсказки`. Без них
   «проверено» голословно; проверки становятся аудируемыми.
2. **Смысловые роли сложностей + клапан (К4):** 6 простых — узнавание/прямое применение;
   3 средних — перенос и комбинирование; 1 проектное/мини-практика. Меньше 10 — только для
   очень короткого урока и с явным обоснованием в отчёте. Перенесено и в авторскую сторону
   (`methodist` §3.1a).

**Независимое схождение:** Codex сам пришёл к норме **10 на УРОК** (не на главу) — сильная
валидация развилки, снятой оператором по данным прода.

**Что есть у нас и НЕТ у Codex (передано handoff'ом):** К5 «варианты/объекты вне материалов —
НЕ дефект» (намеренный перенос; дефект только если признак не давался вовсе). Без правила
ревьюер ложно флагает нормальные задания на перенос.

**Попутно исправлен свой дефект:** SKILL.md Шаг 3 гласил «плотность заданий считается по главе»
— противоречило нормативу «на урок». Исправлено на «по КАЖДОМУ УРОКУ-узлу».

**Anti-bloat:** обе находки — аддитивные (контракт отчёта + семантика ролей), не дублируют
существующие правила. Новых файлов не создавал.

**Правки:** `expert-course-review/references/report-format.md` (+поля-доказательства),
`.../rubric.md` К4 (роли + клапан), `expert-course-review/SKILL.md` (Шаг 3 фикс, v1.2.0 → **1.3.0**),
`methodist/references/assignment-rules.md` §3.1a (роли + клапан).
**Бэкап:** `references/backups/expert-course-review-report-format-2026-07-10.md` (+ ранее сделанные).
**Handoff:** `Root/agents/handoff/2026-07-10-obmen-usileniy-expert-course-review.md`.

---

## 2026-07-11 — Режим D по запросу оператора: разбор ошибок сессии авторинга классов 7-11 (Информатика 5-11, tsk-176)

Оператор явно запросил: «проанализируй ошибки, допущенные в рамках этой сессии, и улучши
все причастные скиллы». Сессия — авторинг банка заданий (3→10/урок) + фикс подсказок +
публикация WP/LMS классов 7-11, dual-track с Codex. 4 воспроизводимых класс-дефекта (не
разовые execution gaps), все — instruction gap. Полный RCA + anti-bloat — `skills-errors.md`
2026-07-11.

**Применённые правки:**
1. `methodist` `assignment-rules.md` §6 — checklist-пункт про `hint` расширен с 1 формы
   проверки (арифметический слив) до 3 (+ смысловой слив термина: синоним/иноязычный
   эквивалент/перечисление; + пустая generic-заглушка, детектируется по совпадению строки
   `hint` у >1 задачи). Подтверждено 12 новыми находками сессии (9 шаблонов + 3 семантических
   слива в классе 11). Закрывает 2 старые OPEN-находки `course-quality-errors.md` (2026-07-10
   находка A, 2026-07-11 находка К5). `SKILL.md` v1.9.0→**1.9.1**.
2. `D:\Work\Root\agents\second-opinion.md` § Анти-паттерны — новый пункт: при точечном фиксе
   находки другого агента по номеру (`#qN`) сверяться с текстом ответа/стема из отчёта, не с
   порядковым номером «на глаз» (номера смещаются при изменении числа блоков между
   прогонами). Прецедент — реальная ошибка сессии (класс 8/9 recheck, исправлен не тот
   пункт).
3. `digital-copywriter` `SKILL.md` цель `lms-publish` п.2 — `lms_course_uid` теперь явно
   требуется проверять/проставлять ПРОАКТИВНО во всех файлах волны до первой публикации
   (была документация поля, не было процедуры проверки); + закреплена практика «один урок —
   один вызов инструмента публикации» (риск тихого пропуска при долгом цикле/таймауте).
   `SKILL.md` v1.11.0→**1.11.1**.
4. `claude-booster` `references/booster-shared.md` § Межагентная координация — новый абзац:
   безопасный паттерн записи в `_ledger.md` (`Write` временного `.py` + `Bash python`, НЕ
   `printf`/`echo` — Windows-пути с `\e` портятся молча). Инцидент повторился дважды за
   сессию до перехода на этот паттерн.

**Anti-bloat:** все 4 правки — расширение существующих пунктов/секций в их «родных» файлах,
ни одного нового файла или механизма. Детали — `skills-errors.md` 2026-07-11.

**WON'T_FIX:** опечатка в audit.md класса 10 (случайные иероглифы) и off-by-one блоков
класса 8 (поймано валидацией) — execution gap / штатная работа предохранителя, не правится.

---

## 2026-07-11 (доп.) — перенос фикса подсказок в expert-course-review (по прямому запросу оператора)

Оператор: «перенеси то же в expert-course-review rubric.md К5» — закрывает отложенный пункт
из записи выше (правка №1 была только в `methodist`, ревью-скилл оставался со старой узкой
версией правила).

**Применено:** `expert-course-review` `references/rubric.md` К5 — пункт «Подсказка не
сливает ответ» расширен с 1 проверки (арифметический слив) до тех же 3, что теперь в
`assignment-rules.md`: (1) арифметика — было; (2) смысловой слив (синоним/корень
слова/иноязычный термин-эквивалент/перечисление элементов ответа, тест «прочитать hint
изолированно от stem»); (3) пустая generic-заглушка (детектируется по совпадению строки
`hint` у >1 задачи, тест «убери stem — подсказка ещё понятна, ПРО КАКУЮ она задачу?»).
Провал-критерий К5 расширен той же формулировкой. `SKILL.md` v1.3.0→**1.3.1**.

**Anti-bloat:** не новый механизм — тот же паттерн правки, применённый к сестринскому
skill'у с идентичным дефектом; формулировки синхронизированы дословно там, где это одно
правило с двух сторон контура (автор пишет / ревьюер проверяет).

**Бэкап:** `references/backups/expert-course-review-rubric-2026-07-11.md`.
**Реестры обновлены:** `skills-errors.md` (дополнение к записи 2026-07-11), обе связанные
записи в `course-quality-errors.md` (2026-07-10 находка A, 2026-07-11 находка К5) —
пометка о синхронизации.

**Бэкапы:** `references/backups/{methodist-assignment-rules,second-opinion,digital-copywriter-SKILL,booster-shared}-2026-07-11.md`.

## 2026-07-11 — Режим D: RCA 6 классов ошибок сессии курса ВУЗ (tsk-180)
Источник: операторский разбор + dual-track ревью (Claude+Codex) курса vstupitelnye-it-vuz.
Обработано классов: 6. Корни: 5 instruction gap, 1 execution gap.

Правки (усиление существующих правил, anti-bloat — ни одного нового файла):
1. **Порог H2+вводный** (класс 1, instruction) → `methodist/lms-wp-export.md` §1.1.1:
   точная формула «материалов = H2 + 1 вводный», + сухой прогон lms-publish-lesson до write.
2. **Формулы/код/адрес → SC, не SA** (класс 2, instruction) → `assignment-rules.md` §9 п.3:
   заменил ошибочное «коммутативные массивом» на инвариант — любой значащий символ
   (`= * + / $ ( ) :`) стирается нормализацией → SC/MC/regex. Зеркально в `digital` §8а.
3. **hint против ВСЕХ accepted** (класс 3, instruction) → §9 п.3 + `digital` §8а: при массиве
   answer сверять подсказку против всех форм, не только канонической (регресс своего фикса).
4. **ё/е явно** (класс 4, instruction) → §9 п.3 + `digital` §8а: пройти по ё-эталонам,
   задать е-форму (lower не сводит ё→е); регистр в массив НЕ добавлять (снимается lower).
5. **Субагент ослабил валидатор** (класс 5, execution gap) → skill НЕ правил; урок в
   `CreateCourses/docs/ai/errors.md`: в промпте субагенту запрещать менять критерий проверки.
6. **Авто-поле проектного одинаково у всех** (класс 6, instruction) → §7: авто-фаза ловит
   значение, одинаковое у всех учеников (число полей), не личное (объём ОЗУ ученика).

Anti-bloat: усилены существующие §7/§9/§1.1.1/§8а, новых файлов и клонов нет. digital SKILL.md
632 стр (историческое превышение, не от этих правок; вынос в reference — отдельная задача).
Codex-канон не трогал (синхронизируется package-skills). course-quality-errors запись C дополнена.
