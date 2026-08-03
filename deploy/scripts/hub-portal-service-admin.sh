#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  hub-portal-service-admin.sh issue-cert --domain DOMAIN
  hub-portal-service-admin.sh validate --domain DOMAIN --source FILE
  hub-portal-service-admin.sh apply --domain DOMAIN --source FILE [--enable|--disable]
  hub-portal-service-admin.sh disable --domain DOMAIN
  hub-portal-service-admin.sh remove --domain DOMAIN
EOF
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Required command is missing: $1" >&2
        exit 127
    }
}

ACTION="${1:-}"
[[ -n "$ACTION" ]] || usage
shift || true

DOMAIN=""
SOURCE_FILE=""
ENABLE_MODE="enable"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN="$2"; shift 2 ;;
        --source) SOURCE_FILE="$2"; shift 2 ;;
        --enable) ENABLE_MODE="enable"; shift 1 ;;
        --disable) ENABLE_MODE="disable"; shift 1 ;;
        *) usage ;;
    esac
done

[[ -n "$DOMAIN" ]] || usage

require_cmd certbot
require_cmd nginx
require_cmd systemctl
require_cmd install
require_cmd readlink
require_cmd mktemp
require_cmd rm

SITE_AVAILABLE="/etc/nginx/sites-available/$DOMAIN"
SITE_ENABLED="/etc/nginx/sites-enabled/$DOMAIN"
LOG_SNIPPET="/etc/nginx/hub-log-mode/services/$DOMAIN.conf"
ACME_ROOT="/var/www/hub-portal/acme"
CERT_PATH="/etc/letsencrypt/live/$DOMAIN"
CERT_ARCHIVE_PATH="/etc/letsencrypt/archive/$DOMAIN"
CERT_RENEWAL_PATH="/etc/letsencrypt/renewal/$DOMAIN.conf"
ENV_FILE="/etc/hub-portal/hub-portal.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi
PUBLIC_HOST="${HUB_PORTAL_PUBLIC_HOST:?HUB_PORTAL_PUBLIC_HOST is not configured}"
SERVICE_SUFFIX="${HUB_PORTAL_SERVICE_DOMAIN_SUFFIX:-.$PUBLIC_HOST}"
[[ "$DOMAIN" == *"$SERVICE_SUFFIX" && "$DOMAIN" != "$PUBLIC_HOST" ]] || {
    echo "Domain must match *$SERVICE_SUFFIX" >&2
    exit 2
}

TMP_DIR="$(mktemp -d)"
HAD_AVAILABLE=0
HAD_ENABLED=0
ENABLED_TARGET=""
HAD_LOG_SNIPPET=0
COMMIT_DONE=0
TEMP_CREATED_LINK=0

cleanup() {
    local exit_code=$?
    if [[ "$exit_code" -ne 0 && "$COMMIT_DONE" -eq 0 ]]; then
        if [[ "$HAD_AVAILABLE" -eq 1 ]]; then
            install -m 644 "$TMP_DIR/original.available" "$SITE_AVAILABLE"
        else
            rm -f "$SITE_AVAILABLE"
        fi
        if [[ "$HAD_ENABLED" -eq 1 ]]; then
            ln -sfn "$ENABLED_TARGET" "$SITE_ENABLED"
        else
            rm -f "$SITE_ENABLED"
        fi
        if [[ "$HAD_LOG_SNIPPET" -eq 1 ]]; then
            install -m 644 "$TMP_DIR/original.log" "$LOG_SNIPPET"
        else
            rm -f "$LOG_SNIPPET"
        fi
        nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [[ -f "$SITE_AVAILABLE" ]]; then
    HAD_AVAILABLE=1
    cp "$SITE_AVAILABLE" "$TMP_DIR/original.available"
fi

if [[ -L "$SITE_ENABLED" || -e "$SITE_ENABLED" ]]; then
    HAD_ENABLED=1
    ENABLED_TARGET="$(readlink -f "$SITE_ENABLED" || true)"
fi

if [[ -f "$LOG_SNIPPET" ]]; then
    HAD_LOG_SNIPPET=1
    cp "$LOG_SNIPPET" "$TMP_DIR/original.log"
fi

mkdir -p "$ACME_ROOT" "/etc/nginx/hub-log-mode/services"

write_service_log_snippet() {
    cat >"$LOG_SNIPPET" <<EOF
access_log off;
error_log /var/log/nginx/$DOMAIN.error.log;
EOF
}

cleanup_service_certificate() {
    if [[ -d "$CERT_PATH" || -d "$CERT_ARCHIVE_PATH" || -f "$CERT_RENEWAL_PATH" ]]; then
        certbot delete --non-interactive --cert-name "$DOMAIN" >/dev/null 2>&1 || true
        rm -rf "$CERT_PATH" "$CERT_ARCHIVE_PATH"
        rm -f "$CERT_RENEWAL_PATH"
    fi
}

build_acme_http_config() {
    cat <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root $ACME_ROOT;
    }

    location / {
        return 302 ${HUB_PORTAL_PUBLIC_SCHEME:-https}://$PUBLIC_HOST/;
    }
}
EOF
}

