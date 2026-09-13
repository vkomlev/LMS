"""Публичная форма захвата заявки на продающих лендингах сайта (tsk-929).

Кнопка «Записаться» на лендингах victor-komlev.ru вела напрямую в личный
Telegram (`t.me/Vvkomlev`) — заявка нигде не фиксировалась (0 по каналу
«Сайт» за всё время, см. `tsk-827`). Решение оператора 13.09: форма встаёт
ПЕРЕД переходом в Telegram, а не заменяет его — посетитель оставляет контакт,
заявка сразу пишется в LMS, а дальше открывается диалог как раньше.

Без cookie гостевой сессии (в отличие от `guest_quiz.py`): форма вызывается
JS-фетчем с чужого домена (WordPress), и cookie с домена LMS туда не долетит
без дополнительной SameSite/Secure возни, которая здесь ничего не даёт —
дедуп по сессии тут не нужен, повторная заявка того же человека не страшнее,
чем сейчас (маркетолог видит дубли в кабинете так же, как по Авито).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.lead import WebsiteLeadRequest, WebsiteLeadResponse
from app.services import lead_service
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/public/leads", tags=["public_leads"])
_settings = Settings()

#: Код канала — справочник заведён миграцией 20260801_120000_tsk505_pricing_and_leads.
_WEBSITE_SOURCE_CODE = "website"

#: Живой человек оставляет контакт с лендинга один раз, редко — дважды (поправил
#: телефон). Как у гостевого квиза (`quiz_lead`): щедро для человека, тесно для спама.
_IP_LIMIT = 10
_WINDOW_SECONDS = 3600


def _client_ip(request: Request) -> str:
    """IP клиента из-за реверс-прокси — `request.client.host` был бы адресом прокси."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post(
    "",
    response_model=WebsiteLeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Заявка с формы на продающем лендинге сайта",
)
async def submit_website_lead(
    body: WebsiteLeadRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> WebsiteLeadResponse:
    """Принять контакт с формы на лендинге перед переходом в Telegram.

    Honeypot `hp`: бот заполняет все поля формы, человек скрытое CSS-полем не
    видит и не трогает. Заполнено — молча отвечаем так же, как при успехе, но
    лида не заводим: явная ошибка подсказала бы боту, какое поле убрать.
    """
    if body.hp:
        logger.info("website_lead: honeypot сработал, page=%s", body.page)
        return WebsiteLeadResponse(lead_id=0)

    ip = _client_ip(request)
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(
        redis, f"website_lead:{ip}", max_requests=_IP_LIMIT, window_seconds=_WINDOW_SECONDS
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много заявок с этого адреса"
        )

    source_id = await lead_service.get_source_id_by_code(db, _WEBSITE_SOURCE_CODE)
    if source_id is None:
        logger.error("website_lead: в справочнике нет канала '%s'", _WEBSITE_SOURCE_CODE)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Приём заявок временно недоступен."
        )

    lead_id = await lead_service.create_lead(
        db,
        source_id=source_id,
        source_detail=body.page,
        full_name=body.full_name,
        contact=body.contact,
        note=None,
        created_by=None,
    )
    logger.info("website_lead: заявка с лендинга page=%s lead_id=%s", body.page, lead_id)
    return WebsiteLeadResponse(lead_id=lead_id)


#: Виджет отдаётся тем же доменом, что принимает POST — фетч внутри iframe
#: идёт same-origin, CORS не участвует вовсе (в отличие от прямой формы на
#: WordPress). Причина такого решения — `<form>/<input>/<script>` вырезаются
#: WordPress при записи через REST API даже у администратора с
#: `unfiltered_html`, а `<iframe>` эту фильтрацию переживает (проверено
#: живьём на лендинге 4609, tsk-929).
_WIDGET_HTML_TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ margin:0; padding:16px; font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;
          background:#fafafa; box-sizing:border-box; }}
  * {{ box-sizing:border-box; }}
  p.title {{ margin:0 0 14px; font-weight:600; font-size:18px; }}
  label {{ display:block; margin-bottom:4px; }}
  .field {{ margin-bottom:12px; }}
  input {{ width:100%; padding:10px; border:1px solid #ccc; border-radius:8px; font-size:16px; }}
  button {{ width:100%; padding:12px; border:none; border-radius:8px; background:#2aabee;
            color:#fff; font-size:16px; font-weight:600; cursor:pointer; }}
  button:disabled {{ opacity:.6; cursor:default; }}
  #status {{ margin:10px 0 0; font-size:14px; min-height:18px; }}
  .hp {{ position:absolute; left:-9999px; top:-9999px; }}
</style></head>
<body>
  <form id="lead-form">
    <p class="title">Оставьте контакт — отвечу в Телеграме</p>
    <div class="field">
      <label for="full_name">Как вас зовут?</label>
      <input id="full_name" name="full_name" type="text" required maxlength="200">
    </div>
    <div class="field">
      <label for="contact">Телефон, Telegram или e-mail</label>
      <input id="contact" name="contact" type="text" required minlength="3" maxlength="200">
    </div>
    <div class="hp" aria-hidden="true">
      <label for="hp">Не заполняйте это поле</label>
      <input id="hp" name="hp" type="text" tabindex="-1" autocomplete="off">
    </div>
    <button type="submit" id="submit-btn">Отправить заявку</button>
    <p id="status" role="status"></p>
  </form>
<script>
(function () {{
  var PAGE = {page_json};
  var form = document.getElementById('lead-form');
  var status = document.getElementById('status');
  var btn = document.getElementById('submit-btn');
  form.addEventListener('submit', function (e) {{
    e.preventDefault();
    btn.disabled = true;
    status.style.color = '#333';
    status.textContent = 'Отправляю...';
    fetch('/api/v1/public/leads', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        full_name: document.getElementById('full_name').value,
        contact: document.getElementById('contact').value,
        page: PAGE,
        hp: document.getElementById('hp').value
      }})
    }})
      .then(function (resp) {{
        if (resp.status === 429) {{
          throw new Error('Слишком много заявок с вашего адреса, попробуйте позже.');
        }}
        if (!resp.ok) {{
          throw new Error('Не получилось отправить, попробуйте ещё раз или напишите в Телеграм.');
        }}
        return resp.json();
      }})
      .then(function () {{
        form.reset();
        status.style.color = '#1a7a1a';
        status.textContent = 'Спасибо! Заявка получена — теперь можно написать мне в Телеграм кнопкой ниже.';
        btn.textContent = 'Отправлено';
      }})
      .catch(function (err) {{
        status.style.color = '#c0392b';
        status.textContent = err.message;
        btn.disabled = false;
      }});
  }});
}})();
</script>
</body></html>"""


@router.get("/widget", response_class=HTMLResponse, include_in_schema=False)
async def website_lead_widget(
    page: str = Query(..., min_length=1, max_length=200),
) -> HTMLResponse:
    """Отдать HTML-страницу с формой для встраивания через `<iframe>` на лендинге.

    Обходит ограничение WordPress: запись `<form>/<input>/<script>` через REST
    API вырезается движком независимо от прав пользователя, а `<iframe src=...>`
    эту фильтрацию переживает (см. `_WIDGET_HTML_TEMPLATE`). `page` экранируется
    через `json.dumps`, а `</` дополнительно ломается, чтобы значение параметра
    не могло закрыть тег `<script>` раньше времени.
    """
    page_json = json.dumps(page).replace("</", "<\\/")
    html = _WIDGET_HTML_TEMPLATE.format(page_json=page_json)
    return HTMLResponse(content=html)
