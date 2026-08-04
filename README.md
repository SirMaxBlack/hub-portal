<p align="center">
  <img src="docs/assets/hub-network-background.png" alt="HUB Portal" width="100%">
</p>

<h1 align="center">HUB Portal</h1>

<p align="center">
  Self-hosted портал единого доступа к внутренним сервисам.<br>
  Django, MariaDB, nginx, суточные сессии, роли, аудит и защищённые reverse proxy.
</p>

<p align="center">
  <img alt="Debian 12 and Ubuntu 24.04" src="https://img.shields.io/badge/Debian_12%20%7C%20Ubuntu_24.04-supported-A81D33?logo=linux&logoColor=white">
  <img alt="Django 5.2 LTS" src="https://img.shields.io/badge/Django-5.2_LTS-0C4B33?logo=django&logoColor=white">
  <img alt="Python 3.11" src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="License AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-22a6d5">
  <img alt="Status alpha" src="https://img.shields.io/badge/status-alpha-f0a202">
</p>

> Проект находится в alpha-стадии. Перед установкой на важный сервер используйте отдельную VM и сохраняйте backup.

## Что это

HUB Portal показывает сотруднику только разрешённые сервисы. Администратор управляет пользователями, правами, токенами, IP/CIDR-доступом, журналом, состоянием сервисов и их nginx-конфигурацией из одного интерфейса.

Проект разрабатывается с использованием OpenAI Codex. Изменения проходят
автоматические проверки и тестируются на отдельной self-hosted установке.

Доступны два типа карточек:

- **Защищённый reverse proxy**: внутренний сервис публикуется под `*.hub.example.com`, а nginx проверяет сессию и права через `auth_request`.
- **Внешняя ссылка**: безопасный переход на уже существующий публичный сайт без проксирования cookie, OAuth и содержимого через HUB.

## Возможности

- вход по логину/паролю или постоянному токену;
- Argon2id для паролей и зашифрованное хранение отображаемых токенов;
- суточные серверные сессии и управление активными устройствами;
- отдельные роли администратора и пользователя;
- права на каждый сервис;
- глобальные и персональные IP/CIDR allowlist;
- ограничение ошибок входа по учётке и серверному browser identity;
- журнал бизнес-событий, фильтры, CSV и безопасная очистка;
- health-check сервисов и блокировка подтверждённо недоступных upstream;
- визуальный редактор nginx-опций, профили и ревизии;
- светлая/тёмная тема и адаптивный интерфейс;
- fingerprinted static, immutable cache и автообновление открытых вкладок;
- установка, диагностика, backup, update и rollback через `hubctl`.

## Быстрая установка

### Публичный репозиторий

Для установки из последнего публичного GitHub Release:

```bash
bash <(curl -Ls https://raw.githubusercontent.com/SirMaxBlack/hub-portal/main/install.sh)
```

По умолчанию bootstrap всегда выбирает самый поздно опубликованный Release.
Если нужна конкретная версия, tag задаётся отдельно и только для этого запуска:

```bash
HUB_PORTAL_VERSION=v0.1.0-alpha.11 \
  bash <(curl -Ls https://raw.githubusercontent.com/SirMaxBlack/hub-portal/main/install.sh)
```

Обычная установка не требует `HUB_PORTAL_VERSION`.

### Приватный форк

```bash
git clone https://github.com/SirMaxBlack/hub-portal.git
cd hub-portal
sudo ./install.sh
```

Для bootstrap через API приватного репозитория можно передать GitHub token только через переменную окружения:

```bash
export GITHUB_TOKEN='github_token_with_repo_read_access'
sudo -E ./install.sh
unset GITHUB_TOKEN
```

При такой установке token проверяется bootstrap-запросом и сохраняется для
последующих обновлений в `/etc/hub-portal/github.token` с правами
`0600 root:root`. Автосохранение можно отключить через
`HUB_PORTAL_STORE_GITHUB_TOKEN=0`.

Установщик пошагово спросит:

1. HTTPS-домен или локальный HTTP-режим — выбор цифрой с пояснениями и примерами.
2. Доступ со всех адресов или только с заданных IP/CIDR — выбор цифрой.
3. Логин, имя и скрытый пароль первого администратора.
4. Часовой пояс из готового списка или собственное значение.
5. Нужно ли добавить правила в активный UFW — выбор цифрой.
6. Итоговое подтверждение перед установкой пакетов и изменением системы.

