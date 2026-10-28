#!/usr/bin/env bash
# Прогон тестов частями.
#
# Окна Qt тяжёлые: в headless-окружении (offscreen) один длинный процесс
# на несколько десятков UI-тестов может не дойти до конца прогона, —
# поэтому список тестов делится на части, и каждая часть идёт отдельным
# процессом pytest. Количество частей задаётся переменной TEST_PARTS
# (по умолчанию 4); при небольшом наборе тестов деление безвредно.
#
# Отдельно учитывается известный крах САМОГО ВЫХОДА из процесса:
# PySide6 в offscreen-режиме иногда роняет интерпретатор уже после того,
# как все тесты части прошли («Fatal Python error: none_dealloc», код
# 134/SIGABRT). Тесты к этому не относятся, поэтому такая часть
# считается пройденной, если в её выводе есть «passed» и нет «failed».
set -euo pipefail

PARTS="${TEST_PARTS:-4}"
LOG_FILE="${TEST_LOG_FILE:-$(mktemp)}"

mapfile -t NAMES < <(python -m pytest --collect-only -q -p no:cacheprovider 2>/dev/null | grep '::' | sed 's/.*:://')

if [ "${#NAMES[@]}" -eq 0 ]; then
  echo "Тесты не найдены" >&2
  exit 1
fi

total="${#NAMES[@]}"
if [ "$PARTS" -gt "$total" ]; then
  PARTS="$total"
fi
size=$(( (total + PARTS - 1) / PARTS ))

status=0
for ((part = 0; part < PARTS; part++)); do
  start=$((part * size))
  chunk=("${NAMES[@]:start:size}")
  [ "${#chunk[@]}" -eq 0 ] && continue

  keys=""
  for name in "${chunk[@]}"; do
    if [ -z "$keys" ]; then
      keys="$name"
    else
      keys="$keys or $name"
    fi
  done

  done_count=$(( (part + 1) * size ))
  [ "$done_count" -gt "$total" ] && done_count="$total"
  echo "--- часть $((part + 1))/$PARTS ($done_count/$total)"

  set +e
  python -m pytest -q -p no:cacheprovider -k "$keys" >"$LOG_FILE" 2>&1
  code=$?
  set -e

  cat "$LOG_FILE"
  if [ "$code" -ne 0 ]; then
    # Крах на выходе из процесса после успешного прогона (см. шапку).
    if [ "$code" -eq 134 ] \
      && grep -q "passed" "$LOG_FILE" \
      && ! grep -qE "[0-9]+ failed|FAILED|ERROR" "$LOG_FILE"; then
      echo "--- часть $((part + 1)): тесты прошли, процесс упал на выходе (none_dealloc) — не считаем провалом"
    else
      status=1
    fi
  fi
done

exit "$status"
