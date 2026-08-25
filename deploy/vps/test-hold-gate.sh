#!/usr/bin/env bash
# Проверка гейта «ждёт решения оператора» из deploy.sh (tsk-672).
#
# Гейт не даёт чужому выкату молча увезти на прод работу, помеченную строкой
# Hold-For-Operator в теле коммита. Проверять его на живом сервере нельзя, но
# логика — чистый git, поэтому тест поднимает пару временных репозиториев
# («сервер» + origin) и прогоняет по ним пять случаев.
#
# Блок гейта НЕ копируется, а вырезается из соседнего deploy.sh: копия разошлась
# бы с боевым файлом при первой же правке, и тест проверял бы не то, что поедет.
#
# Запуск (git-bash на Windows или bash на сервере):
#   bash deploy/vps/test-hold-gate.sh
set -uo pipefail

SRC=${1:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy.sh"}
[[ -f "$SRC" ]] || { echo "Не найден deploy.sh: $SRC"; exit 1; }
# Ветку берём из самого deploy.sh, чтобы тест не расходился с ним (main/master).
BRANCH=$(grep -m1 -oE 'origin/[A-Za-z0-9._-]+' "$SRC" | head -1 | cut -d/ -f2)
[[ -n "$BRANCH" ]] || { echo "Не удалось определить ветку из $SRC"; exit 1; }
echo "Проверяем: $SRC (ветка $BRANCH)"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

sed -n '/^  local range=/,/== reset to origin/p' "$SRC" | sed '$d' > "$WORK/gate-body.sh"
if [[ ! -s "$WORK/gate-body.sh" ]]; then
  echo "ПРОВАЛ: блок гейта не найден в $SRC — его переименовали или удалили."
  exit 1
fi

cat > "$WORK/gate.sh" <<EOF
set -euo pipefail
gate() {
$(cat "$WORK/gate-body.sh")
}
gate
EOF

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
ORIGIN="$WORK/origin"; PROD="$WORK/prod"
git init -q --bare "$ORIGIN"
git init -q -b "$BRANCH" "$WORK/dev"
cd "$WORK/dev"
echo base > f.txt && git add f.txt && git commit -qm "chore: базовая версия"
git remote add origin "$ORIGIN" && git push -q origin "$BRANCH"
git clone -q "$ORIGIN" "$PROD" 2>/dev/null
cd "$PROD" && git checkout -q "$BRANCH"

run_gate() { (cd "$PROD" && git fetch -q origin && bash "$WORK/gate.sh"); }

pass=0; fail=0
check() { # check <описание> <ожидаемый код> <фактический код>
  if [[ "$2" == "$3" ]]; then echo "  OK   $1 (код $3)"; pass=$((pass+1));
  else echo "  ПРОВАЛ $1: ждали код $2, получили $3"; fail=$((fail+1)); fi
}
grep_check() { # grep_check <описание> <текст вывода> <подстрока>
  if echo "$2" | grep -q "$3"; then echo "  OK   $1"; pass=$((pass+1));
  else echo "  ПРОВАЛ $1: в выводе нет «$3»"; fail=$((fail+1)); fi
}

echo "=== 1. Выкатывать нечего — гейт пропускает ==="
out=$(run_gate 2>&1); check "выкат разрешён" 0 "$?"

echo "=== 2. Обычные коммиты без пометок — гейт пропускает и печатает список ==="
cd "$WORK/dev"
echo a >> f.txt && git commit -qam "feat: обычная работа соседа"
echo b >> f.txt && git commit -qam "fix: ещё одна правка"
git push -q origin "$BRANCH"
out=$(run_gate 2>&1); check "выкат разрешён" 0 "$?"
grep_check "список едущих коммитов напечатан" "$out" "Коммитов к выкату: 2"

echo "=== 3. В ветке есть помеченный коммит — гейт останавливает ==="
cd "$WORK/dev"
echo c >> f.txt
git commit -qam "feat: разбор ответа по критериям

Hold-For-Operator: включение добавляет второй вызов модели на каждой сдаче"
echo d >> f.txt && git commit -qam "feat: работа соседнего чипа"
git push -q origin "$BRANCH"
out=$(run_gate 2>&1); check "выкат остановлен" 2 "$?"
grep_check "причина пометки показана" "$out" "второй вызов модели"

echo "=== 4. Осознанный обход DEPLOY_HOLD_OK=1 — гейт пропускает ==="
out=$( (cd "$PROD" && git fetch -q origin && DEPLOY_HOLD_OK=1 bash "$WORK/gate.sh") 2>&1)
check "выкат разрешён" 0 "$?"

echo "=== 5. Помеченное уже на проде — гейт больше не всплывает ==="
(cd "$PROD" && git reset -q --hard "origin/$BRANCH")
out=$(run_gate 2>&1); check "выкат разрешён" 0 "$?"
grep_check "пометка не повторяется" "$out" "Помеченных коммитов нет"

echo
echo "ИТОГ: успешно $pass, провалов $fail"
[[ "$fail" -eq 0 ]]
