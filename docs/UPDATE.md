# Обновление и rollback HUB Portal

## 1. Основной принцип

HUB не обновляет файлы поверх текущего кода. Для каждой версии создаются новые
директории:

```text
/opt/hub-portal/releases/<release-id>
/opt/hub-portal/venvs/<release-id>
```

После подготовки атомарно переключаются symlink:

```text
/opt/hub-portal/current
/opt/hub-portal/venv
```

Это позволяет быстро вернуть код предыдущего release. MariaDB остаётся общей
для всех версий.

## 2. Перед обновлением

Проверьте здоровье и свободное место:

```bash
sudo hubctl status
sudo hubctl doctor
df -h /opt /var
sudo systemctl is-active mariadb nginx hub-portal hub-portal-health-monitor
```

Сделайте отдельный backup и запишите его путь:

```bash
sudo hubctl backup
```

Для alpha-релиза или миграции схемы БД рекомендуется maintenance window и
snapshot VM.

## 3. Обновление до последнего release

```bash
sudo hubctl update
```

Эквивалентно:

```bash
sudo hubctl update latest
```

Команда выбирает недрафтовый GitHub Release с самой поздней датой публикации.
На alpha-стадии это может быть prerelease. Для воспроизводимой установки строго
заданной версии используйте tag из следующего раздела.

## 4. Обновление до конкретной версии

```bash
sudo hubctl update v0.2.0
```

Tag должен существовать в GitHub Releases и содержать два assets:

```text
hub-portal-<version>.tar.gz
hub-portal-<version>.tar.gz.sha256
```

## 5. Что делает `hubctl update`

1. Загружает `/etc/hub-portal/hub-portal.env`.
2. Получает metadata release через GitHub API.
3. При необходимости применяет root-only GitHub token.
4. Скачивает archive и checksum во временный каталог.
5. Проверяет SHA-256 до распаковки.
6. Создаёт новый release и новый Python venv.
7. Устанавливает зависимости из release `requirements.txt`.
8. Выполняет `manage.py check` новым кодом.
9. Автоматически запускает `hubctl backup`.
10. Выполняет migrations MariaDB.
11. Выполняет `collectstatic --noinput`.
12. Проверяет наличие manifest `staticfiles/staticfiles.json`.
13. Создаёт новый `.release-id`.
14. Переключает symlink к новому коду и venv.
15. Обновляет systemd units, helpers, sudoers, nginx snippets и logrotate.
16. Выполняет `nginx -t`, reload nginx, restart приложения и health monitor.
17. Запускает `hubctl doctor`.

Новая версия считается активной только после успешных проверок.

## 6. Что происходит при ошибке

Если после переключения не проходят nginx, restart или `doctor`, `hubctl`
возвращает symlink к предыдущему коду и venv и перезапускает службы.

Важно: уже выполненные migrations MariaDB автоматически назад не откатываются.
Поэтому автоматический rollback защищает код, но не заменяет восстановление БД.

## 7. Проверка после обновления

```bash
sudo hubctl version
sudo hubctl status
sudo hubctl doctor
sudo nginx -t
sudo hubctl logs 100
```

Проверьте browser smoke:

- вход по паролю и токену;
- каталог сервисов и health-status;
- открытие reverse proxy;
- админские разделы;
- смену пароля;
- активные сессии;
- фильтры журнала;
- новую статику без ручной очистки browser cache.

## 8. Fingerprinted static и открытые вкладки

`collectstatic` создаёт имена CSS/JS с content hash. Новый HTML ссылается на
новые файлы, а старые assets могут безопасно оставаться в immutable browser
cache.

`.release-id` публикуется через `/api/release-state/`. Уже открытая вкладка
периодически проверяет его и перезагружается при смене release.

## 9. Приватный fork

Настройка token:

```bash
sudo hubctl github-token set
sudo hubctl github-token check
sudo hubctl github-token status
```

После этого:

```bash
sudo hubctl update v0.2.0
```

Удаление сохранённого token:

```bash
sudo hubctl github-token remove
```

Token хранится в `/etc/hub-portal/github.token`, `0600 root:root`, не передаётся
в аргументах curl и не входит в env Django.

## 10. Просмотр доступных локальных release

```bash
sudo hubctl rollback
```

Команда без ID ничего не меняет и показывает каталоги в
`/opt/hub-portal/releases/`.

## 11. Rollback кода

```bash
sudo hubctl rollback 0.1.0-20260803T120000Z
```

Используйте именно release ID из списка, а не обязательно Git tag. Команда:

1. Проверяет наличие release и соответствующего venv.
2. Переключает оба symlink.
3. Перезапускает приложение и health monitor.
4. Выполняет `doctor`.
5. При ошибке возвращает прежние symlink.

## 12. Когда одного rollback кода недостаточно

Восстановление БД требуется, если новый release:

- удалил или переименовал таблицы/поля;
- преобразовал данные необратимо;
- сделал старый код несовместимым со схемой;
- изменил формат зашифрованных данных.

Тогда остановите HUB, восстановите `database.sql.gz` от нужной версии и только
после этого запускайте старый код. Общая процедура описана в
[`INSTALL.md`](INSTALL.md#13-восстановление).

## 13. Обновление конфигурации

`hubctl update` обновляет поставляемые units, helpers, sudoers и общие nginx
snippets из release. Основной `/etc/hub-portal/hub-portal.env` не заменяется.

Новый release может добавить обязательную env-переменную с безопасным default.
Если переменная обязательна без default, это должно быть указано в CHANGELOG.
Перед ручной правкой env создайте backup.

## 14. Обновление без доступа к GitHub

Текущий `hubctl update` рассчитан на GitHub Release. Для изолированной сети:

1. На подключённом компьютере скачайте archive и `.sha256`.
2. Проверьте checksum.
3. Разверните копию на тестовом сервере.
4. Не подменяйте содержимое `/opt/hub-portal/current` вручную.

Полностью offline-команда обновления пока не является публичным интерфейсом.
Безопаснее временно разрешить доступ к GitHub Release или подготовить внутреннее
зеркало после отдельного тестирования.

## 15. Удаление старых release

`hubctl` пока не удаляет старые release автоматически. Не удаляйте:

- каталог, на который указывает `current`;
- каталог, на который указывает `venv`;
- release, который нужен для rollback.

Проверка:

```bash
readlink -f /opt/hub-portal/current
readlink -f /opt/hub-portal/venv
sudo du -sh /opt/hub-portal/releases/* /opt/hub-portal/venvs/*
```

Удаление старых каталогов делайте только после backup и проверки новой версии.

## 16. Типовые проблемы

### GitHub возвращает 404

Для приватного fork настройте token:

```bash
sudo hubctl github-token set
sudo hubctl github-token check
```

### Checksum не совпал

Release не применяется. Не обходите проверку: повторите загрузку и проверьте
assets GitHub Release.

### Не создан static manifest

Обновление останавливается до переключения приложения. Проверьте `collectstatic`,
место на диске и права release-каталога.

### nginx-конфиг невалиден

```bash
sudo nginx -t
sudo nginx -T 2>&1 | less
```

Не делайте reload до исправления ошибки.

### После обновления 502

```bash
sudo systemctl status hub-portal
sudo journalctl -u hub-portal -n 200 --no-pager
sudo ls -la /run/hub-portal/
sudo hubctl doctor
```

Полный справочник: [`HUBCTL.md`](HUBCTL.md).
