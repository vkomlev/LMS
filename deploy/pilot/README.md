# Экземпляр «pilot» — копия платформы для школы коллег (tsk-764)

Пятый экземпляр платформы на той же машине `lms-spw-vds` (5.42.102.20).
Не новая архитектура: механизм «много экземпляров из одного кода» здесь уже
работает — боевой (`learn`) плюс три яруса полигона. Пилот повторяет образец.

**Развилка решена (бриф `Root/Docs/briefs/tsk-764-pilot-rasprostraneniya-lms.md`):**
общий код, разные базы. Форк отклонён — контур правится по 10–15 раз в неделю,
каждая правка делалась бы дважды. Мультиарендность не строим: она не нужна,
пока у каждой школы своя база.

Экземпляр идёт на **той же ветке**, что боевой (`main` у LMS, `master` у SPW).
Отдельной ветки, в отличие от полигона, нет и не должно быть.

## Карта экземпляров на машине

| Экземпляр | База | LMS | SPW | Служба | nginx |
|---|---|---|---|---|---|
| боевой | `learn` | :8000 | :3000 | `lms`, `spw` | `lms.conf`, `spw.conf`, `study.conf` |
| полигон dev | `poligon_dev` | :8010 | :3010 | `lms-poligon-dev`, `spw-poligon-dev` | `poligon.conf` |
| полигон test | `poligon_test` | :8011 | :3011 | `lms-poligon-test`, `spw-poligon-test` | `poligon.conf` |
| полигон stage | `poligon_stage` | :8012 | :3012 | `lms-poligon-stage`, `spw-poligon-stage` | `poligon.conf` |
| **pilot** | **`pilot`** | **:8020** | **:3020** | **`lms-pilot`, `spw-pilot`** | **`pilot.conf`** |

Домен: `pilot.victor-komlev.ru` — один на весь экземпляр (у боевого их два,
`learn.*` и `api.learn.*`, но пилоту разделять API не для кого).

Имя `pilot` — инфраструктурное, наше. Название и логотип школы коллег живут в
переменных `BRAND_*` и меняются без переименования баз, служб и домена.

## Что делает оператор (без него не начать)

Оба шага — в чужих панелях, у агента туда доступа нет.

**Шаг 1. DNS у reg.ru** (домен обслуживают `ns1.reg.ru`/`ns2.reg.ru`).
Добавить A-запись: `pilot` → `5.42.102.20`.
Как понять, что готово: `nslookup pilot.victor-komlev.ru` отдаёт `5.42.102.20`.
Обычно несколько минут, изредка до часа.

**Шаг 2. База в панели Timeweb** («Базы данных» → кластер → «Пользователи»/«Базы»).
Создать пользователя `pilot_app` и базу `pilot` с владельцем `pilot_app`.
Пароль — не придумывать: он уже сгенерирован и лежит в `/opt/lms-pilot/.env`.
Показать его командой:

```bash
ssh lms-spw-vds "sudo grep '^DATABASE_URL' /opt/lms-pilot/.env"
```

Как понять, что готово: в списке баз кластера появилась `pilot`, размер около нуля.
База создаётся **пустой** — наших учеников там быть не должно ни одной строки.

## Первичная настройка (один раз, на сервере)

```bash
ssh lms-spw-vds

# --- журналы ---
sudo mkdir -p /var/log/lms-pilot /var/log/spw-pilot
sudo chown app:app /var/log/lms-pilot /var/log/spw-pilot

# --- право пользователю app рестартовать свои службы ---
# Без него скрипты выката падают на `sudo systemctl restart` (у app разрешены
# поимённо только уже существующие службы, /etc/sudoers.d/app-deploy).
printf '%s\n' \
  'app ALL=(root) NOPASSWD: /usr/bin/systemctl restart lms-pilot' \
  'app ALL=(root) NOPASSWD: /usr/bin/systemctl restart spw-pilot' \
  | sudo tee -a /etc/sudoers.d/app-deploy > /dev/null
sudo visudo -c -f /etc/sudoers.d/app-deploy   # обязательно: битый файл ломает sudo целиком

# --- LMS ---
# Каталог создаёт root и сразу отдаёт app — иначе git clone упрётся в права.
sudo mkdir -p /opt/lms-pilot /opt/spw-pilot
sudo chown app:app /opt/lms-pilot /opt/spw-pilot

# Клонировать по SSH-ключу (репозиторий SPW закрытый; у LMS так же ходит боевой чекаут).
sudo -u app git clone --branch main git@github.com:vkomlev/LMS.git /opt/lms-pilot
cd /opt/lms-pilot
sudo -u app python3.11 -m venv venv
sudo -u app ./venv/bin/pip install --upgrade -r requirements.txt

sudo -u app cp deploy/pilot/.env.lms.pilot.example /opt/lms-pilot/.env
sudo -u app nano /opt/lms-pilot/.env          # заполнить REPLACE_ME
sudo chmod 600 /opt/lms-pilot/.env

# схема на пустую базу
sudo -u app bash -lc 'cd /opt/lms-pilot && source venv/bin/activate && \
  DATABASE_URL=$(grep ^DATABASE_URL .env | cut -d= -f2-) alembic upgrade head'

sudo cp deploy/pilot/lms-pilot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lms-pilot

# --- SPW ---
sudo -u app git clone --branch master git@github.com:vkomlev/spw.git /opt/spw-pilot
cd /opt/spw-pilot
sudo -u app cp deploy/pilot/.env.spw.pilot.example /opt/spw-pilot/.env
sudo -u app nano /opt/spw-pilot/.env          # BRAND_NAME и остальное
# логотип школы — в public/ ПОД ИМЕНЕМ ИЗ BRAND_LOGO_URL, до сборки

corepack enable
sudo -u app pnpm install
sudo -u app pnpm build     # .env читается сборкой: страница /onboarding статическая
sudo cp deploy/pilot/spw-pilot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spw-pilot

# --- nginx ---
# СНАЧАЛА без TLS-блока (сертификата ещё нет) — certbot допишет сам.
sudo cp /opt/lms-pilot/deploy/pilot/nginx-pilot.conf /etc/nginx/sites-available/pilot.conf
sudo ln -s /etc/nginx/sites-available/pilot.conf /etc/nginx/sites-enabled/pilot.conf
sudo nginx -t && sudo systemctl reload nginx
# `nginx -t` обязателен ДО reload: тот же процесс обслуживает боевой кабинет.

# --- сертификат (после того, как DNS доехал) ---
sudo certbot --nginx -d pilot.victor-komlev.ru
```

