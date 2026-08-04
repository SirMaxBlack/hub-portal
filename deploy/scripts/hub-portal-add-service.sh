#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  hub-portal-add-service.sh \
    --domain DOMAIN \
    --upstream URL \
    --title TITLE \
    --icon ICON \
    [--description TEXT] \
    [--sort-order N] \
    [--proxy-ssl-verify off] \
    [--proxy-ssl-protocols "TLSv1.3"] \
    [--omit-x-forwarded-for] \
    [--precheck-url URL] \
    [--health-type http|https|tcp] \
    [--health-host HOST] \
    [--health-port PORT] \
    [--health-path PATH] \
    [--health-timeout SECONDS] \
    [--health-codes CSV] \
    [--health-interval SECONDS] \
    [--health-failure-threshold N] \
    [--health-success-threshold N]
EOF
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Required command is missing: $1" >&2
        exit 127
    }
}

fail_preflight() {
    local message="$1"
    echo "Preflight failed: $message" >&2
    echo "No certbot request, nginx reload or DB update was executed." >&2
    echo "Prepared service:" >&2
    echo "  domain   = $DOMAIN" >&2
    echo "  upstream = $UPSTREAM" >&2
    echo "  title    = $TITLE" >&2
    exit 2
}

build_http_config() {
    cat <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root $ACME_ROOT;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}
EOF
}

UPSTREAM_BASE=""
UPSTREAM_PATH_PREFIX=""
UPSTREAM_ROOT_PROXY=""

