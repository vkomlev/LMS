#!/usr/bin/env bash
# Ставит хуки репозитория из .githooks/ (tsk-669).
#
# Запуск один раз на рабочую копию (в том числе в свежем клоне):
#   bash scripts/install_git_hooks.sh
#
# Что делает:
#   1. core.hooksPath = .githooks — git берёт хуки из репозитория, а не из .git/hooks;
#   2. старый локальный .git/hooks/pre-commit переименовывает в *.disabled-tsk669,
#      чтобы он не сбивал с толку (сам git его после смены hooksPath уже не вызывает).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git config core.hooksPath .githooks
echo "core.hooksPath = $(git config core.hooksPath)"

LEGACY=".git/hooks/pre-commit"
if [ -f "$LEGACY" ]; then
    mv "$LEGACY" "$LEGACY.disabled-tsk669"
    echo "Старый $LEGACY отключён (переименован в pre-commit.disabled-tsk669)."
fi

chmod +x .githooks/* 2>/dev/null || true
echo "Готово. Хуки берутся из .githooks/."
