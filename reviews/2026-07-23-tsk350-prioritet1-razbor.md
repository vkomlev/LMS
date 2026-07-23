# tsk-350 — разбор приоритета 1 («разные приложенные файлы»)

26 пар, где текст/числа/формула/ответ совпали, а различались приложенные файлы. Разбор: имя файла `/api/v1/media/<sha256>.<ext>` — это SHA256 самого содержимого (CAS-хранилище), поэтому **хэш уже посчитан и лежит в имени**: одинаковое имя = байт-в-байт один файл. Плюс сверка ID первоисточника и (для 3 пар «разные байты») скачивание файлов с прода и сравнение содержимого.

## Итог

- **точно дубль:** 15 пар
- **вероятно дубль** (совпал короткий ответ, стоит взглянуть): 10 пар
- **не дубль** (файлы реально разные): 1 пар
- Кластеров дублей (с учётом транзитивности): 22

Как определялся дубль: (1) совпал sha файла → один файл; (2) совпал ID первоисточника → одна задача; (3) для разных банков — совпал текст + числа + формула + **ответ** (многозначный ответ исключает случайность); (4) три пары «разные байты» сверены по содержимому — две оказались тем же файлом в другом формате (xls↔ods, csv с `,` и `;`), одна — реально разными данными.

## Кластеры дублей — рекомендация к скрытию

Канон = версия **с файлом данных** (иначе задание без данных нерешаемо), при равенстве — лучший источник (каталог `ext:d4` > навигатор > ТГ-разбор).

### Кластер 1 — ТОЧНО дубли (2 задания)

_один первоисточник polyakov #4406_

