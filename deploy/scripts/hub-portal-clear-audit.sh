#!/usr/bin/env bash
set -euo pipefail

LOCK_DIR="/run/hub-portal"
LOCK_FILE="$LOCK_DIR/audit-maintenance.lock"
SYNC_LOG_MODE_BIN="/usr/local/bin/hub-portal-sync-log-mode"
APP_DIR="/opt/hub-portal/current"
VENV_PY="/opt/hub-portal/venv/bin/python"
ENV_FILE="/etc/hub-portal/hub-portal.env"
LOG_DIR="/var/log/nginx"

MUTED_MODE_ACTIVE="0"

json_response() {
    python3 - "$@" <<'PY'
import json
import sys

payload = {}
for item in sys.argv[1:]:
    if "=" not in item:
        continue
    key, value = item.split("=", 1)
    try:
        payload[key] = json.loads(value)
    except json.JSONDecodeError:
        payload[key] = value
print(json.dumps(payload, ensure_ascii=False))
PY
}

restore_logging() {
    if [[ "$MUTED_MODE_ACTIVE" != "1" ]]; then
        return 0
    fi
    if "$SYNC_LOG_MODE_BIN" enabled >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
        systemctl reload nginx >/dev/null 2>&1 || true
    fi
    MUTED_MODE_ACTIVE="0"
}

cleanup() {
    restore_logging || true
    rm -f "$LOCK_FILE"
    rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
}

trap cleanup EXIT

mkdir -p "$LOCK_DIR"
chmod 755 "$LOCK_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    json_response ok=false message='"Очистка уже выполняется."' >&2
    exit 3
fi
printf '%s\n' "$$" >&9
chmod 644 "$LOCK_FILE"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

if ! "$SYNC_LOG_MODE_BIN" muted >/dev/null 2>&1; then
    json_response ok=false message='"Не удалось включить muted-режим project-логов."' >&2
    exit 1
fi
MUTED_MODE_ACTIVE="1"

if ! nginx -t >/dev/null 2>&1; then
    json_response ok=false message='"Конфигурация nginx не прошла проверку после включения muted-режима."' >&2
    exit 1
fi
if ! systemctl reload nginx >/dev/null 2>&1; then
    json_response ok=false message='"Не удалось выполнить reload nginx перед очисткой журнала."' >&2
    exit 1
fi

if [[ ! -x "$VENV_PY" || ! -f "$APP_DIR/manage.py" ]]; then
    json_response ok=false message='"Не найдено рабочее окружение HUB Portal для очистки журнала."' >&2
    exit 1
fi

clear_output="$("$VENV_PY" "$APP_DIR/manage.py" clear_audit_events --json 2>/dev/null)" || {
    json_response ok=false message='"Не удалось очистить таблицу AuditEvent."' >&2
    exit 1
}

read -r deleted_events cleared_files removed_archives < <(
    python3 - "$clear_output" "$LOG_DIR" "${HUB_PORTAL_PUBLIC_HOST:-}" "${HUB_PORTAL_SERVICE_DOMAIN_SUFFIX:-}" <<'PY'
import json
import os
import sys
from pathlib import Path

payload = json.loads(sys.argv[1] or "{}")
deleted_events = int(payload.get("deleted_events") or 0)
log_dir = Path(sys.argv[2])
public_host = sys.argv[3].strip().lower()
service_suffix = sys.argv[4].strip().lower()
cleared_files = 0
removed_archives = 0

def is_hub_project_log(name: str) -> bool:
    if not name.endswith(".log") and ".log." not in name:
        return False
    return (
        name.startswith("hub-portal")
        or name.startswith("hub-bot-noise")
        or (public_host and name.startswith(public_host))
        or (service_suffix and service_suffix.lstrip(".") in name)
    )

if log_dir.exists():
    for path in sorted(log_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if not is_hub_project_log(name):
            continue
        if name.endswith(".log"):
            path.write_text("", encoding="utf-8")
            cleared_files += 1
            continue
        if ".log." in name:
            path.unlink(missing_ok=True)
            removed_archives += 1

print(deleted_events, cleared_files, removed_archives)
PY
)

if ! "$SYNC_LOG_MODE_BIN" enabled >/dev/null 2>&1; then
    json_response ok=false message='"Не удалось вернуть обычный режим project-логов HUB."' >&2
    exit 1
fi
MUTED_MODE_ACTIVE="0"

if ! nginx -t >/dev/null 2>&1; then
    json_response ok=false message='"Конфигурация nginx не прошла проверку после возврата логирования."' >&2
    exit 1
fi
if ! systemctl reload nginx >/dev/null 2>&1; then
    json_response ok=false message='"Не удалось выполнить reload nginx после очистки журнала."' >&2
    exit 1
fi

json_response \
    ok=true \
    deleted_events="$deleted_events" \
    cleared_files="$cleared_files" \
    removed_archives="$removed_archives" \
    message='"Журнал и project-логи HUB очищены."'
