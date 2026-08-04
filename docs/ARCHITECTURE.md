# Архитектура

```mermaid
flowchart LR
    U[Браузер] --> N[nginx]
    N -->|portal| G[Gunicorn + Django]
    G --> M[(MariaDB localhost)]
    N -->|auth_request| A[/internal/authz]
    A --> G
    N -->|разрешено| S[Внутренний сервис]
    G -->|прямая ссылка| E[Внешний сайт]
```

## Граница доверия

nginx принимает внешний трафик. Django создаёт сессии, проверяет глобальный CIDR, состояние пользователя и право на сервис. Для reverse proxy nginx вызывает закрытый endpoint `internal/authz`; запрос разрешается только с внутренним shared key.

## Данные

- `User`: пользователи, роль, пароли и токены;
- `Service`: reverse proxy или external link;
- `ServiceHealthState`: runtime health;
- `UserSession`: управляемые серверные сессии;
- `AuditEvent`: бизнес-аудит;
- `PortalAccessRule` и `UserAccessRule`: глобальные и персональные сети;
- `ServiceRevision`: история nginx-конфигов.

## Релизы

Код и venv каждой версии неизменяемы. `/opt/hub-portal/current` и `/opt/hub-portal/venv` переключаются symlink-ами. Static собирается с content hash, поэтому браузер не использует старый CSS/JS после релиза.
