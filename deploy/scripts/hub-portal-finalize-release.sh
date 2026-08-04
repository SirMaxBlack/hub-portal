#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/opt/hub-portal"
APP_DIR="$APP_ROOT/current"
VENV_DIR="$APP_ROOT/venv"
ENV_FILE="/etc/hub-portal/hub-portal.env"
MANIFEST_PATH="$APP_DIR/staticfiles/staticfiles.json"

generate_release_id() {
    local timestamp git_sha
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    if git -C "$APP_DIR" rev-parse --short HEAD >/dev/null 2>&1; then
        git_sha="$(git -C "$APP_DIR" rev-parse --short HEAD)"
        printf '%s-%s\n' "$timestamp" "$git_sha"
        return
    fi
    printf '%s\n' "$timestamp"
}

validate_manifest() {
    python3 - "$MANIFEST_PATH" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
if not manifest_path.exists():
    raise SystemExit(f"Manifest file not found: {manifest_path}")

payload = json.loads(manifest_path.read_text(encoding="utf-8"))
paths = payload.get("paths", payload)
required = {"portal/styles.css", "portal/app.js"}

missing = [key for key in required if not paths.get(key)]
if missing:
    raise SystemExit(f"Manifest entries missing: {', '.join(missing)}")

non_hashed = [key for key in required if paths.get(key) == key]
if non_hashed:
    raise SystemExit(f"Manifest entries are not fingerprinted: {', '.join(non_hashed)}")
PY
}

main() {
    local release_id release_file release_tmp
    release_id="${1:-$(generate_release_id)}"
    release_file="$APP_DIR/.release-id"
    release_tmp="$APP_DIR/.release-id.tmp"

    cd "$APP_DIR"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a

    "$VENV_DIR/bin/python" manage.py collectstatic --noinput
    validate_manifest

    printf '%s\n' "$release_id" > "$release_tmp"
    mv "$release_tmp" "$release_file"
    chown hubportal:www-data "$release_file"
    chmod 640 "$release_file"

    systemctl restart hub-portal
    systemctl is-active hub-portal >/dev/null
}

main "$@"
