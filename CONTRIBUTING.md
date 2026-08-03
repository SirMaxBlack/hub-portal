# Участие в разработке

1. Создайте ветку от `main`.
2. Не добавляйте production env, сертификаты, DB dump и реальные IP/домены.
3. Добавьте тесты для изменения поведения.
4. Выполните:

```bash
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py test portal
python manage.py collectstatic --noinput
shellcheck install.sh deploy/scripts/*.sh
```

5. Откройте draft PR с описанием риска и rollback.

Изменения installer/nginx/auth требуют отдельной проверки на чистой Debian 12 VM.