- **ОСТАВИТЬ (канон)** [2058 — в LMS](https://learn.victor-komlev.ru/courses/id-138/task/ext%3Apolyakov%3Apilot%3Amini50%3A4406) · есть файл данных · [Поляков #4406](https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=4406)
- **скрыть** [3203 — в LMS](https://learn.victor-komlev.ru/courses/id-138/task/tg%3Aege%3A702) · БЕЗ файла данных · [ТГ-пост (Поляков #4406)](https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=4406)

### Кластер 2 — ТОЧНО дубли (2 задания)

_один первоисточник kompege #2054_

- **ОСТАВИТЬ (канон)** [3323 — в LMS](https://learn.victor-komlev.ru/courses/id-138/task/tg%3Aege%3A524) · есть файл данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/524)
- **скрыть** [2133 — в LMS](https://learn.victor-komlev.ru/courses/id-138/task/ext%3Ad4%3Akompege%3A20260602%3A2054) · БЕЗ файла данных · [КомпЕГЭ #2054](https://kompege.ru/api/v1/task/2054)

### Кластер 3 — ТОЧНО дубли (2 задания)

_один первоисточник kompege #17622_

- **ОСТАВИТЬ (канон)** [3320 — в LMS](https://learn.victor-komlev.ru/courses/id-138/task/tg%3Aege%3A528) · есть файл данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/528)
- **скрыть** [2136 — в LMS](https://learn.victor-komlev.ru/courses/id-138/task/ext%3Ad4%3Akompege%3A20260602%3A17622) · БЕЗ файла данных · [КомпЕГЭ #17622](https://kompege.ru/api/v1/task/17622)

### Кластер 4 — ТОЧНО дубли (2 задания)

_один первоисточник kompege #23262_

- **ОСТАВИТЬ (канон)** [3275 — в LMS](https://learn.victor-komlev.ru/courses/id-138/task/tg%3Aege%3A598) · есть файл данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/598)
- **скрыть** [2137 — в LMS](https://learn.victor-komlev.ru/courses/id-138/task/ext%3Ad4%3Akompege%3A20260602%3A23262) · БЕЗ файла данных · [КомпЕГЭ #23262](https://kompege.ru/api/v1/task/23262)

### Кластер 5 — вероятные дубли (3 задания)

_разные банки, совпал ответ 5 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [2167 — в LMS](https://learn.victor-komlev.ru/courses/id-149/task/ext%3Ad4%3Akompege%3A20260602%3A17876) · есть файл данных · [КомпЕГЭ #17876](https://kompege.ru/api/v1/task/17876)
- **скрыть** [2315 — в LMS](https://learn.victor-komlev.ru/courses/id-149/task/ext%3Ad4%3Asdamgia%3A20260602%3A70549) · есть файл данных · [Решу ЕГЭ #70549](https://inf-ege.sdamgia.ru/problem?id=70549)
- **скрыть** [3593 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3A2839ef30) · БЕЗ файла данных · [WP-навигатор (Яндекс.Учебник)](https://education.yandex.ru/ege/inf/task/c1faf2ab-0ceb-416d-a848-66449ee138ba)

### Кластер 6 — ТОЧНО дубли (3 задания)

_разные банки, но совпал многозначный ответ 1204502 (случайность исключена) + текст + числа_

- **ОСТАВИТЬ (канон)** [2307 — в LMS](https://learn.victor-komlev.ru/courses/id-146/task/ext%3Ad4%3Asdamgia%3A20260602%3A27415) · есть файл данных · [Решу ЕГЭ #27415](https://inf-ege.sdamgia.ru/problem?id=27415)
- **скрыть** [2190 — в LMS](https://learn.victor-komlev.ru/courses/id-146/task/ext%3Ad4%3Akompege%3A20260602%3A17) · БЕЗ файла данных · [КомпЕГЭ #17](https://kompege.ru/api/v1/task/17)
- **скрыть** [3462 — в LMS](https://learn.victor-komlev.ru/courses/id-146/task/tg%3Aege%3A221) · есть файл данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/221)

### Кластер 7 — ТОЧНО дубли (2 задания)

_один первоисточник sdamgia #72576_

- **ОСТАВИТЬ (канон)** [2306 — в LMS](https://learn.victor-komlev.ru/courses/id-146/task/ext%3Ad4%3Asdamgia%3A20260602%3A72576) · есть файл данных · [Решу ЕГЭ #72576](https://inf-ege.sdamgia.ru/problem?id=72576)
- **скрыть** [3463 — в LMS](https://learn.victor-komlev.ru/courses/id-146/task/tg%3Aege%3A220) · БЕЗ файла данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/220)

### Кластер 8 — ТОЧНО дубли (2 задания)

_один первоисточник sdamgia #72603_

- **ОСТАВИТЬ (канон)** [2310 — в LMS](https://learn.victor-komlev.ru/courses/id-146/task/ext%3Ad4%3Asdamgia%3A20260602%3A72603) · есть файл данных · [Решу ЕГЭ #72603](https://inf-ege.sdamgia.ru/problem?id=72603)
- **скрыть** [3456 — в LMS](https://learn.victor-komlev.ru/courses/id-146/task/tg%3Aege%3A260) · БЕЗ файла данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/260)

### Кластер 9 — вероятные дубли (2 задания)

_разные банки, совпал ответ 103 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [2348 — в LMS](https://learn.victor-komlev.ru/courses/id-141/task/ext%3Ad4%3Asdamgia%3A20260602%3A70537) · есть файл данных · [Решу ЕГЭ #70537](https://inf-ege.sdamgia.ru/problem?id=70537)
- **скрыть** [3578 — в LMS](https://learn.victor-komlev.ru/courses/id-1388/task/wp_nav%3A10%3A8391ada6) · БЕЗ файла данных · [WP-навигатор (Яндекс.Учебник)](https://education.yandex.ru/ege/inf/task/abcb1c2b-320b-4dc1-ae72-1db00837e1ca)

### Кластер 10 — ТОЧНО дубли (2 задания)

_приложен байт-в-байт один файл (совпал sha256)_

- **ОСТАВИТЬ (канон)** [3105 — в LMS](https://learn.victor-komlev.ru/courses/id-160/task/tg%3Aege%3A865) · есть файл данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/865)
- **скрыть** [9481 — в LMS](https://learn.victor-komlev.ru/courses/id-160/task/crylov%3Av1t9) · есть файл данных · источник: Крылов вариант 1, задание 9

### Кластер 11 — ТОЧНО дубли (2 задания)

_приложен байт-в-байт один файл (совпал sha256)_

- **ОСТАВИТЬ (канон)** [3153 — в LMS](https://learn.victor-komlev.ru/courses/id-151/task/tg%3Aege%3A800) · есть файл данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/800)
- **скрыть** [9510 — в LMS](https://learn.victor-komlev.ru/courses/id-151/task/crylov%3Av5t24) · есть файл данных · источник: Крылов вариант 5, задание 24

### Кластер 12 — вероятные дубли (2 задания)

_разные банки, совпал ответ 38 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [3295 — в LMS](https://learn.victor-komlev.ru/courses/id-1381/task/tg%3Aege%3A569) · есть файл данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/569)
- **скрыть** [3477 — в LMS](https://learn.victor-komlev.ru/courses/id-1381/task/wp_nav%3A3%3A30622dd3) · БЕЗ файла данных · [WP-навигатор (Яндекс.Учебник)](https://education.yandex.ru/ege/collections/b24b2dd9-52dc-42a7-b9f8-766c46e4c737/task/3)

### Кластер 13 — ТОЧНО дубли (2 задания)

_разные банки, но совпал многозначный ответ 9997 (случайность исключена) + текст + числа_

- **ОСТАВИТЬ (канон)** [3296 — в LMS](https://learn.victor-komlev.ru/courses/id-145/task/tg%3Aege%3A568) · есть файл данных · [ТГ-пост @cyberguru_ege](https://t.me/cyberguru_ege/568)
- **скрыть** [3471 — в LMS](https://learn.victor-komlev.ru/courses/id-145/task/wp_nav%3A17%3Ad2e2fbd3) · БЕЗ файла данных · [WP-навигатор (Яндекс.Учебник)](https://education.yandex.ru/ege/collections/a97d888a-5402-4044-bb08-35bcc66f9ec7/task/17)

### Кластер 14 — вероятные дубли (2 задания)

_разные банки, совпал ответ 85 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [3807 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3Afffd3d17) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=10592)
- **скрыть** [3500 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3A949f9e2c) · БЕЗ файла данных · [WP-навигатор (Яндекс.Учебник)](https://education.yandex.ru/ege/inf/task/250cf1cc-5326-4025-b07c-9f4862d5904e)

### Кластер 15 — ТОЧНО дубли (2 задания)

_приложен байт-в-байт один файл (совпал sha256)_

- **ОСТАВИТЬ (канон)** [3549 — в LMS](https://learn.victor-komlev.ru/courses/id-1387/task/wp_nav%3A9%3A1c180527) · есть файл данных · [WP-навигатор (Яндекс.Учебник)](https://education.yandex.ru/ege/inf/task/6b618986-8c2b-4719-9f2e-5b3ab46b01e5)
- **скрыть** [4220 — в LMS](https://learn.victor-komlev.ru/courses/id-1387/task/wp_nav%3A9%3Aad5f105f) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=6925)

### Кластер 16 — ТОЧНО дубли (2 задания)

_содержимое файлов сверено — совпало_

- **ОСТАВИТЬ (канон)** [3562 — в LMS](https://learn.victor-komlev.ru/courses/id-1387/task/wp_nav%3A9%3A758e1c43) · есть файл данных · [WP-навигатор (Яндекс.Учебник)](https://education.yandex.ru/ege/inf/task/82c97d22-18da-44ce-aafa-9e25f9e55301)
- **скрыть** [4225 — в LMS](https://learn.victor-komlev.ru/courses/id-1387/task/wp_nav%3A9%3Ab7698e75) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=7030)

### Кластер 17 — ТОЧНО дубли (2 задания)

_разные банки, но совпал многозначный ответ 165697 (случайность исключена) + текст + числа_

- **ОСТАВИТЬ (канон)** [4168 — в LMS](https://learn.victor-komlev.ru/courses/id-1381/task/wp_nav%3A3%3Aedb22c64) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=5749)
- **скрыть** [3681 — в LMS](https://learn.victor-komlev.ru/courses/id-1381/task/wp_nav%3A3%3Ad79ae04e) · БЕЗ файла данных · [WP-навигатор (Яндекс.Учебник)](https://education.yandex.ru/ege/task/6f3286d9-c5db-43c4-8142-0116f91d35c1)

### Кластер 18 — вероятные дубли (2 задания)

_разные банки, совпал ответ 22 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [4150 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3Ae8ae8b24) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=5414)
- **скрыть** [4338 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3A3ffe5c01) · БЕЗ файла данных · [WP-навигатор (первоисточник)](https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=5863)

### Кластер 19 — вероятные дубли (2 задания)

_разные банки, совпал ответ 32 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [4151 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3Ad6863871) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=5415)
- **скрыть** [4339 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3Ae2f5a01b) · БЕЗ файла данных · [WP-навигатор (первоисточник)](https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=5864)

### Кластер 20 — вероятные дубли (2 задания)

_разные банки, совпал ответ 11 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [4152 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3A6f4de7a7) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=5416)
- **скрыть** [4340 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3A49d9f09c) · БЕЗ файла данных · [WP-навигатор (первоисточник)](https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=5865)

### Кластер 21 — вероятные дубли (2 задания)

_разные банки, совпал ответ 19 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [4153 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3Aabeb0c1a) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=5417)
- **скрыть** [4341 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3A1e1def2b) · БЕЗ файла данных · [WP-навигатор (первоисточник)](https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=5866)

### Кластер 22 — вероятные дубли (2 задания)

_разные банки, совпал ответ 42 + текст + числа + формула (короткий ответ — стоит взглянуть)_

- **ОСТАВИТЬ (канон)** [4155 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3A8d274da8) · есть файл данных · [WP-навигатор (первоисточник)](https://kompege.ru/task?id=5419)
- **скрыть** [4343 — в LMS](https://learn.victor-komlev.ru/courses/id-1398/task/wp_nav%3A22%3Af43a5e19) · БЕЗ файла данных · [WP-навигатор (первоисточник)](https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=5868)

## Не дубль

- 3790 / 3793 — содержимое файлов реально разное. Оставить оба.
- **оставить** [3790 — в LMS](https://learn.victor-komlev.ru/courses/id-153/task/wp_nav%3A26%3A6ec606c1) · есть файл данных · [WP-навигатор (первоисточник)](https://inf-ege.sdamgia.ru/problem?id=70553)
- **оставить** [3793 — в LMS](https://learn.victor-komlev.ru/courses/id-153/task/wp_nav%3A26%3A7e7af267) · есть файл данных · [WP-навигатор (первоисточник)](https://inf-ege.sdamgia.ru/problem?id=76129)

