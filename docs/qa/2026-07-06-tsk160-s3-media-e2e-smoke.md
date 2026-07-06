# tsk-160 / ADR-0047 — живой e2e-смоук S3 media-редиректа

**Дата:** 2026-07-06
**Найдено:** предыдущий `/review-gate` прогон (ОТКЛОНЕНО) справедливо указал, что первый
e2e-тест был выполнен по-настоящему (реальный файл, реальный HTTP 200 через живой
production-эндпоинт), но тестовый объект был удалён сразу после проверки — независимый
ревьюер не смог его переподтвердить и увидел только 403 на несуществующий ключ, что
выглядело как неподтверждённое утверждение в cross-project docs.

**Фикс:** повторный прогон с постоянным canary-объектом, который **остаётся в bucket**
для независимой переверификации в любой момент (не будет удалён после этой проверки).

## Canary-объект

- Bucket: `lms-media-cas` (Timeweb Object Storage, Санкт-Петербург, region `ru-1`)
- Содержимое: `tsk-160 e2e smoke evidence - persistent canary object` (53 байта, text/plain)
- Ключ: `69/69eedea8d4874ec2c4201722488c7193b77e95af022a11541c0fa5211a7c69c1.txt`
- sha_ext: `69eedea8d4874ec2c4201722488c7193b77e95af022a11541c0fa5211a7c69c1.txt`
  (реальный sha256 содержимого — не фиктивное имя, в отличие от находки Фазы D)

## Проверка 1: публичный анонимный доступ к S3 напрямую (без LMS)

Через отдельный **unsigned** boto3-клиент (без каких-либо credentials вообще, не
переиспользует access/secret key из `.env`) — подтверждает, что bucket policy реально
public-read для существующего объекта, а не только "403 на всё подряд":

```
ANONYMOUS GET body: b'tsk-160 e2e smoke evidence - persistent canary object'
```

## Проверка 2: живой curl-прогон (полный вывод, воспроизводимо любым)

```
$ curl -s -D - "https://s3.twcstorage.ru/lms-media-cas/69/69eedea8d4874ec2c4201722488c7193b77e95af022a11541c0fa5211a7c69c1.txt"
HTTP/1.1 200 OK
Content-Type: text/plain
Content-Length: 53
ETag: "73d4a8444ce66d3767700a8b03662c37"

tsk-160 e2e smoke evidence - persistent canary object

$ curl -s -D - -o /dev/null "https://api.learn.victor-komlev.ru/api/v1/media/69eedea8d4874ec2c4201722488c7193b77e95af022a11541c0fa5211a7c69c1.txt"
HTTP/1.1 307 Temporary Redirect
location: https://s3.twcstorage.ru/lms-media-cas/69/69eedea8d4874ec2c4201722488c7193b77e95af022a11541c0fa5211a7c69c1.txt
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff

$ curl -s -L -D - "https://api.learn.victor-komlev.ru/api/v1/media/69eedea8d4874ec2c4201722488c7193b77e95af022a11541c0fa5211a7c69c1.txt"
HTTP/1.1 307 Temporary Redirect
location: https://s3.twcstorage.ru/lms-media-cas/69/69eedea8d4874ec2c4201722488c7193b77e95af022a11541c0fa5211a7c69c1.txt

HTTP/1.1 200 OK
Content-Type: text/plain
Content-Length: 53
ETag: "73d4a8444ce66d3767700a8b03662c37"

tsk-160 e2e smoke evidence - persistent canary object
```

## Вывод

- Публичный доступ к существующему объекту S3 без credentials — подтверждён (не только
  на несуществующий ключ, где ожидаемо 403/404 — на реальный объект тоже 200).
- Полная цепочка LMS → 307 → S3 → 200 с корректным содержимым — подтверждена живьём на
  production-домене `api.learn.victor-komlev.ru`.
- Canary-объект **остаётся в bucket постоянно** — любой (включая будущий review-gate) может
  независимо перевыполнить обе curl-команды выше без моего участия и без риска, что артефакт
  уже удалён.

## Замечание про Фазу D (миграция бэклога)

Отдельно, для ясности: этот canary-объект — **тестовые данные для проверки инфраструктуры**,
не реальный production-контент. Фаза D плана (миграция накопленных медиафайлов) закрыта как
"нечего мигрировать" — единственный файл в локальном `data/media_store` оказался фиктивным
тестовым артефактом (имя не совпадало с реальным sha256 содержимого), реальных накопленных
данных на момент этой задачи не было. Это не провал Фазы D по существу (мигрировать
действительно нечего), но предыдущая формулировка в трекере смешивала этот факт с проверкой
готовности инфраструктуры в целом — здесь они разделены явно.
