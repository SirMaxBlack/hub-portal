# Установка HUB Portal

## Перед началом

Рекомендуется отдельная Debian 12+ или Ubuntu 24.04+ VM. Если сервер общий, проверьте занятые порты и существующие vhost:

```bash
sudo ss -lntup
sudo nginx -T
sudo systemctl --type=service --state=running
```

Установщик не удаляет существующие сайты nginx и не добавляет default/catch-all vhost.

## Режимы

### `domain`

Основной режим. Нужны DNS и открытые порты 80/443. Установщик получает сертификат Let's Encrypt и включает secure cookie для портала и его поддоменов.

### `local`

HTTP на выбранном порту, по умолчанию 8080. Подходит для лаборатории. Защищённая публикация сервисов под поддоменами требует последующего перехода на domain-режим.

## IP/CIDR

- `unrestricted`: окно входа доступно с любого адреса;
- `restricted`: окно входа и защищённые сервисы доступны только из перечисленных IP/CIDR.

Не включайте restricted-режим, не добавив собственный текущий адрес: можно закрыть себе доступ.

## Файлы после установки

```text
/opt/hub-portal/current                 текущий release symlink
/opt/hub-portal/releases/               версии приложения
/opt/hub-portal/venv                    текущий venv symlink
/opt/hub-portal/venvs/                  venv каждой версии
/opt/hub-portal/runtime/                runtime state
/etc/hub-portal/hub-portal.env          секреты и конфигурация
/etc/nginx/sites-available/hub-portal   основной vhost
/usr/local/bin/hubctl                   управление
```

## Проверка

```bash
sudo hubctl doctor
sudo systemctl status hub-portal
sudo nginx -t
```

## Перенос без Git

Вся self-hosted версия находится в одной директории проекта. Её можно скопировать
на другой компьютер и запустить `sudo ./install.sh` из корня.

Для чистого переносимого архива без `.git`, локальной БД, runtime и секретов:

```bash
python3 deploy/scripts/build_portable_bundle.py
```

Архив и SHA-256 checksum появятся в `dist/`. После распаковки установка запускается
тем же `sudo ./install.sh`.

## UFW

Если UFW активен, установщик предложит правила. В restricted-режиме создаются allow only для заданных CIDR и порта портала. Скрипт не включает UFW сам.
