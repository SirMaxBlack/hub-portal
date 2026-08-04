# Установка HUB Portal

## 1. Что делает установщик

`install.sh` разворачивает готовый self-hosted портал на Debian 12+ или Ubuntu
24.04+. Установщик:

1. Проверяет ОС, root-права, занятый порт и базовые параметры.
2. Устанавливает системные пакеты.
3. Создаёт системного пользователя `hubportal`.
4. Создаёт отдельные каталоги release и Python venv.
5. Устанавливает Python-зависимости из `requirements.txt`.
6. Создаёт MariaDB-базу, пользователя БД и случайные секреты.
7. Выполняет Django migrations, `collectstatic` и создаёт администратора.
8. Устанавливает systemd units, timers, nginx snippets, helpers и `hubctl`.
9. В domain-режиме получает сертификат Let's Encrypt.
10. Проверяет nginx и работоспособность портала через `hubctl doctor`.

Установщик не удаляет чужие vhost, базы данных или systemd-службы. При конфликте
выбранного порта он останавливается до изменения nginx.

## 2. Требования

- Debian 12+ или Ubuntu 24.04+;
- root либо пользователь с `sudo`;
- минимум 1 vCPU, 1 ГБ RAM и 5 ГБ свободного места;
- доступ к репозиториям ОС и GitHub Releases;
- для HTTPS — DNS-запись на этот сервер и доступные `80/tcp`, `443/tcp`;
- корректные дата, время и DNS resolver на сервере.

Рекомендуется отдельная VM. На общем сервере сначала проверьте конфликты:

```bash
sudo ss -lntup
sudo systemctl --type=service --state=running
sudo nginx -T 2>/dev/null | less
df -h
```

Если nginx ещё не установлен, ошибка команды `nginx -T` на чистом сервере
нормальна.

## 3. Подготовка DNS

Для основного режима создайте A/AAAA-запись портала заранее:

```text
hub.example.com -> публичный IP сервера
```

Проверьте с внешнего компьютера:

```bash
dig +short hub.example.com
curl -I http://hub.example.com
```

Для reverse proxy каждый сервис получает отдельную DNS-запись. В стандартной
установке она должна быть под доменом портала:

```text
crm.hub.example.com -> тот же IP сервера
```

