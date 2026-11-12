#!/usr/bin/env bash
# Прогон тестов частями.
#
# Окна Qt тяжёлые: в headless-окружении (offscreen) один длинный процесс
# на несколько десятков UI-тестов может не дойти до конца прогона, —
# поэтому список тестов делится на части, и каждая часть идёт отдельным
# процессом pytest. Количество частей задаётся переменной TEST_PARTS
# (по умолчанию 4); при небольшом наборе тестов деление безвредно.
#
# Отдельно учитывается известный крах PySide6 в offscreen-режиме:
# интерпретатор падает с «Fatal Python error: none_dealloc» (код
# 134/SIGABRT). Если это случилось **после** того, как все тесты части
# выполнились (в выводе есть «passed» и нет «failed»), часть считается
# пройденной — тесты тут ни при чём. Если крах случился **в середине**
# части, часть доигрывается: каждый её тест запускается отдельным
# процессом. Так гарантируется, что все тесты действительно выполнены,
# а настоящий провал теста не теряется среди падений интерпретатора
# (иначе крах середины части выглядел бы как провал всего прогона).
set -euo pipefail

PARTS="${TEST_PARTS:-4}"
LOG_FILE="${TEST_LOG_FILE:-$(mktemp)}"

mapfile -t IDS < <(python -m pytest --collect-only -q -p no:cacheprovider 2>/dev/null | grep '::')
NAMES=("${IDS[@]##*::}")

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
  # Провал части — тоже в аннотации: причина видна без скачивания лога.
  if [ "$code" -ne 0 ] && grep -qE "[0-9]+ failed|FAILED|ERROR" "$LOG_FILE"; then
    grep -E "FAILED|Error|assert" "$LOG_FILE" | head -n 15 \
      | awk '{printf "%s%%0A", $0}' | sed 's/^/::error ::/' | head -1
  fi
  if [ "$code" -eq 0 ]; then
    continue
  fi
  # Крах на выходе из процесса после успешного прогона (см. шапку).
  if [ "$code" -eq 134 ] \
    && grep -q "passed" "$LOG_FILE" \
    && ! grep -qE "[0-9]+ failed|FAILED|ERROR" "$LOG_FILE"; then
    echo "--- часть $((part + 1)): тесты прошли, процесс упал на выходе (none_dealloc) — не считаем провалом"
    continue
  fi
  # Крах случился **в середине** части: часть из тестов не выполнилась.
  # До игрываем их по одному — так каждый тест будет выполнен, а
  # настоящий провал теста не потеряется среди падений интерпретатора.
  echo "--- часть $((part + 1)): процесс упал внутри прогона, доигрываем тесты по одному"
  part_status=0
  index=$start
  for _name in "${chunk[@]}"; do
    test_id="${IDS[$index]}"
    index=$((index + 1))
    set +e
    python -m pytest "$test_id" -q -p no:cacheprovider >"$LOG_FILE" 2>&1
    test_code=$?
    set -e
    if [ "$test_code" -ne 0 ] && ! grep -q "passed" "$LOG_FILE"; then
      echo "--- ПРОВАЛ: $test_id"
      cat "$LOG_FILE"
      # Хвост провала — в аннотации запуска: логи Actions скачивать
      # не всегда удобно, а причина видна прямо на странице проверки.
      tail -n 25 "$LOG_FILE" | awk '{printf "%s%%0A", $0}' \
        | sed 's/^/::error ::/' | head -1
      part_status=1
    fi
  done
  if [ "$part_status" -ne 0 ]; then
    status=1
  fi
done

exit "$status"