parse_upstream_url() {
    if [[ "$UPSTREAM" =~ ^(https?://[^/]+)(/[^?#]*)?$ ]]; then
        UPSTREAM_BASE="${BASH_REMATCH[1]}"
        local raw_path="${BASH_REMATCH[2]:-}"
        if [[ -n "$raw_path" && "$raw_path" != "/" ]]; then
            UPSTREAM_PATH_PREFIX="${raw_path%/}"
            UPSTREAM_ROOT_PROXY="${UPSTREAM_BASE}${UPSTREAM_PATH_PREFIX}/"
            return
        fi
    fi

    UPSTREAM_BASE="$UPSTREAM"
    UPSTREAM_PATH_PREFIX=""
    UPSTREAM_ROOT_PROXY="$UPSTREAM"
}

write_service_log_snippet() {
    local snippet_dir="/etc/nginx/hub-log-mode/services"
    mkdir -p "$snippet_dir"
    cat >"$snippet_dir/$DOMAIN.conf" <<EOF
access_log off;
error_log /var/log/nginx/$DOMAIN.error.log;
EOF
}

build_service_proxy_location() {
    local ssl_extra="$1"
    local forwarded_for_directive="$2"

    if [[ -n "$UPSTREAM_PATH_PREFIX" ]]; then
        cat <<EOF
    location / {
        auth_request /_portal_auth;
        error_page 401 = /_portal_login;
        error_page 403 = /_portal_forbidden;

        proxy_pass $UPSTREAM_ROOT_PROXY;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Cookie \$hub_upstream_cookie;
        proxy_set_header X-Real-IP \$remote_addr;
${forwarded_for_directive}
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
${ssl_extra}        proxy_buffering off;
        proxy_connect_timeout 10s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }

    location = $UPSTREAM_PATH_PREFIX {
        return 302 $UPSTREAM_PATH_PREFIX/;
    }

    location ^~ $UPSTREAM_PATH_PREFIX/ {
        auth_request /_portal_auth;
        error_page 401 = /_portal_login;
        error_page 403 = /_portal_forbidden;

        proxy_pass $UPSTREAM_BASE;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Cookie \$hub_upstream_cookie;
        proxy_set_header X-Real-IP \$remote_addr;
${forwarded_for_directive}
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
${ssl_extra}        proxy_buffering off;
        proxy_connect_timeout 10s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
EOF
        return
    fi

    cat <<EOF
    location / {
        auth_request /_portal_auth;
        error_page 401 = /_portal_login;
        error_page 403 = /_portal_forbidden;

        proxy_pass $UPSTREAM;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Cookie \$hub_upstream_cookie;
        proxy_set_header X-Real-IP \$remote_addr;
${forwarded_for_directive}
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
${ssl_extra}        proxy_buffering off;
        proxy_connect_timeout 10s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
EOF
}

build_https_config() {
    local ssl_extra=""
    if [[ "$UPSTREAM" == https://* ]]; then
        ssl_extra=$'        proxy_ssl_server_name on;\n'
        if [[ -n "$PROXY_SSL_PROTOCOLS" ]]; then
            ssl_extra+="$(printf '        proxy_ssl_protocols %s;\n' "$PROXY_SSL_PROTOCOLS")"
        fi
        if [[ "$PROXY_SSL_VERIFY" == "off" ]]; then
            ssl_extra+=$'        proxy_ssl_verify off;\n'
        fi
    fi

    local forwarded_for_directive="        proxy_set_header X-Forwarded-For \$remote_addr;"
    if [[ "$OMIT_X_FORWARDED_FOR" == "1" ]]; then
        forwarded_for_directive='        proxy_set_header X-Forwarded-For "";'
    fi

    local service_proxy_location
    service_proxy_location="$(build_service_proxy_location "$ssl_extra" "$forwarded_for_directive")"

    cat <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root $ACME_ROOT;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate $CERT_PATH/fullchain.pem;
    ssl_certificate_key $CERT_PATH/privkey.pem;
    include /etc/nginx/snippets/hub-tls.conf;

    include /etc/nginx/hub-log-mode/services/$DOMAIN.conf;

    client_max_body_size 100m;
    include /etc/nginx/snippets/hub-security-headers.conf;

    location = /_portal_auth {
        internal;
        proxy_pass http://hub_portal_app/internal/authz;
        proxy_pass_request_body off;
        proxy_pass_request_headers off;
        proxy_set_header Content-Length "";
        proxy_set_header Host $HUB_PORTAL_PUBLIC_HOST;
        proxy_set_header X-Portal-Auth-Key $HUB_PORTAL_INTERNAL_AUTH_KEY;
        proxy_set_header X-Original-Service $DOMAIN;
        proxy_set_header X-Original-URI \$request_uri;
        proxy_set_header X-Original-Method \$request_method;
        proxy_set_header Cookie \$hub_portal_gate_cookie;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }

${service_proxy_location}

    location = /_portal_forbidden {
        internal;
        proxy_pass http://hub_portal_app/forbidden/;
        proxy_pass_request_body off;
        proxy_pass_request_headers off;
        proxy_set_header Content-Length "";
        proxy_set_header Host $HUB_PORTAL_PUBLIC_HOST;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }

    location = /_portal_login {
        proxy_pass http://hub_portal_app/internal/login-state;
        proxy_pass_request_body off;
        proxy_pass_request_headers off;
        proxy_set_header Content-Length "";
        proxy_set_header Host $HUB_PORTAL_PUBLIC_HOST;
        proxy_set_header X-Portal-Auth-Key $HUB_PORTAL_INTERNAL_AUTH_KEY;
        proxy_set_header X-Original-Service $DOMAIN;
        proxy_set_header X-Original-URI \$request_uri;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
EOF
}

DOMAIN=""
UPSTREAM=""
TITLE=""
ICON="default"
DESCRIPTION=""
SORT_ORDER="100"
PROXY_SSL_VERIFY="on"
PROXY_SSL_PROTOCOLS=""
OMIT_X_FORWARDED_FOR="0"
PRECHECK_URL=""
HEALTH_TYPE=""
HEALTH_HOST=""
HEALTH_PORT=""
HEALTH_PATH=""
HEALTH_TIMEOUT="3"
HEALTH_CODES=""
HEALTH_INTERVAL="5"
HEALTH_FAILURE_THRESHOLD="2"
HEALTH_SUCCESS_THRESHOLD="1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN="$2"; shift 2 ;;
        --upstream) UPSTREAM="$2"; shift 2 ;;
        --title) TITLE="$2"; shift 2 ;;
        --icon) ICON="$2"; shift 2 ;;
        --description) DESCRIPTION="$2"; shift 2 ;;
        --sort-order) SORT_ORDER="$2"; shift 2 ;;
        --proxy-ssl-verify) PROXY_SSL_VERIFY="$2"; shift 2 ;;
        --proxy-ssl-protocols) PROXY_SSL_PROTOCOLS="$2"; shift 2 ;;
        --omit-x-forwarded-for) OMIT_X_FORWARDED_FOR="1"; shift 1 ;;
        --precheck-url) PRECHECK_URL="$2"; shift 2 ;;
        --health-type) HEALTH_TYPE="$2"; shift 2 ;;
        --health-host) HEALTH_HOST="$2"; shift 2 ;;
        --health-port) HEALTH_PORT="$2"; shift 2 ;;
        --health-path) HEALTH_PATH="$2"; shift 2 ;;
        --health-timeout) HEALTH_TIMEOUT="$2"; shift 2 ;;
        --health-codes) HEALTH_CODES="$2"; shift 2 ;;
        --health-interval) HEALTH_INTERVAL="$2"; shift 2 ;;
        --health-failure-threshold) HEALTH_FAILURE_THRESHOLD="$2"; shift 2 ;;
        --health-success-threshold) HEALTH_SUCCESS_THRESHOLD="$2"; shift 2 ;;
        *) usage ;;
    esac
done

