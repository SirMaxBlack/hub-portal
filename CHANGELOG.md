# Changelog

Формат основан на Keep a Changelog. Версии используют Semantic Versioning после стабилизации API.

## [Unreleased]

## [0.1.0-alpha.2] - 2026-08-03

### Fixed

- загрузка release assets из приватного GitHub-репозитория через API;
- выбор последнего prerelease для bootstrap и `hubctl update`;
- восстановление venv после неуспешного rollback;
- безопасная передача реквизитов MariaDB в backup;
- HTTPS/HSTS defaults и проверки shell-скриптов в CI.

### Added

- self-hosted installer для Debian 12;
- команда управления `hubctl`;
- переносимая конфигурация домена;
- тип сервиса `Внешняя ссылка`;
- стартовые ссылки Cloudflare, GitHub, ChatGPT и DeepSeek;
- GitHub CI и release pipeline.

## [0.1.0-alpha.1] - 2026-08-03

- Первая self-hosted alpha-версия HUB Portal.