ensure_certbot_email_args() {
    CERTBOT_ARGS=(
        certonly
        --webroot
        -w "$ACME_ROOT"
        -d "$DOMAIN"
        --non-interactive
        --agree-tos
    )
    if [[ -n "${HUB_PORTAL_CERTBOT_EMAIL:-}" ]]; then
        CERTBOT_ARGS+=(--email "$HUB_PORTAL_CERTBOT_EMAIL")
    else
        CERTBOT_ARGS+=(--register-unsafely-without-email)
    fi
}

case "$ACTION" in
    validate)
        [[ -f "$SOURCE_FILE" ]] || {
            echo "Validation source file is missing." >&2
            exit 1
        }
        write_service_log_snippet
        install -m 644 "$SOURCE_FILE" "$SITE_AVAILABLE"
        if [[ "$HAD_ENABLED" -eq 0 ]]; then
            ln -sfn "$SITE_AVAILABLE" "$SITE_ENABLED"
            TEMP_CREATED_LINK=1
        fi
        nginx -t >/dev/null
        if [[ "$TEMP_CREATED_LINK" -eq 1 ]]; then
            rm -f "$SITE_ENABLED"
        fi
        if [[ "$HAD_AVAILABLE" -eq 1 ]]; then
            install -m 644 "$TMP_DIR/original.available" "$SITE_AVAILABLE"
        else
            rm -f "$SITE_AVAILABLE"
        fi
        if [[ "$HAD_LOG_SNIPPET" -eq 1 ]]; then
            install -m 644 "$TMP_DIR/original.log" "$LOG_SNIPPET"
        else
            rm -f "$LOG_SNIPPET"
        fi
        COMMIT_DONE=1
        echo "Nginx config for $DOMAIN passed validation."
        ;;

    apply)
        [[ -f "$SOURCE_FILE" ]] || {
            echo "Apply source file is missing." >&2
            exit 1
        }
        write_service_log_snippet
        install -m 644 "$SOURCE_FILE" "$SITE_AVAILABLE"
        if [[ "$ENABLE_MODE" == "enable" ]]; then
            ln -sfn "$SITE_AVAILABLE" "$SITE_ENABLED"
        else
            rm -f "$SITE_ENABLED"
        fi
        nginx -t >/dev/null
        systemctl reload nginx
        COMMIT_DONE=1
        echo "Applied config for $DOMAIN ($ENABLE_MODE)."
        ;;

    disable)
        rm -f "$SITE_ENABLED"
        nginx -t >/dev/null
        systemctl reload nginx
        COMMIT_DONE=1
        echo "Service $DOMAIN disabled."
        ;;

    remove)
        rm -f "$SITE_ENABLED"
        rm -f "$SITE_AVAILABLE"
        rm -f "$LOG_SNIPPET"
        nginx -t >/dev/null
        systemctl reload nginx
        cleanup_service_certificate
        COMMIT_DONE=1
        echo "Service $DOMAIN removed from nginx and Let's Encrypt storage."
        ;;

    issue-cert)
        ensure_certbot_email_args
        build_acme_http_config >"$TMP_DIR/acme.conf"
        install -m 644 "$TMP_DIR/acme.conf" "$SITE_AVAILABLE"
        ln -sfn "$SITE_AVAILABLE" "$SITE_ENABLED"
        nginx -t >/dev/null
        systemctl reload nginx
        if [[ ! -d "$CERT_PATH" ]]; then
            certbot "${CERTBOT_ARGS[@]}"
        fi
        if [[ "$HAD_AVAILABLE" -eq 1 ]]; then
            install -m 644 "$TMP_DIR/original.available" "$SITE_AVAILABLE"
        else
            rm -f "$SITE_AVAILABLE"
        fi
        if [[ "$HAD_ENABLED" -eq 1 ]]; then
            ln -sfn "$ENABLED_TARGET" "$SITE_ENABLED"
        else
            rm -f "$SITE_ENABLED"
        fi
        if [[ "$HAD_LOG_SNIPPET" -eq 1 ]]; then
            install -m 644 "$TMP_DIR/original.log" "$LOG_SNIPPET"
        else
            rm -f "$LOG_SNIPPET"
        fi
        nginx -t >/dev/null
        systemctl reload nginx
        COMMIT_DONE=1
        echo "Certificate is ready for $DOMAIN."
        ;;

    *)
        usage
        ;;
esac
