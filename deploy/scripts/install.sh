#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/hub-portal"
ENV_DIR="/etc/hub-portal"
ENV_FILE="$ENV_DIR/hub-portal.env"
NGINX_SITE="/etc/nginx/sites-available/hub-portal"
NGINX_LINK="/etc/nginx/sites-enabled/hub-portal"
SOURCE_ROOT=""
NON_INTERACTIVE="${HUB_INSTALL_NONINTERACTIVE:-0}"
INSTALL_GITHUB_TOKEN="${GITHUB_TOKEN:-}"
STORE_GITHUB_TOKEN="${HUB_PORTAL_STORE_GITHUB_TOKEN:-1}"
unset GITHUB_TOKEN

die() { echo "Ошибка: $*" >&2; exit 1; }
info() { printf '\n[HUB] %s\n' "$*"; }
explain() { printf '\n'; printf '%s\n' "$@"; }
prompt() {
    local variable="$1" text="$2" default="${3:-}" value
    if [[ "$NON_INTERACTIVE" == "1" ]]; then
        value="${!variable:-$default}"
    else
        read -r -p "$text${default:+ [$default]}: " value
        value="${value:-$default}"
    fi
    printf -v "$variable" '%s' "$value"
}
prompt_secret() {
    local variable="$1" text="$2" first second
    if [[ "$NON_INTERACTIVE" == "1" ]]; then
        first="${!variable:-}"
        [[ ${#first} -ge 8 ]] || die "Пароль должен содержать минимум 8 символов."
    else
        while true; do
            read -r -s -p "$text: " first || die "Ввод прерван."
            echo
            read -r -s -p "Повторите пароль: " second || die "Ввод прерван."
            echo
            if [[ "$first" != "$second" ]]; then
                echo "Пароли не совпадают. Повторите ввод."
                continue
            fi
            if [[ ${#first} -lt 8 ]]; then
                echo "Пароль должен содержать минимум 8 символов. Повторите ввод."
                continue
            fi
            break
        done
    fi
    printf -v "$variable" '%s' "$first"
}
select_option() {
    local variable="$1" title="$2" default_value="$3" selected entry value label description answer
    local -a options=("${@:4}")
    local index=0 default_index=0 selected_valid=0

    for entry in "${options[@]}"; do
        index=$((index + 1))
        IFS='|' read -r value label description <<<"$entry"
        [[ "$value" == "$default_value" ]] && default_index="$index"
    done
    [[ "$default_index" -gt 0 ]] || die "Для меню '$title' не задан корректный вариант по умолчанию."

    if [[ "$NON_INTERACTIVE" == "1" ]]; then
        selected="${!variable:-$default_value}"
    else
        explain "$title"
        index=0
        for entry in "${options[@]}"; do
            index=$((index + 1))
            IFS='|' read -r value label description <<<"$entry"
            printf '  %s) %s\n' "$index" "$label"
            [[ -n "$description" ]] && printf '     %s\n' "$description"
        done
        while true; do
            read -r -p "Введите номер [$default_index]: " answer || die "Ввод прерван."
            answer="${answer:-$default_index}"
            if [[ "$answer" =~ ^[0-9]+$ && "$answer" -ge 1 && "$answer" -le ${#options[@]} ]]; then
                IFS='|' read -r selected _ <<<"${options[$((answer - 1))]}"
                break
            fi
            echo "Введите номер от 1 до ${#options[@]}."
        done
    fi

    for entry in "${options[@]}"; do
        IFS='|' read -r value _ <<<"$entry"
        [[ "$value" == "$selected" ]] && selected_valid=1
    done
    [[ "$selected_valid" == "1" ]] || die "Недопустимое значение '$selected' для меню '$title'."
    printf -v "$variable" '%s' "$selected"
}
random_hex() { python3 -c "import secrets; print(secrets.token_hex($1))"; }
random_fernet() { python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'; }
render_template() {
    local source="$1" target="$2"
    sed -e "s|__PUBLIC_HOST__|$PUBLIC_HOST|g" -e "s|__PUBLIC_PORT__|$PUBLIC_PORT|g" "$source" > "$target"
}
validate_cidrs() {
    python3 - "$@" <<'PY'
import ipaddress
import sys

for value in sys.argv[1:]:
    ipaddress.ip_network(value, strict=False)
PY
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source) SOURCE_ROOT="$(cd "$2" && pwd)"; shift 2 ;;
        *) die "Неизвестный аргумент: $1" ;;
    esac
done
[[ $EUID -eq 0 ]] || die "Запустите установщик через sudo."
[[ -n "$SOURCE_ROOT" && -f "$SOURCE_ROOT/manage.py" ]] || die "Исходный код HUB не найден."
if [[ -n "$INSTALL_GITHUB_TOKEN" && ! "$INSTALL_GITHUB_TOKEN" =~ ^[A-Za-z0-9_]+$ ]]; then
    die "GitHub token имеет недопустимый формат."
fi
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
    debian:*) dpkg --compare-versions "$VERSION_ID" ge 12 || die "Нужен Debian 12+." ;;
    ubuntu:*) dpkg --compare-versions "$VERSION_ID" ge 24.04 || die "Нужен Ubuntu 24.04+." ;;
    *) die "Поддерживаются Debian 12+ и Ubuntu 24.04+." ;;
esac

info "Параметры установки"
INSTALL_MODE="${HUB_INSTALL_MODE:-}"
select_option INSTALL_MODE "Как портал должен открываться?" "domain" \
    "domain|Домен с HTTPS (рекомендуется)|Нужна DNS-запись на этот сервер. Установщик получит сертификат Let's Encrypt и откроет портал по адресу https://portal.example.ru." \
    "local|Локальный HTTP|Для лаборатории без сертификата. Пример: http://192.168.1.20:8080. Не рекомендуется для доступа через интернет."
if [[ "$INSTALL_MODE" == "domain" ]]; then
    explain \
        "Укажите полное DNS-имя портала без https:// и без пути." \
        "DNS-запись уже должна указывать на этот сервер." \
        "Пример: hub.example.ru"
    while true; do
        prompt PUBLIC_HOST "Домен портала" "${HUB_INSTALL_PUBLIC_HOST:-}"
        [[ "$PUBLIC_HOST" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] && break
        [[ "$NON_INTERACTIVE" == "1" ]] && die "Некорректный домен."
        echo "Некорректный домен. Введите значение вида hub.example.ru."
    done
    PUBLIC_HOST="${PUBLIC_HOST,,}"
    PUBLIC_SCHEME="https"
    PUBLIC_PORT="443"
    PUBLIC_ORIGIN="$PUBLIC_SCHEME://$PUBLIC_HOST"
    SESSION_COOKIE_DOMAIN=".$PUBLIC_HOST"
else
    explain \
        "Укажите IP-адрес или локальное DNS-имя сервера." \
        "Примеры: 192.168.1.20 или hub.lan"
    prompt PUBLIC_HOST "IP или локальное DNS-имя" "${HUB_INSTALL_PUBLIC_HOST:-127.0.0.1}"
    explain \
        "Укажите свободный TCP-порт для локального HTTP." \
        "Пример: 8080. Допустимый диапазон: 1-65535."
    while true; do
        prompt PUBLIC_PORT "HTTP-порт" "${HUB_INSTALL_PUBLIC_PORT:-8080}"
        [[ "$PUBLIC_PORT" =~ ^[0-9]+$ && "$PUBLIC_PORT" -ge 1 && "$PUBLIC_PORT" -le 65535 ]] && break
        [[ "$NON_INTERACTIVE" == "1" ]] && die "Некорректный порт."
        echo "Некорректный порт. Введите число от 1 до 65535."
    done
    PUBLIC_SCHEME="http"
    PUBLIC_ORIGIN="$PUBLIC_SCHEME://$PUBLIC_HOST"
    if [[ "$PUBLIC_PORT" != "80" ]]; then PUBLIC_ORIGIN="$PUBLIC_ORIGIN:$PUBLIC_PORT"; fi
    SESSION_COOKIE_DOMAIN=""
fi

ACCESS_MODE="${HUB_INSTALL_ACCESS_MODE:-}"
select_option ACCESS_MODE "Кто сможет открыть страницу портала?" "restricted" \
    "restricted|Только разрешённые IP-адреса и подсети (рекомендуется)|До окна входа дойдут только перечисленные далее адреса. Остальные увидят страницу ограничения доступа." \
    "unrestricted|Любой IP-адрес|Окно входа будет доступно всему интернету. Пользователей всё равно защищают логин, пароль и лимит ошибок авторизации."
ACCESS_RULES=()
if [[ "$ACCESS_MODE" == "restricted" ]]; then
    if [[ "$NON_INTERACTIVE" == "1" ]]; then
        IFS=',' read -r -a ACCESS_RULES <<<"${HUB_INSTALL_CIDRS:-}"
    else
        explain \
            "Добавьте адреса, с которых разрешено открывать HUB." \
            "Один адрес: 203.0.113.25/32. Подсеть: 192.168.1.0/24." \
            "Обязательно добавьте свой текущий внешний или локальный адрес, иначе закроете себе доступ."
        while true; do
            if [[ ${#ACCESS_RULES[@]} -eq 0 ]]; then
                cidr_default_action="add"
            else
                echo
                echo "Сейчас добавлено адресов: ${#ACCESS_RULES[@]}"
                printf '  - %s\n' "${ACCESS_RULES[@]}"
                cidr_default_action="finish"
            fi
            unset CIDR_ACTION
            select_option CIDR_ACTION "Что сделать со списком разрешённых адресов?" "$cidr_default_action" \
                "add|Добавить IP-адрес или подсеть|После выбора потребуется ввести значение в формате IP или CIDR." \
                "finish|Завершить список|Продолжить установку с уже добавленными адресами."
            [[ "$CIDR_ACTION" == "finish" ]] && break
            explain "Введите адрес или подсеть. Примеры: 203.0.113.25/32, 192.168.1.0/24, 2001:db8::/64"
            cidr=""
            prompt cidr "IP/CIDR"
            if ! validate_cidrs "$cidr" 2>/dev/null; then
                echo "Некорректный IP/CIDR. Адрес не добавлен."
                continue
            fi
            if printf '%s\n' "${ACCESS_RULES[@]}" | grep -Fxq -- "$cidr"; then
                echo "Такой адрес уже есть в списке."
                continue
            fi
            ACCESS_RULES+=("$cidr")
        done
    fi
    [[ ${#ACCESS_RULES[@]} -gt 0 ]] || die "Restricted-режим требует минимум один IP/CIDR."
    validate_cidrs "${ACCESS_RULES[@]}" || die "Обнаружен некорректный IP/CIDR."
fi

explain \
    "Укажите логин первого администратора HUB." \
    "Разрешены буквы, цифры и символы _ . @ + -. Пример: admin"
while true; do
    prompt ADMIN_USERNAME "Логин администратора" "${HUB_INSTALL_ADMIN_USERNAME:-admin}"
    [[ "$ADMIN_USERNAME" =~ ^[A-Za-z0-9_.@+-]+$ ]] && break
    [[ "$NON_INTERACTIVE" == "1" ]] && die "Логин содержит недопустимые символы."
    echo "Логин содержит недопустимые символы. Пример корректного логина: admin."
done
explain \
    "Укажите отображаемое имя администратора." \
    "Оно будет видно в верхней панели портала. Пример: Иван Иванов"
prompt ADMIN_FULL_NAME "Имя администратора" "${HUB_INSTALL_ADMIN_FULL_NAME:-Администратор}"
explain \
    "Создайте пароль первого администратора. Ввод на экране не отображается." \
    "Минимум 8 символов; рекомендуется не менее 12 символов с буквами, цифрами и разным регистром."
prompt_secret ADMIN_PASSWORD "Пароль администратора"
if [[ "$NON_INTERACTIVE" == "1" ]]; then
    TIME_ZONE="${HUB_INSTALL_TIME_ZONE:-UTC}"
else
    select_option TIME_ZONE_CHOICE "Какой часовой пояс использовать для журнала и дат портала?" "UTC" \
        "UTC|UTC|Универсальное серверное время без смещения." \
        "Europe/Moscow|Москва — UTC+3|Подходит для московского часового пояса." \
        "Asia/Yekaterinburg|Екатеринбург — UTC+5|Подходит для Екатеринбурга и соответствующего часового пояса." \
        "custom|Другой часовой пояс|Позволяет ввести системное имя вручную, например Asia/Novosibirsk."
    if [[ "$TIME_ZONE_CHOICE" == "custom" ]]; then
        explain \
            "Введите системное имя часового пояса из /usr/share/zoneinfo." \
            "Примеры: Asia/Novosibirsk, Europe/Kaliningrad, Asia/Vladivostok"
        while true; do
            prompt TIME_ZONE "Часовой пояс"
            [[ -f "/usr/share/zoneinfo/$TIME_ZONE" ]] && break
            echo "Часовой пояс не найден. Проверьте системное имя и повторите ввод."
        done
    else
        TIME_ZONE="$TIME_ZONE_CHOICE"
    fi
fi
[[ -f "/usr/share/zoneinfo/$TIME_ZONE" ]] || die "Неизвестный часовой пояс."
if [[ "$INSTALL_MODE" == "domain" ]]; then
    explain \
        "Email используется Let's Encrypt для важных уведомлений о сертификате." \
        "Пример: admin@example.ru. Поле можно оставить пустым."
    while true; do
        prompt CERTBOT_EMAIL "Email для Let's Encrypt (можно пустым)" "${HUB_INSTALL_CERTBOT_EMAIL:-}"
        [[ -z "$CERTBOT_EMAIL" || "$CERTBOT_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] && break
        [[ "$NON_INTERACTIVE" == "1" ]] && die "Некорректный email."
        echo "Некорректный email. Введите адрес вида admin@example.ru или оставьте поле пустым."
    done
fi

info "Проверка занятых ресурсов"
if [[ "$INSTALL_MODE" == "local" ]] && ss -ltnH "sport = :$PUBLIC_PORT" 2>/dev/null | grep -q .; then
    die "Порт $PUBLIC_PORT уже занят."
fi
if [[ "$INSTALL_MODE" == "domain" ]] && command -v nginx >/dev/null 2>&1 \
    && nginx -T 2>&1 | grep -Eiq "server_name[[:space:]]+[^;]*\b${PUBLIC_HOST//./\.}\b"; then
    die "Домен $PUBLIC_HOST уже объявлен в существующей конфигурации nginx."
fi
MARIADB_WAS_INSTALLED=0
command -v mariadbd >/dev/null 2>&1 && MARIADB_WAS_INSTALLED=1
if [[ "$MARIADB_WAS_INSTALLED" == "1" ]] \
    && ss -ltnH 'sport = :3306' 2>/dev/null | awk '{print $4}' | grep -Eq '(^|\[)(0\.0\.0\.0|::)(\]|):3306$'; then
    die "Существующая MariaDB доступна на внешнем интерфейсе. Установка остановлена, чтобы не менять чужую конфигурацию."
fi
if [[ -e "$NGINX_SITE" || -L "$NGINX_LINK" || -e "$ENV_FILE" ]]; then
    die "HUB уже установлен. Используйте hubctl update или удалите старую установку осознанно."
fi

info "Проверьте выбранные параметры"
printf 'Адрес портала: %s\n' "$PUBLIC_ORIGIN"
printf 'Режим публикации: %s\n' "$( [[ "$INSTALL_MODE" == "domain" ]] && echo 'домен с HTTPS' || echo 'локальный HTTP' )"
if [[ "$ACCESS_MODE" == "restricted" ]]; then
    echo "Доступ: только разрешённые IP/CIDR"
    printf '  - %s\n' "${ACCESS_RULES[@]}"
else
    echo "Доступ: с любого IP-адреса"
fi
printf 'Администратор: %s (%s)\n' "$ADMIN_USERNAME" "$ADMIN_FULL_NAME"
printf 'Часовой пояс: %s\n' "$TIME_ZONE"
[[ "$INSTALL_MODE" == "domain" ]] && printf "Email Let's Encrypt: %s\n" "${CERTBOT_EMAIL:-не указан}"
explain \
    "После подтверждения установщик поставит MariaDB, nginx, Python, Certbot и зависимости HUB." \
    "Будут созданы отдельная база данных, systemd-службы, nginx-конфигурация и команда hubctl." \
    "Существующие nginx-сайты установщик не удаляет."
CONFIRM_INSTALL="${HUB_INSTALL_CONFIRM:-}"
select_option CONFIRM_INSTALL "Начать установку с указанными параметрами?" "install" \
    "install|Начать установку|Перейти к установке пакетов и настройке HUB." \
    "cancel|Отменить|Завершить работу без установки HUB."
if [[ "$CONFIRM_INSTALL" == "cancel" ]]; then
    info "Установка отменена. Изменения HUB не применялись."
    exit 0
fi

info "Установка системных зависимостей"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    certbot curl libnginx-mod-http-js mariadb-client mariadb-server nginx python3 python3-pip python3-venv sudo tar

systemctl enable --now mariadb nginx
install -D -m 644 /dev/stdin /etc/mysql/mariadb.conf.d/99-hub-portal-bind.cnf <<'EOF'
[mysqld]
bind-address = 127.0.0.1
EOF
systemctl restart mariadb
if ss -ltnH 'sport = :3306' 2>/dev/null | awk '{print $4}' | grep -Eq '(^|\[)(0\.0\.0\.0|::)(\]|):3306$'; then
    die "MariaDB всё ещё слушает внешний интерфейс."
fi
id -u hubportal >/dev/null 2>&1 || useradd --system --home "$APP_ROOT" --shell /usr/sbin/nologin hubportal

VERSION="$(tr -d '[:space:]' < "$SOURCE_ROOT/VERSION")"
RELEASE_ID="${VERSION}-$(date -u +%Y%m%dT%H%M%SZ)"
RELEASE_DIR="$APP_ROOT/releases/$RELEASE_ID"
VENV_DIR="$APP_ROOT/venvs/$RELEASE_ID"
install -d -o hubportal -g www-data -m 750 "$APP_ROOT" "$APP_ROOT/releases" "$APP_ROOT/venvs" "$APP_ROOT/runtime"
install -d -o hubportal -g www-data -m 750 "$APP_ROOT/runtime/login-protection/browser-locks" "$APP_ROOT/runtime/service-admin/staging"
install -d -o www-data -g www-data -m 755 /var/www/hub-portal/acme
mkdir -p "$RELEASE_DIR"
tar -C "$SOURCE_ROOT" --exclude='.git' --exclude='.venv' --exclude='db.sqlite3' --exclude='staticfiles' -cf - . | tar -C "$RELEASE_DIR" -xf -
chown -R hubportal:www-data "$RELEASE_DIR"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip wheel
"$VENV_DIR/bin/pip" install -r "$RELEASE_DIR/requirements.txt"
ln -sfn "$RELEASE_DIR" "$APP_ROOT/current"
ln -sfn "$VENV_DIR" "$APP_ROOT/venv"

info "Создание MariaDB и секретов"
DB_NAME="hub_portal"
DB_USER="hub_portal"
DB_PASSWORD="$(random_hex 24)"
SECRET_KEY="$(random_hex 32)"
TOKEN_KEY="$(random_fernet)"
INTERNAL_KEY="$(random_hex 32)"
mkdir -p "$ENV_DIR"
if [[ -n "$INSTALL_GITHUB_TOKEN" && "$STORE_GITHUB_TOKEN" == "1" ]]; then
    token_file="$(mktemp)"
    trap 'rm -f "$token_file"' EXIT
    printf '%s\n' "$INSTALL_GITHUB_TOKEN" > "$token_file"
    install -o root -g root -m 600 "$token_file" "$ENV_DIR/github.token"
    rm -f "$token_file"
    trap - EXIT
fi
cat > "$ENV_FILE" <<EOF
HUB_PORTAL_SECRET_KEY=$SECRET_KEY
HUB_PORTAL_TOKEN_ENCRYPTION_KEY=$TOKEN_KEY
HUB_PORTAL_INTERNAL_AUTH_KEY=$INTERNAL_KEY
HUB_PORTAL_PUBLIC_HOST=$PUBLIC_HOST
HUB_PORTAL_PUBLIC_SCHEME=$PUBLIC_SCHEME
HUB_PORTAL_PUBLIC_URL=$PUBLIC_ORIGIN
HUB_PORTAL_SERVICE_DOMAIN_SUFFIX=.$PUBLIC_HOST
HUB_PORTAL_ALLOWED_HOSTS=$PUBLIC_HOST,.$PUBLIC_HOST,127.0.0.1,localhost
HUB_PORTAL_CSRF_TRUSTED_ORIGINS=$PUBLIC_ORIGIN
HUB_PORTAL_SESSION_COOKIE_DOMAIN=$SESSION_COOKIE_DOMAIN
HUB_PORTAL_DB_NAME=$DB_NAME
HUB_PORTAL_DB_USER=$DB_USER
HUB_PORTAL_DB_PASSWORD=$DB_PASSWORD
HUB_PORTAL_DB_HOST=127.0.0.1
HUB_PORTAL_DB_PORT=3306
HUB_PORTAL_DEBUG=0
HUB_PORTAL_SESSION_COOKIE_SECURE=$( [[ "$PUBLIC_SCHEME" == "https" ]] && echo 1 || echo 0 )
HUB_PORTAL_CSRF_COOKIE_SECURE=$( [[ "$PUBLIC_SCHEME" == "https" ]] && echo 1 || echo 0 )
HUB_PORTAL_SECURE_SSL_REDIRECT=$( [[ "$PUBLIC_SCHEME" == "https" ]] && echo 1 || echo 0 )
HUB_PORTAL_SECURE_HSTS_SECONDS=$( [[ "$PUBLIC_SCHEME" == "https" ]] && echo 31536000 || echo 0 )
HUB_PORTAL_SECURE_HSTS_INCLUDE_SUBDOMAINS=$( [[ "$PUBLIC_SCHEME" == "https" ]] && echo 1 || echo 0 )
HUB_PORTAL_SECURE_HSTS_PRELOAD=0
HUB_PORTAL_TIME_ZONE=$TIME_ZONE
HUB_PORTAL_AUDIT_RETENTION_DAYS=180
HUB_PORTAL_GUNICORN_WORKERS=2
HUB_PORTAL_CERTBOT_EMAIL=${CERTBOT_EMAIL:-}
HUB_PORTAL_SERVICE_ADMIN_HELPER=/usr/local/bin/hub-portal-service-admin
HUB_PORTAL_SERVICE_ADMIN_RUNTIME_DIR=$APP_ROOT/runtime/service-admin
HUB_PORTAL_LOGIN_PROTECTION_RUNTIME_DIR=$APP_ROOT/runtime/login-protection
HUB_PORTAL_ACCESS_RULE_MARKER_FILE=$APP_ROOT/runtime/portal-access-rules.stamp
HUB_PORTAL_SERVICE_SITE_AVAILABLE_DIR=/etc/nginx/sites-available
HUB_PORTAL_SERVICE_SITE_ENABLED_DIR=/etc/nginx/sites-enabled
EOF
chown root:www-data "$ENV_FILE"; chmod 640 "$ENV_FILE"

mariadb <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASSWORD';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

info "Инициализация приложения"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
cd "$RELEASE_DIR"
"$VENV_DIR/bin/python" manage.py migrate --noinput
printf '%s\n' "$ADMIN_PASSWORD" | "$VENV_DIR/bin/python" manage.py bootstrap_admin \
    --username "$ADMIN_USERNAME" --full-name "$ADMIN_FULL_NAME" --password-stdin
configure_args=("$VENV_DIR/bin/python" manage.py configure_installation --mode "$ACCESS_MODE" --admin-username "$ADMIN_USERNAME")
for cidr in "${ACCESS_RULES[@]}"; do configure_args+=(--cidr "$cidr"); done
"${configure_args[@]}"
"$VENV_DIR/bin/python" manage.py seed_default_links
"$VENV_DIR/bin/python" manage.py collectstatic --noinput
[[ -f "$RELEASE_DIR/staticfiles/staticfiles.json" ]] || die "Manifest статики не создан."
printf '%s\n' "$RELEASE_ID" > "$RELEASE_DIR/.release-id"
chown hubportal:www-data "$RELEASE_DIR/.release-id"

info "Установка systemd и helper-ов"
for unit in "$RELEASE_DIR"/deploy/systemd/*; do install -m 644 "$unit" "/etc/systemd/system/$(basename "$unit")"; done
install -m 755 "$RELEASE_DIR/deploy/scripts/hub-portal-sync-log-mode.sh" /usr/local/bin/hub-portal-sync-log-mode
install -m 755 "$RELEASE_DIR/deploy/scripts/hub-portal-clear-audit.sh" /usr/local/bin/hub-portal-clear-audit
install -m 755 "$RELEASE_DIR/deploy/scripts/hub-portal-service-admin.sh" /usr/local/bin/hub-portal-service-admin
install -m 755 "$RELEASE_DIR/deploy/scripts/hub-portal-add-service.sh" /usr/local/bin/hub-portal-add-service
install -m 755 "$RELEASE_DIR/deploy/scripts/hubctl" /usr/local/bin/hubctl
install -D -m 440 "$RELEASE_DIR/deploy/sudoers/hub-portal-clear-audit" /etc/sudoers.d/hub-portal-clear-audit
install -D -m 440 "$RELEASE_DIR/deploy/sudoers/hub-portal-service-admin" /etc/sudoers.d/hub-portal-service-admin
visudo -cf /etc/sudoers.d/hub-portal-clear-audit >/dev/null
visudo -cf /etc/sudoers.d/hub-portal-service-admin >/dev/null
install -m 644 "$RELEASE_DIR/deploy/nginx/hub-portal-upstream.conf" /etc/nginx/conf.d/hub-portal-upstream.conf
install -m 644 "$RELEASE_DIR/deploy/nginx/hub-portal-request-vars.conf" /etc/nginx/conf.d/hub-portal-request-vars.conf
install -D -m 644 "$RELEASE_DIR/deploy/nginx/hub-portal-cookies.js" /etc/nginx/hub-njs/hub-portal-cookies.js
install -D -m 644 "$RELEASE_DIR/deploy/nginx/hub-security-headers.conf" /etc/nginx/snippets/hub-security-headers.conf
install -D -m 644 "$RELEASE_DIR/deploy/nginx/hub-tls.conf" /etc/nginx/snippets/hub-tls.conf
install -D -m 644 "$RELEASE_DIR/deploy/logrotate/hub-bot-noise" /etc/logrotate.d/hub-bot-noise
mkdir -p /etc/nginx/hub-log-mode/services
/usr/local/bin/hub-portal-sync-log-mode enabled

systemctl daemon-reload
systemctl enable --now hub-portal hub-portal-health-monitor hub-portal-clearsessions.timer hub-portal-prune-audit.timer

info "Настройка nginx"
if [[ "$INSTALL_MODE" == "domain" ]]; then
    render_template "$RELEASE_DIR/deploy/nginx/portal-acme.conf.template" "$NGINX_SITE"
    ln -sfn "$NGINX_SITE" "$NGINX_LINK"
    nginx -t && systemctl reload nginx
    cert_args=(certonly --webroot -w /var/www/hub-portal/acme -d "$PUBLIC_HOST" --non-interactive --agree-tos)
    if [[ -n "${CERTBOT_EMAIL:-}" ]]; then cert_args+=(--email "$CERTBOT_EMAIL"); else cert_args+=(--register-unsafely-without-email); fi
    certbot "${cert_args[@]}"
    render_template "$RELEASE_DIR/deploy/nginx/portal-https.conf.template" "$NGINX_SITE"
else
    render_template "$RELEASE_DIR/deploy/nginx/portal-http.conf.template" "$NGINX_SITE"
    ln -sfn "$NGINX_SITE" "$NGINX_LINK"
fi
nginx -t && systemctl reload nginx

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    if [[ "$INSTALL_MODE" == "domain" ]]; then
        ufw_port_description="TCP-порты 80 и 443"
    else
        ufw_port_description="TCP-порт $PUBLIC_PORT"
    fi
    explain \
        "На сервере обнаружен активный UFW." \
        "Установщик может добавить разрешающие правила для HUB: $ufw_port_description." \
        "Существующие правила UFW не удаляются. Если правила уже настроены вручную, выберите второй пункт."
    OPEN_UFW="${HUB_INSTALL_OPEN_UFW:-}"
    select_option OPEN_UFW "Нужно добавить правила HUB в UFW?" "1" \
        "1|Добавить правила (рекомендуется)|Разрешить подключения к порту портала. В restricted-режиме правила будут созданы только для указанных IP/CIDR." \
        "0|Не изменять UFW|Оставить текущие правила без изменений. Портал может быть недоступен, если нужный порт закрыт."
    if [[ "$OPEN_UFW" == "1" ]]; then
        if [[ "$ACCESS_MODE" == "restricted" ]]; then
            port_value="$( [[ "$INSTALL_MODE" == "domain" ]] && echo 443 || echo "$PUBLIC_PORT" )"
            for cidr in "${ACCESS_RULES[@]}"; do ufw allow from "$cidr" to any port "$port_value" proto tcp; done
        elif [[ "$INSTALL_MODE" == "domain" ]]; then
            ufw allow 80/tcp; ufw allow 443/tcp
        else
            ufw allow "$PUBLIC_PORT"/tcp
        fi
    fi
else
    info "UFW не установлен или не активен — правила файрвола не изменяются."
fi

hubctl doctor
info "Установка завершена: $PUBLIC_ORIGIN"