[[ -n "$DOMAIN" && -n "$UPSTREAM" && -n "$TITLE" ]] || usage
[[ "$UPSTREAM" =~ ^https?:// ]] || fail_preflight "upstream must start with http:// or https://"
[[ "$PROXY_SSL_VERIFY" == "on" || "$PROXY_SSL_VERIFY" == "off" ]] || fail_preflight "--proxy-ssl-verify must be on or off"

require_cmd certbot
require_cmd curl
require_cmd getent
require_cmd nginx
require_cmd ss
require_cmd systemctl

APP_DIR="/opt/hub-portal/current"
VENV_PY="/opt/hub-portal/venv/bin/python"
ENV_FILE="/etc/hub-portal/hub-portal.env"
SITE_AVAILABLE="/etc/nginx/sites-available/$DOMAIN"
SITE_ENABLED="/etc/nginx/sites-enabled/$DOMAIN"
CERT_PATH="/etc/letsencrypt/live/$DOMAIN"
ACME_ROOT="/var/www/hub-portal/acme"

mkdir -p "$ACME_ROOT"
write_service_log_snippet

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

[[ -n "${HUB_PORTAL_INTERNAL_AUTH_KEY:-}" ]] || fail_preflight "HUB_PORTAL_INTERNAL_AUTH_KEY is not configured"
[[ -n "${HUB_PORTAL_PUBLIC_HOST:-}" ]] || fail_preflight "HUB_PORTAL_PUBLIC_HOST is not configured"
SERVICE_SUFFIX="${HUB_PORTAL_SERVICE_DOMAIN_SUFFIX:-.$HUB_PORTAL_PUBLIC_HOST}"
[[ "$DOMAIN" == *"$SERVICE_SUFFIX" && "$DOMAIN" != "$HUB_PORTAL_PUBLIC_HOST" ]] || fail_preflight "domain must match *$SERVICE_SUFFIX"

PRECHECK_URL="${PRECHECK_URL:-$UPSTREAM}"
parse_upstream_url
TMP_HTTP_CONFIG="$(mktemp)"
TMP_HTTPS_CONFIG="$(mktemp)"
trap 'rm -f "$TMP_HTTP_CONFIG" "$TMP_HTTPS_CONFIG"' EXIT

build_http_config >"$TMP_HTTP_CONFIG"
build_https_config >"$TMP_HTTPS_CONFIG"

echo "== Preflight =="
getent ahostsv4 "$DOMAIN" >/dev/null || fail_preflight "DNS for $DOMAIN does not resolve yet"
ss -ltn | grep -Eq 'LISTEN.+:80([[:space:]]|$)' || fail_preflight "nothing is listening on tcp/80"
ss -ltn | grep -Eq 'LISTEN.+:443([[:space:]]|$)' || fail_preflight "nothing is listening on tcp/443"

curl_args=(--silent --show-error --location --output /dev/null --max-time 10)
if [[ "$PRECHECK_URL" == https://* && "$PROXY_SSL_VERIFY" == "off" ]]; then
    curl_args+=(--insecure)
fi
curl "${curl_args[@]}" "$PRECHECK_URL" || fail_preflight "upstream did not answer: $PRECHECK_URL"
nginx -t >/dev/null || fail_preflight "current nginx configuration is invalid"

install -m 644 "$TMP_HTTP_CONFIG" "$SITE_AVAILABLE"
ln -sfn "$SITE_AVAILABLE" "$SITE_ENABLED"
nginx -t
systemctl reload nginx

if [[ ! -d "$CERT_PATH" ]]; then
    certbot_args=(
        certonly
        --webroot
        -w "$ACME_ROOT"
        -d "$DOMAIN"
        --non-interactive
        --agree-tos
    )
    if [[ -n "${HUB_PORTAL_CERTBOT_EMAIL:-}" ]]; then
        certbot_args+=(--email "$HUB_PORTAL_CERTBOT_EMAIL")
    else
        certbot_args+=(--register-unsafely-without-email)
    fi
    certbot "${certbot_args[@]}"
fi

install -m 644 "$TMP_HTTPS_CONFIG" "$SITE_AVAILABLE"
nginx -t
systemctl reload nginx

upsert_args=(
    "$VENV_PY" "$APP_DIR/manage.py" upsert_service
    --domain "$DOMAIN"
    --upstream "$UPSTREAM"
    --title "$TITLE"
    --icon "$ICON"
    --description "$DESCRIPTION"
    --sort-order "$SORT_ORDER"
)

[[ -n "$HEALTH_TYPE" ]] && upsert_args+=(--health-type "$HEALTH_TYPE")
[[ -n "$HEALTH_HOST" ]] && upsert_args+=(--health-host "$HEALTH_HOST")
[[ -n "$HEALTH_PORT" ]] && upsert_args+=(--health-port "$HEALTH_PORT")
[[ -n "$HEALTH_PATH" ]] && upsert_args+=(--health-path "$HEALTH_PATH")
[[ -n "$HEALTH_TIMEOUT" ]] && upsert_args+=(--health-timeout "$HEALTH_TIMEOUT")
[[ -n "$HEALTH_CODES" ]] && upsert_args+=(--health-codes "$HEALTH_CODES")
[[ -n "$HEALTH_INTERVAL" ]] && upsert_args+=(--health-interval "$HEALTH_INTERVAL")
[[ -n "$HEALTH_FAILURE_THRESHOLD" ]] && upsert_args+=(--health-failure-threshold "$HEALTH_FAILURE_THRESHOLD")
[[ -n "$HEALTH_SUCCESS_THRESHOLD" ]] && upsert_args+=(--health-success-threshold "$HEALTH_SUCCESS_THRESHOLD")

"${upsert_args[@]}"

echo "Service $DOMAIN is configured and registered."
