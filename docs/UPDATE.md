# Обновление и rollback

## Backup

```bash
sudo hubctl backup
```

Backup содержит дамп MariaDB, env и HUB-specific nginx-конфигурацию. Путь выводится после выполнения.

## Обновление

```bash
sudo hubctl update
sudo hubctl update v0.2.0
```

`hubctl` скачивает release и checksum, создаёт отдельные release/venv, выполняет Django checks, миграции и `collectstatic`, затем атомарно переключает symlink.

Для приватного репозитория:

```bash
sudo hubctl github-token set
sudo hubctl github-token check
sudo hubctl update
```

Token вводится скрыто, проверяется через GitHub API и хранится только в
`/etc/hub-portal/github.token` с правами `0600 root:root`. Значение не попадает в
аргументы процессов, журналы и env приложения.

```bash
sudo hubctl github-token status
sudo hubctl github-token remove
```

Для разового обновления без сохранения по-прежнему поддерживается
`sudo -E hubctl update` с `GITHUB_TOKEN` в окружении. При публичном репозитории
token не нужен.

## Rollback

```bash
sudo hubctl rollback
sudo hubctl rollback 0.1.0-20260803T120000Z
```

Rollback возвращает код и venv. Миграции БД назад автоматически не выполняются. Перед обновлением всегда создаётся backup; для DB rollback восстановите соответствующий дамп в maintenance window.
