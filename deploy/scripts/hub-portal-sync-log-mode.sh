#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
case "$MODE" in
    enabled|muted) ;;
    *)
        echo "Usage: hub-portal-sync-log-mode.sh enabled|muted" >&2
        exit 1
        ;;
esac

HUB_LOG_MODE_DIR="/etc/nginx/hub-log-mode"
SERVICE_SNIPPETS_DIR="$HUB_LOG_MODE_DIR/services"
SITES_AVAILABLE_DIR="/etc/nginx/sites-available"
ENV_FILE="/etc/hub-portal/hub-portal.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi
ROOT_DOMAIN="${HUB_PORTAL_PUBLIC_HOST:?HUB_PORTAL_PUBLIC_HOST is not configured}"
SERVICE_SUFFIX="${HUB_PORTAL_SERVICE_DOMAIN_SUFFIX:-.$ROOT_DOMAIN}"

mkdir -p "$SERVICE_SNIPPETS_DIR"
chmod 755 "$HUB_LOG_MODE_DIR" "$SERVICE_SNIPPETS_DIR"

if [[ "$MODE" == "enabled" ]]; then
    cat >"$HUB_LOG_MODE_DIR/hub-portal.conf" <<'EOF'
access_log off;
error_log /var/log/nginx/hub-portal.error.log;
EOF
    cat >"$HUB_LOG_MODE_DIR/hub-catchall.conf" <<'EOF'
access_log /var/log/nginx/hub-bot-noise.log;
error_log /var/log/nginx/hub-bot-noise.error.log notice;
EOF
else
    cat >"$HUB_LOG_MODE_DIR/hub-portal.conf" <<'EOF'
access_log off;
error_log /dev/null crit;
EOF
    cat >"$HUB_LOG_MODE_DIR/hub-catchall.conf" <<'EOF'
access_log off;
error_log /dev/null crit;
EOF
fi

python3 - "$MODE" "$SITES_AVAILABLE_DIR" "$SERVICE_SNIPPETS_DIR" "$ROOT_DOMAIN" "$SERVICE_SUFFIX" <<'PY'
import re
import sys
from pathlib import Path

mode = sys.argv[1]
sites_available_dir = Path(sys.argv[2])
service_snippets_dir = Path(sys.argv[3])
root_domain = sys.argv[4]
service_suffix = sys.argv[5]

service_snippets_dir.mkdir(parents=True, exist_ok=True)

enabled_template = "access_log off;\nerror_log /var/log/nginx/{domain}.error.log;\n"
muted_template = "access_log off;\nerror_log /dev/null crit;\n"
snippet_template = enabled_template if mode == "enabled" else muted_template

for config_path in sorted(sites_available_dir.glob(f"*{service_suffix}")):
    domain = config_path.name
    if domain == root_domain or domain == "default":
        continue
    if not config_path.is_file():
        continue

    include_line = f"    include /etc/nginx/hub-log-mode/services/{domain}.conf;"
    content = config_path.read_text(encoding="utf-8")

    if include_line not in content:
        legacy_block = re.compile(
            rf"(?m)^(\s*)error_log /var/log/nginx/{re.escape(domain)}\.error\.log;[ \t]*\n\1access_log off;[ \t]*$"
        )
        patched_content, replacements = legacy_block.subn(rf"\1include /etc/nginx/hub-log-mode/services/{domain}.conf;", content, count=1)
        if replacements == 0:
            raise SystemExit(
                f"Unable to patch legacy log directives for {domain}. Add include manually before switching log mode."
            )
        config_path.write_text(patched_content, encoding="utf-8")

    snippet_path = service_snippets_dir / f"{domain}.conf"
    snippet_path.write_text(snippet_template.format(domain=domain), encoding="utf-8")
PY
