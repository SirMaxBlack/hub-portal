#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
FUNCTIONS="$(awk '/^random_hex\(\)/ { exit } { print }' "$ROOT/deploy/scripts/install.sh")"
# shellcheck disable=SC1090
eval "$FUNCTIONS"

NON_INTERACTIVE=0
SELECTED=""
output_file="$(mktemp)"
trap 'rm -f "$output_file"' EXIT
select_option SELECTED "Тестовое меню" "domain" \
    "domain|HTTPS|Домен и сертификат." \
    "local|HTTP|Локальная установка." <<< $'9\n2\n' >"$output_file"
[[ "$SELECTED" == "local" ]]
grep -q 'Введите номер от 1 до 2' "$output_file"
grep -q 'Домен и сертификат' "$output_file"

export NON_INTERACTIVE=1
SELECTED="domain"
select_option SELECTED "Тестовое меню" "local" \
    "domain|HTTPS|Домен и сертификат." \
    "local|HTTP|Локальная установка." >/dev/null
[[ "$SELECTED" == "domain" ]]

if (SELECTED="unknown"; select_option SELECTED "Тестовое меню" "local" \
    "domain|HTTPS|Домен и сертификат." \
    "local|HTTP|Локальная установка." >/dev/null 2>&1); then
    echo "Недопустимое неинтерактивное значение ошибочно принято." >&2
    exit 1
fi

echo "install UI helpers: OK"
