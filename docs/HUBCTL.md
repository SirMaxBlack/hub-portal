# Справочник команд `hubctl`

## Общие правила

`hubctl` устанавливается в `/usr/local/bin/hubctl`. Для единообразия запускайте
команды через `sudo`: конфигурация содержит секреты и не читается обычным
пользователем.

```bash
sudo hubctl help
```

При ошибке команда возвращает ненулевой exit code. Это позволяет использовать
`hubctl doctor` в мониторинге и автоматизации.

## `hubctl help`

Показывает список поддерживаемых команд.

```bash
sudo hubctl help
sudo hubctl --help
```

Команда ничего не меняет.

## `hubctl status`

Показывает версию, release ID, публичный URL и полный systemd status приложения
и health monitor.

```bash
sudo hubctl status
```

Используйте первым шагом при 502, проблемах health-check или после reboot.

## `hubctl doctor`

Запускает комплексную самопроверку:

- `mariadb` active;
- `nginx` active;
- `hub-portal` active;
- `hub-portal-health-monitor` active;
- `nginx -t` успешен;
- `manage.py check --deploy` успешен;
- Django может прочитать план migrations из MariaDB.

```bash
sudo hubctl doctor
```

Пример автоматической проверки:

```bash
if sudo hubctl doctor; then
  echo 'HUB исправен'
else
  echo 'HUB требует диагностики' >&2
fi
```

Команда не проверяет доступность каждого downstream с точки зрения внешнего
браузера. Их состояние показывает web-раздел `Сервисы`.

## `hubctl version`

Печатает версию из `/opt/hub-portal/current/VERSION`.

```bash
sudo hubctl version
```

Подходит для скриптов:

```bash
installed_version="$(sudo hubctl version)"
printf 'Installed: %s\n' "$installed_version"
```

## `hubctl restart`

Перезапускает Gunicorn и health monitor, затем запускает `doctor`.

```bash
sudo hubctl restart
```

Используйте после допустимого изменения `/etc/hub-portal/hub-portal.env`. Команда
не перезапускает MariaDB и nginx.

Если менялся nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo hubctl doctor
```

## `hubctl logs [N]`

Показывает последние `N` строк journald двух основных units. По умолчанию — 100.

```bash
sudo hubctl logs
sudo hubctl logs 200
sudo hubctl logs 1000
```

Для live-режима используйте напрямую:

```bash
sudo journalctl -u hub-portal -u hub-portal-health-monitor -f
```

`hubctl logs` не показывает nginx error log, certbot и DB-аудит.

## `hubctl backup`

Создаёт operational backup и печатает его каталог.

```bash
sudo hubctl backup
```

Пример результата:

```text
/var/backups/hub-portal/20260804T120000Z
```

Содержимое:

- `database.sql.gz` — MariaDB dump;
- `hub-portal.env` — секреты и конфигурация;
- `nginx.tar.gz` — основной HUB vhost, log-mode и `hub-portal-*.conf`;
- `release-path` — текущий release.

Скопируйте backup в защищённое внешнее хранилище:

```bash
sudo tar -C /var/backups/hub-portal -czf /root/hub-backup.tar.gz 20260804T120000Z
sudo sha256sum /root/hub-backup.tar.gz
```

Backup содержит секреты. Не загружайте его в Git и не храните с публичным
доступом. Это не полный snapshot сертификатов и всех системных файлов; подробности
в [INSTALL.md](INSTALL.md#12-резервные-копии).

## `hubctl update [tag|latest]`

Скачивает GitHub Release, проверяет checksum, создаёт новый release/venv,
выполняет backup, migrations, static build и переключает версию после проверок.

```bash
sudo hubctl update
sudo hubctl update latest
sudo hubctl update v0.2.0
```

Рекомендуемый предсказуемый вариант:

```bash
sudo hubctl backup
sudo hubctl update v0.2.0
sudo hubctl doctor
```

Если указанная версия уже установлена, команда сообщает об этом и ничего не
меняет. Детальный алгоритм: [UPDATE.md](UPDATE.md).

## `hubctl github-token status`

Показывает, откуда может быть взят GitHub token, но не раскрывает его значение.

```bash
sudo hubctl github-token status
```

Возможные состояния:

- token передан через окружение;
- token сохранён в root-only файле;
- token не настроен.

Для публичного репозитория token не нужен.

## `hubctl github-token set`

Скрыто запрашивает token, проверяет доступ к configured GitHub repository и
только после успеха сохраняет его.

```bash
sudo hubctl github-token set
```

Для pipe/automation:

```bash
printf '%s\n' "$GITHUB_TOKEN" | sudo hubctl github-token set
```

Избегайте второго варианта в shared CI без защищённого secret store.

Файл:

```text
/etc/hub-portal/github.token
owner: root:root
mode: 0600
```

## `hubctl github-token check`

Проверяет сохранённый или переданный token запросом к GitHub API.

```bash
sudo hubctl github-token check
```

Команда не выполняет update.

## `hubctl github-token remove`

Удаляет сохранённый token.

```bash
sudo hubctl github-token remove
sudo hubctl github-token status
```

Установленные release и работающий портал не меняются. Следующее обновление
приватного fork потребует новый token.

## `hubctl rollback [release-id]`

Без параметра показывает локальные release:

```bash
sudo hubctl rollback
```

С параметром переключает код и venv:

```bash
sudo hubctl rollback 0.1.0-20260803T120000Z
```

Git tag и release ID отличаются: используйте ID из вывода команды без параметра.
После переключения выполняются restart и `doctor`. Если проверка не проходит,
прежние symlink возвращаются.

Rollback не откатывает MariaDB migrations. Для несовместимой схемы восстановите
backup БД соответствующей версии.

## `hubctl uninstall`

Безопасно отключает системную интеграцию HUB.

```bash
sudo hubctl uninstall
```

Для подтверждения нужно точно ввести:

```text
REMOVE HUB
```

Команда:

1. Создаёт backup.
2. Останавливает и отключает HUB units/timers.
3. Удаляет основной nginx-vhost HUB.
4. Проверяет nginx и делает reload.
5. Удаляет установленные unit-файлы и helper-команды.
6. Оставляет код, env и MariaDB для восстановления.

Она не является «стереть всё». Окончательное удаление данных выполняется
вручную только после проверки backup.

## Типовой сценарий обслуживания

```bash
sudo hubctl status
sudo hubctl doctor
sudo hubctl backup
sudo hubctl update v0.2.0
sudo hubctl doctor
sudo hubctl logs 100
```

## Типовой сценарий диагностики 502

```bash
sudo hubctl status
sudo hubctl logs 300
sudo systemctl status hub-portal
sudo nginx -t
sudo ls -la /run/hub-portal/hub-portal.sock
sudo hubctl doctor
```

## Что `hubctl` пока не делает

- не выполняет offline-update из локального архива;
- не откатывает migrations назад;
- не восстанавливает backup одной командой;
- не удаляет старые release автоматически;
- не заменяет snapshot VM;
- не управляет DNS у провайдера.

Не используйте недокументированные внутренние helpers вместо `hubctl` без
необходимости: они предназначены для web-backend и могут иметь другой контракт.
