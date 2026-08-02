#!/usr/bin/env bash
# tsk-520: живая проверка на проде — загрузка идёт в бакет, а не на диск,
# скачивание возвращает те же байты и не редиректит на хранилище.
set -euo pipefail
cd /opt/lms

KEY=$(grep -m1 '^VALID_API_KEYS=' .env | cut -d= -f2- | cut -d, -f1 | tr -d '\r')
BUCKET_URL=$(grep -m1 '^S3_MEDIA_BUCKET_URL=' .env | cut -d= -f2- | tr -d '\r')

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Маленький валидный PNG (1x1), уникальный за счёт добавленного комментария
printf '\x89PNG\r\n\x1a\n' > "$TMP/pic.png"
head -c 64 /dev/urandom >> "$TMP/pic.png"
SHA=$(sha256sum "$TMP/pic.png" | cut -d' ' -f1)
echo "sha загружаемого файла: $SHA"

echo "== 1. загрузка =="
RESP=$(curl -sS -X POST "http://127.0.0.1:8000/api/v1/materials/upload?api_key=$KEY" \
  -F "file=@$TMP/pic.png;type=image/png")
echo "ответ: $RESP"
URL=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])')
FILE_ID="${URL##*/}"
echo "file_id: $FILE_ID"

echo "== 2. имя = sha содержимого =="
[ "$FILE_ID" = "$SHA.png" ] && echo "OK: имя совпало с sha" || { echo "ПРОВАЛ: ожидалось $SHA.png"; exit 1; }

echo "== 3. на диске приложения пусто =="
COUNT=$(find /opt/lms/uploads/materials -type f | wc -l)
echo "файлов в uploads/materials: $COUNT"
[ "$COUNT" = "0" ] && echo "OK: на диск ничего не легло" || { echo "ПРОВАЛ: файл осел на диске"; exit 1; }

echo "== 4. объект в бакете =="
CODE=$(curl -sS -o "$TMP/from_s3.png" -w '%{http_code}' "$BUCKET_URL/materials/${SHA:0:2}/$SHA.png")
echo "прямой GET бакета: $CODE"
[ "$CODE" = "200" ] && echo "OK: объект лежит в хранилище" || { echo "ПРОВАЛ: объекта нет"; exit 1; }
diff -q "$TMP/pic.png" "$TMP/from_s3.png" >/dev/null && echo "OK: содержимое в бакете совпадает"

echo "== 5. скачивание через API: тело, а не редирект =="
HDRS=$(curl -sS -D - -o "$TMP/from_api.png" -w '%{http_code}' \
  "http://127.0.0.1:8000/api/v1/materials/files/$FILE_ID?api_key=$KEY")
echo "$HDRS" | head -8
diff -q "$TMP/pic.png" "$TMP/from_api.png" >/dev/null \
  && echo "OK: скачано байт в байт" || { echo "ПРОВАЛ: содержимое не совпало"; exit 1; }
echo "$HDRS" | grep -qi '^location:' && { echo "ПРОВАЛ: ответ редиректит на хранилище"; exit 1; } \
  || echo "OK: редиректа на хранилище нет"

echo "== 6. аноним не скачивает (проверка доступа tsk-516 жива) =="
ANON=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://127.0.0.1:8000/api/v1/materials/files/$FILE_ID")
echo "аноним: $ANON"
[ "$ANON" = "401" ] && echo "OK: 401" || { echo "ПРОВАЛ: ожидался 401"; exit 1; }

echo ""
echo "ИТОГ: живая проверка пройдена. file_id=$FILE_ID"