Все варианты выбора имеют цифровые пункты. Перед текстовыми полями установщик
объясняет назначение значения и показывает корректный пример.

Подробности: [инструкция установки](docs/INSTALL.md).

## Управление

```bash
sudo hubctl status
sudo hubctl doctor
sudo hubctl backup
sudo hubctl github-token status
sudo hubctl github-token set
sudo hubctl github-token check
sudo hubctl update
sudo hubctl rollback
sudo hubctl logs 200
sudo hubctl restart
```

Все команды: `hubctl help`.

## Стартовый каталог

Чистая установка содержит четыре внешние ссылки, видимые администратору:

- Cloudflare Dashboard;
- GitHub;
- ChatGPT;
- DeepSeek.

Это именно прямые ссылки. Проксировать эти сайты через HUB нельзя: OAuth, CSP, anti-bot, cookie scope и условия эксплуатации делают reverse proxy нестабильным и небезопасным.

## Требования

- чистая или контролируемая VM с Debian 12+ либо Ubuntu 24.04+;
- root/sudo;
- минимум 1 vCPU, 1 ГБ RAM и 5 ГБ диска;
- для HTTPS: DNS A/AAAA на сервер и доступные `80/tcp`, `443/tcp`;
- MariaDB и nginx могут быть установлены скриптом.

Установщик не удаляет чужие vhost и не создаёт глобальный catch-all server. При конфликте собственного порта установка останавливается до изменений nginx.

## Безопасность

- MariaDB слушает локальный интерфейс;
- секреты находятся в `/etc/hub-portal/hub-portal.env` с ограниченными правами;
- пароль администратора не передаётся аргументом процесса;
- HTML и release-state отдаются с `no-store`, hashed static — с `immutable`;
- service helper проверяет домен, выполняет `nginx -t` и откатывает конфиг при ошибке;
- удаление reverse proxy очищает vhost, ссылку и сертификат только выбранного домена;
- реальные пользовательские события хранятся в БД, bot noise — отдельно в nginx logs.

Сообщить об уязвимости: [SECURITY.md](SECURITY.md).

## Структура

```text
hub_portal/     Django settings, urls, WSGI
portal/         модели, views, шаблоны, static, management commands
deploy/         installer, hubctl, nginx/systemd/logrotate templates
docs/           установка, обновление, архитектура
.github/        CI, release workflow, Dependabot
```

## Разработка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py test portal
python manage.py runserver
```

Без `HUB_PORTAL_DB_NAME` при локальной разработке используется SQLite. Рабочая
установка использует MariaDB.

Вся self-hosted версия автономна внутри этой директории. Для переноса без Git и
локальных runtime-файлов выполните:

```bash
python3 deploy/scripts/build_portable_bundle.py
```

Готовый архив и checksum будут созданы в `dist/`.

## Документация

### Начало работы и эксплуатация

- [Установка и устройство установленной системы](docs/INSTALL.md) — режимы установки, пакеты, файлы, службы, БД, логи, backup и восстановление.
- [Обновление и rollback](docs/UPDATE.md) — полный порядок обновления, проверки, автоматический откат кода и восстановление БД.
- [Справочник `hubctl`](docs/HUBCTL.md) — все команды управления с параметрами, примерами и ожидаемым результатом.
- [Архитектура](docs/ARCHITECTURE.md) — компоненты, потоки запросов, данные и границы доверия.
- [Модель защиты](docs/SECURITY_MODEL.md) — как защищены портал и reverse proxy, доменные схемы и ограничения безопасности.

### Web-интерфейс

- [Карта web-разделов](docs/web/README.md)
- [Авторизация и первый вход](docs/web/AUTHENTICATION.md)
- [Сервисы](docs/web/SERVICES.md)
- [Учётная запись](docs/web/ACCOUNT.md)
- [Управление сервисами](docs/web/MANAGEMENT.md)
- [Пользователи](docs/web/USERS.md)
- [Журнал](docs/web/AUDIT.md)
- [Безопасность](docs/web/SECURITY.md)
- [Настройки визуала](docs/web/VISUAL.md)

### Проект

- [Политика сообщения об уязвимости](SECURITY.md)
- [Участие в разработке](CONTRIBUTING.md)
- [История изменений](CHANGELOG.md)

## Лицензия

AGPL-3.0. См. [LICENSE](LICENSE).
