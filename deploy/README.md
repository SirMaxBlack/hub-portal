# Deployment files

Этот каталог содержит production-шаблоны и служебные файлы self-hosted HUB Portal.

- `scripts/install.sh` выполняет полную интерактивную установку.
- `scripts/hubctl` управляет установленным экземпляром.
- `scripts/hub-portal-add-service.sh` добавляет защищённый reverse proxy.
- `nginx/` содержит шаблоны portal vhost и общие snippets.
- `systemd/` содержит unit- и timer-файлы.
- `logrotate/` содержит ротацию HUB-specific nginx logs.
- `sudoers/` содержит минимальные разрешения для root helper-команд.

Обычная установка запускается из корня репозитория:

```bash
sudo ./install.sh
```

Полная инструкция: [`docs/INSTALL.md`](../docs/INSTALL.md).