Нестандартная доменная схема описана в [модели защиты](SECURITY_MODEL.md#домены-сервисов-и-cookie-zone).

## 4. Запуск установки

### Публичный GitHub Release

```bash
bash <(curl -Ls https://raw.githubusercontent.com/SirMaxBlack/hub-portal/main/install.sh)
```

Bootstrap скачивает последний недрафтовый GitHub Release и проверяет его
SHA-256. Основная установка выполняется уже из проверенного архива.

### Из клонированного репозитория

```bash
git clone https://github.com/SirMaxBlack/hub-portal.git
cd hub-portal
sudo ./install.sh
```

### Приватный fork

```bash
export GITHUB_TOKEN='TOKEN_WITH_REPOSITORY_READ_ACCESS'
sudo -E ./install.sh
unset GITHUB_TOKEN
```

По умолчанию проверенный token сохраняется в `/etc/hub-portal/github.token` с
правами `0600 root:root`. Отключить сохранение:

```bash
export HUB_PORTAL_STORE_GITHUB_TOKEN=0
```

Token нельзя вписывать в командную строку, README, unit-файл или `.env`
приложения.

## 5. Пошаговые вопросы установщика

Все варианты выбираются цифрами. Неверный номер не прерывает установку: меню
показывается повторно.

### 5.1. Режим публикации

```text
1) Домен и HTTPS
2) Локальный HTTP
```

`Домен и HTTPS` — рабочий режим. Нужен DNS, порты 80/443 и выпуск Let's Encrypt.
Cookie получают флаги Secure, включаются HTTPS redirect и HSTS.

`Локальный HTTP` — лабораторный режим на IP/hostname и выбранном порту, обычно
8080. Не использовать для передачи реальных паролей через недоверенную сеть.

### 5.2. Доступ к порталу

```text
1) Только указанные IP/CIDR
2) Без ограничений по IP
```

Restricted-режим скрывает даже форму входа от остальных адресов. Добавьте свой
текущий адрес до завершения списка:

```text
203.0.113.25/32       один IPv4
192.168.10.0/24       локальная IPv4-подсеть
2001:db8:10::/64      IPv6-подсеть
```

Пустой глобальный список в web-разделе `Безопасность` означает доступ с любых
адресов.

### 5.3. Администратор

Установщик спросит логин, отображаемое имя и пароль. Логин используется для
входа, отображаемое имя — только в интерфейсе. Пароль вводится скрыто и не
передаётся аргументом процесса.

Используйте уникальный пароль длиной не менее 12–16 символов. Минимум приложения
— 8 символов.

### 5.4. Часовой пояс

Можно выбрать UTC, `Europe/Moscow`, `Asia/Yekaterinburg` или ввести IANA timezone:

```text
Asia/Novosibirsk
Europe/Kaliningrad
```

Параметр влияет на отображение времени в журнале, сессиях и интерфейсе. MariaDB
и системные timestamps могут оставаться в UTC.

### 5.5. UFW

Если UFW установлен и активен, скрипт предлагает добавить правила. В restricted
режиме разрешения создаются только для введённых CIDR. Установщик не включает
UFW автоматически и не удаляет существующие правила.

Перед выбором `Не изменять UFW` убедитесь, что нужный порт уже открыт.

### 5.6. Финальное подтверждение

До выбора `Начать установку` выводится сводка. Проверьте режим, URL, CIDR,
администратора и timezone. После подтверждения начинается установка пакетов и
изменение системы.

## 6. Устанавливаемые пакеты

```text
certbot
curl
libnginx-mod-http-js
mariadb-client
mariadb-server
nginx
python3
python3-pip
python3-venv
sudo
tar
```

Python-библиотеки устанавливаются в отдельный venv релиза, а не в системный
Python.

## 7. Что создаётся в системе

### Каталоги приложения

| Путь | Владелец/режим | Назначение |
|---|---|---|
| `/opt/hub-portal/releases/` | `hubportal:www-data`, `0750` | Код всех установленных release. |
| `/opt/hub-portal/venvs/` | `hubportal:www-data`, `0750` | Python venv каждого release. |
| `/opt/hub-portal/current` | symlink | Текущий код. |
| `/opt/hub-portal/venv` | symlink | Текущий Python venv. |
| `/opt/hub-portal/runtime/` | `hubportal:www-data`, `0750` | Runtime locks и staging операций. |
| `/var/www/hub-portal/acme/` | `www-data:www-data`, `0755` | Webroot ACME challenge. |

### Конфигурация

| Путь | Назначение |
|---|---|
| `/etc/hub-portal/hub-portal.env` | Секреты, БД, домен и параметры Django. |
| `/etc/hub-portal/github.token` | Необязательный token приватного fork, `0600 root:root`. |
| `/etc/nginx/sites-available/hub-portal` | Основной vhost портала. |
| `/etc/nginx/sites-enabled/hub-portal` | Symlink включённого vhost. |
| `/etc/nginx/conf.d/hub-portal-*.conf` | Upstream и служебные переменные. |
| `/etc/nginx/snippets/hub-*.conf` | TLS и security headers. |
| `/etc/nginx/hub-njs/` | njs-обработка cookie HUB. |
| `/etc/nginx/hub-log-mode/` | Управляемые режимы логирования HUB. |

### Команды и службы

```text
/usr/local/bin/hubctl
/usr/local/bin/hub-portal-add-service
/usr/local/bin/hub-portal-service-admin
/usr/local/bin/hub-portal-clear-audit
/usr/local/bin/hub-portal-sync-log-mode
```

| Unit | Назначение |
|---|---|
| `hub-portal.service` | Gunicorn/Django. |
| `hub-portal-health-monitor.service` | Постоянный health-check сервисов. |
| `hub-portal-clearsessions.timer` | Ежедневная очистка просроченных sessions, 03:10. |
| `hub-portal-prune-audit.timer` | Ежедневное удаление старого аудита, 03:25. |

## 8. MariaDB

Установщик создаёт:

```text
База:       hub_portal
Пользователь: hub_portal@127.0.0.1
Кодировка:  utf8mb4
Collation:  utf8mb4_unicode_ci
```

MariaDB привязывается к `127.0.0.1`. Публиковать `3306/tcp` наружу нельзя.

### Вход в БД

На сервере root обычно входит через Unix socket:

```bash
sudo mariadb hub_portal
```

Полезные безопасные команды:

```sql
SHOW TABLES;
SELECT COUNT(*) FROM portal_user;
SELECT COUNT(*) FROM portal_service;
SELECT COUNT(*) FROM portal_auditevent;
EXIT;
```

Не изменяйте строки вручную. Django-связи, шифрованные токены, ревизии и аудит
должны меняться через web-интерфейс или management commands.

## 9. Секреты в env

Проверить параметры без вывода секретов:

```bash
sudo stat /etc/hub-portal/hub-portal.env
sudo grep -E '^(HUB_PORTAL_PUBLIC_|HUB_PORTAL_TIME_ZONE|HUB_PORTAL_AUDIT_RETENTION_DAYS)' \
  /etc/hub-portal/hub-portal.env
```

Критичные значения:

- `HUB_PORTAL_SECRET_KEY` — подпись Django и sessions;
- `HUB_PORTAL_TOKEN_ENCRYPTION_KEY` — расшифровка отображаемых login tokens;
- `HUB_PORTAL_INTERNAL_AUTH_KEY` — доверие между nginx и `/internal/authz`;
- `HUB_PORTAL_DB_PASSWORD` — пароль локального пользователя MariaDB.

Их нельзя менять без плана миграции. Смена ключа токенов делает старые токены
нераскрываемыми; смена Django secret завершает/ломает старые подписанные данные;
рассинхронизация internal auth key блокирует reverse proxy.

После допустимой правки env:

```bash
sudo systemctl restart hub-portal hub-portal-health-monitor
sudo hubctl doctor
```

## 10. Что можно и нельзя редактировать

### Можно

- пользователей, права, CIDR и сервисы — через web-интерфейс;
- firewall — штатными средствами ОС;
- DNS — у своего DNS-провайдера;
- документированные несекретные env-параметры — с backup и restart;
- логи — читать через `hubctl logs` и `journalctl`.

### Нельзя

- править код внутри `/opt/hub-portal/current`;
- устанавливать пакеты pip в `/opt/hub-portal/venv` вручную;
- вручную переключать symlink `current` и `venv`;
- редактировать managed nginx-vhost сервиса: портал перезапишет его;
- менять данные MariaDB прямыми `UPDATE/DELETE`;
- удалять `/opt/hub-portal/runtime` во время работы;
- открывать MariaDB наружу;
- хранить секреты в shell history или Git.

Для нестандартного nginx используйте `Ручной режим` сервиса только если понимаете
последствия. После каждой ручной правки обязателен `nginx -t`.

## 11. Логи и аудит

### Журналы systemd

```bash
sudo hubctl logs 200
sudo journalctl -u hub-portal -f
sudo journalctl -u hub-portal-health-monitor -f
```

### nginx и certbot

```text
/var/log/nginx/hub-portal.error.log
/var/log/nginx/hub-bot-noise.log
/var/log/nginx/hub-bot-noise.error.log
/var/log/nginx/<service-domain>.error.log
/var/log/letsencrypt/letsencrypt.log
```

Пользовательский бизнес-аудит хранится в MariaDB (`portal_auditevent`) и
просматривается в разделе `Журнал`. В nginx-файлы не должны попадать подробные
действия пользователей внутри downstream-приложений.

## 12. Резервные копии

### Штатный operational backup

```bash
sudo hubctl backup
```

Результат создаётся в `/var/backups/hub-portal/<UTC-timestamp>/`:

```text
database.sql.gz     дамп MariaDB
hub-portal.env      секреты и конфигурация
nginx.tar.gz        основной HUB vhost, log-mode и hub-portal conf.d
release-path        путь текущего release
```

Это backup данных и базовой конфигурации, а не полный образ VM. Текущая команда
не архивирует все сертификаты Let's Encrypt, все service-vhost и системные пакеты.

### Backup только БД

```bash
sudo install -d -m 700 /var/backups/hub-portal/manual
sudo mariadb-dump hub_portal | gzip | \
  sudo tee /var/backups/hub-portal/manual/database-$(date -u +%Y%m%dT%H%M%SZ).sql.gz >/dev/null
```

### Полный disaster-recovery backup

Для гарантированного переноса используйте snapshot диска/VM. Он должен включать:

- `/opt/hub-portal`;
- `/etc/hub-portal`;
- HUB-файлы nginx и systemd;
- service-vhost;
- `/etc/letsencrypt`;
- MariaDB data или согласованный SQL dump;
- UFW/nftables-конфигурацию.

На общем сервере не архивируйте чужие сертификаты и vhost в переносимый пакет.
Выбирайте HUB-файлы отдельно либо используйте backup всей VM с защищённым
хранилищем.

## 13. Восстановление

### Восстановление БД на том же сервере

```bash
BACKUP=/var/backups/hub-portal/20260101T120000Z
sudo systemctl stop hub-portal hub-portal-health-monitor
sudo mariadb -e "DROP DATABASE IF EXISTS hub_portal; CREATE DATABASE hub_portal CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
gzip -dc "$BACKUP/database.sql.gz" | sudo mariadb hub_portal
sudo systemctl start hub-portal hub-portal-health-monitor
sudo hubctl doctor
```

Перед `DROP DATABASE` проверьте путь и наличие читаемого dump.

### Перенос на новый сервер

1. Сделайте чистую установку той же или совместимой версии.
2. Остановите HUB.
3. Сохраните новый env отдельно.
4. Восстановите старые `hub-portal.env` и БД вместе — ключ шифрования нужен для
   старых токенов.
5. Установите пароль MariaDB-пользователя в значение из восстановленного env.
6. Восстановите или перевыпустите сертификаты и service-vhost.
7. Выполните migrations, `collectstatic`, `nginx -t` и `hubctl doctor`.

Полное восстановление лучше выполнять в отдельном maintenance window. Сначала
проверьте его на копии VM.

## 14. Проверка после установки

```bash
sudo hubctl status
sudo hubctl doctor
sudo nginx -t
sudo systemctl list-timers 'hub-portal-*'
curl -I https://hub.example.com/
```

Проверьте через браузер:

1. Вход администратора.
2. Смену темы.
3. Разделы `Сервисы`, `Управление`, `Пользователи`, `Журнал`, `Безопасность`.
4. Создание тестового пользователя и вход под ним.
5. Открытие разрешённого сервиса и отказ для запрещённого.

## 15. Удаление

```bash
sudo hubctl uninstall
```

Нужно ввести `REMOVE HUB`. Команда сначала создаёт backup, затем отключает units,
удаляет основной vhost и команды HUB. Код, БД и env намеренно сохраняются для
ручного восстановления или окончательного удаления.

## 16. Диагностика

```bash
sudo hubctl doctor
sudo hubctl logs 300
sudo nginx -t
sudo systemctl status mariadb nginx hub-portal hub-portal-health-monitor
sudo journalctl -u hub-portal --since '30 minutes ago'
```

Справочник всех операций: [`HUBCTL.md`](HUBCTL.md).
