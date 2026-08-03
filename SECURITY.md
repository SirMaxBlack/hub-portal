# Безопасность

## Сообщение об уязвимости

Не публикуйте рабочий exploit, токены, cookie, IP внутренней сети или env в Issues.

Создайте private security advisory в GitHub: **Security → Advisories → New draft advisory**. Укажите версию, сценарий, влияние и минимальные шаги воспроизведения.

## Поддерживаемые версии

До выхода `1.0` security fixes выпускаются только для последнего GitHub Release.

## Обязательные правила эксплуатации

- не включать `DEBUG` в production;
- не хранить `/etc/hub-portal/hub-portal.env` в Git;
- ограничить SSH и административный раздел доверенными сетями;
- регулярно выполнять `hubctl backup` и `hubctl update`;
- не отключать `nginx -t` и preflight helper-ов;
- использовать отдельную БД и локальный bind MariaDB;
- не публиковать административные upstream напрямую в обход HUB.
