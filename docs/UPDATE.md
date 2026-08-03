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
export GITHUB_TOKEN='github_token_with_repo_read_access'
sudo -E hubctl update
unset GITHUB_TOKEN
```

## Rollback

```bash
sudo hubctl rollback
sudo hubctl rollback 0.1.0-20260803T120000Z
```

Rollback возвращает код и venv. Миграции БД назад автоматически не выполняются. Перед обновлением всегда создаётся backup; для DB rollback восстановите соответствующий дамп в maintenance window.