## Проверка изоляции — главное после установки

Проверять запросом, а не конфигурацией. Прецедент — tsk-614: три яруса полигона
делили одну сборку, и обращения `/api/v1/*` уходили в настоящую систему, хотя
в конфигурации всё выглядело раздельным.

```bash
# 1. Новый экземпляр не видит наших данных: в его базе ноль курсов и ноль людей,
#    в боевой — сотни. Одинаковый ответ = базы перепутаны.
cd /opt/lms-pilot && sudo -u app venv/bin/python deploy/pilot/isolation_check.py

# 2. Запрос с домена пилота доходит до ЕГО процесса, а не до боевого.
#    Проверяется по журналу: строка обязана появиться в /var/log/lms-pilot/app.log
#    и НЕ появиться в /var/log/lms/app.log.
curl -s "https://pilot.victor-komlev.ru/api/v1/health?probe=tsk764" -o /dev/null
sudo tail -3 /var/log/lms-pilot/app.log
sudo grep -c 'probe=tsk764' /var/log/lms/app.log    # ожидаем 0

# 3. Боевой экземпляр не перезапускался и отвечает.
systemctl is-active lms spw lms-pilot spw-pilot
systemctl show lms -p ActiveEnterTimestamp
curl -s -o /dev/null -w '%{http_code}\n' https://learn.victor-komlev.ru/
```

## Повторный выкат

```bash
ssh lms-spw-vds
sudo -u app bash /opt/lms-pilot/deploy/pilot/deploy-lms-pilot.sh
sudo -u app bash /opt/spw-pilot/deploy/pilot/deploy-spw-pilot.sh
```

Оба скрипта останавливаются, если `DATABASE_URL` ведёт не в базу `pilot`, и
печатают состояние боевых служб после выката.

## Права в кластере — ловушка, которую нашли живьём (03.09.2026)

**Панель Timeweb при создании пользователя выдаёт ему права на ВСЕ базы кластера.**
Роль `pilot_app`, созданная для этого экземпляра, получила чтение, изменение и
удаление на всех 89 таблицах боевой `learn` и на базе контента `content_backbone`.
Не «увидела список баз» — прочитала 111 человек, 21 089 сдач, 785 курсов.
Тем же путём это давно было верно для `poligon_stage_app` и `poligon_test_app`:
README полигона утверждал «ожидаем permission denied», и это никогда не проверяли.

Права отозваны в обе стороны (от владельцев `lms_prod`, `cb_prod`, `pilot_app`).
Проверка, которая это ловит:

```bash
cd /opt/lms-pilot && sudo -u app venv/bin/python deploy/pilot/isolation_check.py
```

Строка «базы, куда пускает эта роль» обязана перечислять только `pilot`
(плюс служебная `default_db`). Появились `learn` или `content_backbone` — права
раздались заново.

**Поэтому при создании ЛЮБОГО следующего пользователя в панели проверку надо
повторять.** Сама по себе она копеечная, а без неё «у каждой школы своя база»
остаётся словами: базы разные, а роль ходит во все.

## Что помнить

- **Пятый набор выкатов и журналов.** Разбор tsk-764 назвал это главным счётом
  пилота: сопровождение на время пилота держит оператор лично, дальше подрядчик.
  Срок пилота — месяц (решение оператора 03.09.2026).
- **Кластер базы общий.** Лимит подключений 200 на все пять экземпляров.
  Смотреть `pg_stat_activity`, если начнутся отказы.
- **Память машины.** 3.8 ГБ на всё; пятый экземпляр добавляет два процесса.
  Сборка SPW на сервере — самый жадный момент, делать её не в час занятий.
- **`.env` вне git.** Чужая правка не всплывёт при слиянии. Порядок правки —
  как у боевых `.env`: захват в `Root/agents/`, бэкап рядом с файлом, рестарт.
- **Логотип школы тоже вне git.** Лежит в `/opt/spw-pilot/public/brand-logo.png`,
  запасная копия — `/opt/pilot-assets/brand-logo.png`. `git reset --hard` его не
  трогает (файл не под git), но заново склонированный чекаут останется без него:
  восстановить копией из `/opt/pilot-assets/` ДО `pnpm build`. В репозиторий он
  не кладётся сознательно — это чужой фирменный знак, а не наш ресурс.
- **Использование знака согласовано** (оператор, 04.09.2026). Брендбук требует
  согласования для знака без текстовой части в электронных носителях (блок 1,
  стр. 14) — в шапке кабинета знак стоит рядом со словом «Дипломат», разрешение
  получено. Меняется композиция знака и надписи — согласовывать заново.
