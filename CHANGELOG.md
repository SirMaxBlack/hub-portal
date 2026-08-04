# Changelog

Формат основан на Keep a Changelog. Версии используют Semantic Versioning после
стабилизации API.

## [Unreleased]

## [0.1.0-alpha.9] - 2026-08-04

### Added

- интерактивная установка на Debian 12+ и Ubuntu 24.04+;
- управление установкой через `hubctl`;
- команды `hubctl github-token set/status/check/remove` для приватных GitHub
  Releases;
- роли, права на сервисы, IP/CIDR, токены, активные сессии и аудит;
- защищённые reverse proxy, внешние ссылки и health-check;
- backup, update, rollback и portable archive с SHA-256.

### Fixed

- `hubctl update` и bootstrap не передают пустой HTTP-ответ в JSON parser;
- ошибки GitHub API `401/403/404`, сети и release JSON выводятся без traceback;
- повторное обновление уже установленной версии не создаёт новый release/venv;
- GitHub token не попадает в argv, журналы и env приложения.

## [0.1.0-alpha.1] - 2026-08-03

- Первая публичная alpha-версия HUB Portal.
