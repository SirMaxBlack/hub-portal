from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse


RECOMMENDATION_HIGH = "high"
RECOMMENDATION_MEDIUM = "medium"
RECOMMENDATION_LOW = "low"

RECOMMENDATION_LABELS = {
    RECOMMENDATION_HIGH: "Рекомендуется",
    RECOMMENDATION_MEDIUM: "По ситуации",
    RECOMMENDATION_LOW: "Точечно",
}

RECOMMENDATION_NOTES = {
    RECOMMENDATION_HIGH: "Обычно включается для большинства web-сервисов.",
    RECOMMENDATION_MEDIUM: "Нужно смотреть на поведение downstream-приложения.",
    RECOMMENDATION_LOW: "Используется только под конкретный кейс совместимости или трафика.",
}

DEFAULT_SERVICE_FEATURES = (
    "security_headers",
    "websocket_support",
    "long_timeouts",
)


@dataclass(frozen=True)
class ServiceOptionDefinition:
    key: str
    title: str
    description: str
    summary: str
    contexts: str
    category: str
    recommendation: str
    when_to_use: str
    when_not_to_use: str = ""
    dependencies: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    practices: tuple[str, ...] = ()
    directives: tuple[str, ...] = ()
    locked: bool = False
    default_enabled: bool = False
    auto_when_path_prefix: bool = False


@dataclass(frozen=True)
class ServiceProfileDefinition:
    key: str
    title: str
    description: str
    when_to_use: str
    recommendation: str
    features: tuple[str, ...]
    caution: str = ""
    examples: tuple[str, ...] = ()


SERVICE_OPTION_DEFINITIONS: tuple[ServiceOptionDefinition, ...] = (
    ServiceOptionDefinition(
        key="auth_request_gate",
        title="auth_request gate",
        description="Системный шлюз HUB: без него сервис откроется в обход ролей и прав.",
        summary="Главный предохранитель портала. Любой запрос к поддомену сначала проходит проверку текущей сессии в HUB и только потом идёт в upstream.",
        contexts="443 · location",
        category="Безопасность",
        recommendation=RECOMMENDATION_HIGH,
        when_to_use="Всегда. Это базовая защита сервиса внутри HUB.",
        when_not_to_use="Никогда не отключать для сервисов, которые должны жить за порталом.",
        examples=(
            "CRM, NetBox, Home Assistant, Frigate, биллинг и любые внутренние web-сервисы.",
        ),
        practices=(
            "Считайте эту функцию частью каркаса HUB, а не пользовательской опцией.",
            "Если downstream внезапно открывается без авторизации, сначала проверяйте именно эту точку.",
        ),
        directives=(
            "auth_request /_portal_auth;",
            "error_page 401 = /_portal_login;",
            "error_page 403 = /_portal_forbidden;",
        ),
        locked=True,
        default_enabled=True,
    ),
    ServiceOptionDefinition(
        key="https_redirect",
        title="HTTP -> HTTPS redirect",
        description="Все обращения на 80 порт перенаправляются на HTTPS.",
        summary="Держит единый защищённый вход. Даже если пользователь вручную ввёл `http://`, nginx уводит его на TLS-вариант домена.",
        contexts="80 · server",
        category="Транспорт",
        recommendation=RECOMMENDATION_HIGH,
        when_to_use="Всегда для публичных web-сервисов HUB.",
        when_not_to_use="Не отключать, если домен выдаётся наружу через интернет.",
        examples=(
            "Пользователь открывает `http://crm.hub.example.com` и сразу уходит на HTTPS.",
        ),
        practices=(
            "Держите единый внешний URL только на 443, чтобы не плодить смешанные сценарии.",
        ),
        directives=(
            "return 301 https://$host$request_uri;",
        ),
        locked=True,
        default_enabled=True,
    ),
    ServiceOptionDefinition(
        key="http2_tls",
        title="HTTP/2 для TLS listener",
        description="Включает HTTP/2 на HTTPS listener.",
        summary="Нормальный режим для современных браузерных сервисов: меньше накладных расходов на соединения, лучше отдача SPA, ассетов и API.",
        contexts="443 · server",
        category="Транспорт",
        recommendation=RECOMMENDATION_HIGH,
        when_to_use="Оставлять включённым почти всегда.",
        when_not_to_use="Только если конкретный клиент или reverse-proxy ломается именно на HTTP/2.",
        examples=(
            "Большинство обычных web-панелей за HUB.",
        ),
        practices=(
            "Отключение имеет смысл только после подтверждённой проблемы, а не профилактически.",
        ),
        directives=(
            "listen 443 ssl http2;",
            "listen [::]:443 ssl http2;",
        ),
        locked=True,
        default_enabled=True,
    ),
    ServiceOptionDefinition(
        key="security_headers",
        title="Security headers snippet",
        description="Подключает стандартный набор защитных HTTP-заголовков HUB.",
        summary="Даёт безопасные дефолты для браузера: ограничения на встроенный контент, базовую anti-sniff защиту и другие общие HTTP-заголовки.",
        contexts="443 · server",
        category="Безопасность",
        recommendation=RECOMMENDATION_HIGH,
        when_to_use="Стартовый выбор почти для любого web-сервиса.",
        when_not_to_use="Отключать только если downstream доказанно ломается на одном из заголовков и это проверено вручную.",
        examples=(
            "Большинство админок, дашбордов и CRUD-приложений за HUB.",
        ),
        practices=(
            "Если фронтенд начинает вести себя странно, сравнивайте это с реальным набором заголовков, а не выключайте всё сразу.",
        ),
        directives=(
            "include snippets/security-headers.conf;",
        ),
        default_enabled=True,
    ),
    ServiceOptionDefinition(
        key="websocket_support",
        title="WebSocket upgrade headers",
        description="Добавляет Upgrade/Connection заголовки для realtime-каналов.",
        summary="Нужна приложениям, которые держат WebSocket или похожие живые соединения: чаты, live-дашборды, видеопотоки, операторские панели.",
        contexts="443 · location",
        category="Realtime",
        recommendation=RECOMMENDATION_MEDIUM,
        when_to_use="Когда сервис использует WebSocket, live-уведомления или постоянные двусторонние каналы.",
        when_not_to_use="Если это строго обычный HTTP без realtime и нужен максимально строгий минимальный конфиг.",
        examples=(
            "Frigate, Home Assistant, дашборды мониторинга, SPA с живыми обновлениями.",
        ),
        practices=(
            "Если не уверены, начните с включённого состояния и проверяйте браузерную консоль/Network.",
        ),
        directives=(
            "proxy_set_header Upgrade $http_upgrade;",
            "proxy_set_header Connection $connection_upgrade;",
        ),
        default_enabled=True,
    ),
    ServiceOptionDefinition(
        key="long_timeouts",
        title="Long proxy timeouts",
        description="Увеличивает connect/send/read timeout для медленных и тяжёлых сервисов.",
        summary="Полезно для внутренних приложений, которые отвечают не мгновенно, держат длинные запросы или долго прогружают большие страницы.",
        contexts="443 · location",
        category="Производительность",
        recommendation=RECOMMENDATION_MEDIUM,
        when_to_use="Realtime-панели, тяжёлые SPA, долгие отчёты, сервисы с медленными backend-ответами.",
        when_not_to_use="Если сервис очень простой и нужно быстрее отсеивать зависания upstream.",
        examples=(
            "CRM с длинными операторскими запросами, NetBox, биллинг, панели камер.",
        ),
        practices=(
            "Если сервис периодически отдаёт 504 или обрывает длинные действия, это одна из первых опций для проверки.",
        ),
        directives=(
            "proxy_connect_timeout 10s;",
            "proxy_send_timeout 300s;",
            "proxy_read_timeout 300s;",
        ),
        default_enabled=True,
    ),
    ServiceOptionDefinition(
        key="streaming_mode",
        title="Streaming / SSE mode",
        description="Режим для SSE, long-poll, хвостов логов и живых обновлений.",
        summary="Комплексный флаг для сервисов, которым нужен по-настоящему потоковый ответ без задержки на буферизацию: серверные события, long-poll, live-feed.",
        contexts="443 · location",
        category="Realtime",
        recommendation=RECOMMENDATION_MEDIUM,
        when_to_use="SSE, live-логи, long-poll, heartbeat-потоки, потоковые JSON-ответы.",
        when_not_to_use="Обычные страницы и API без длительного удержания соединения.",
        dependencies=("proxy_buffering_off", "proxy_request_buffering_off", "long_timeouts", "sse_response_headers"),
        examples=(
            "Операторские панели присутствия, live-ленты событий, browser-event-stream интерфейсы.",
        ),
        practices=(
            "Включайте этот режим как профиль, а не по частям, если downstream действительно потоковый.",
            "После включения проверяйте, что данные доходят в браузер постепенно, а не пачками.",
        ),
        directives=(
            "proxy_buffering off;",
            "proxy_request_buffering off;",
            'add_header X-Accel-Buffering "no" always;',
            'add_header Cache-Control "no-store" always;',
        ),
    ),
    ServiceOptionDefinition(
        key="proxy_buffering_off",
        title="proxy_buffering off",
        description="Отключает буферизацию ответов nginx.",
        summary="Нужна, когда ответы должны уходить к клиенту сразу, а не копиться внутри nginx до завершения буфера.",
        contexts="443 · location",
        category="Realtime",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Потоковые ответы, live-логи, SSE, very-chatty API.",
        when_not_to_use="Если downstream обычный и выгоднее оставить нормальную буферизацию ответов.",
        examples=(
            "Live tail логов, потоковые панели состояния, некоторые события камер.",
        ),
        practices=(
            "Обычно включается вместе с `Streaming / SSE mode`, а не как отдельная случайная галочка.",
        ),
        directives=(
            "proxy_buffering off;",
        ),
    ),
    ServiceOptionDefinition(
        key="proxy_request_buffering_off",
        title="proxy_request_buffering off",
        description="Отключает буферизацию тела запроса до отправки в upstream.",
        summary="Полезна для больших загрузок и сценариев, когда запрос должен лететь в backend сразу, а не копиться на стороне nginx.",
        contexts="443 · location",
        category="Совместимость",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Большие загрузки, chunked upload, потоковая передача тела запроса, нестандартные аплоады.",
        when_not_to_use="Если downstream работает штатно и нет проблем с загрузкой или долгими POST-запросами.",
        examples=(
            "Сервисы загрузки файлов, панели резервных копий, API с потоковой отправкой данных.",
        ),
        practices=(
            "Для тяжёлых аплоадов удобно сочетать с `Large upload body` и `Long proxy timeouts`.",
        ),
        directives=(
            "proxy_request_buffering off;",
        ),
    ),
    ServiceOptionDefinition(
        key="sse_response_headers",
        title="SSE / no-cache response headers",
        description="Добавляет заголовки для неблокируемых потоковых ответов.",
        summary="Подсказывает и nginx, и браузеру, что этот ответ лучше не кэшировать и не буферизовать по дороге.",
        contexts="443 · location",
        category="Realtime",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="SSE и другие живые ответы, которые должны обновляться сразу.",
        when_not_to_use="Если сервис отдаёт обычные страницы и файлы, а не потоковый контент.",
        examples=(
            "Server-Sent Events, live-статусы, живые очереди событий.",
        ),
        practices=(
            "Чаще всего живёт вместе со `Streaming / SSE mode`.",
        ),
        directives=(
            'add_header Cache-Control "no-store" always;',
            'add_header X-Accel-Buffering "no" always;',
        ),
    ),
    ServiceOptionDefinition(
        key="large_uploads",
        title="Large upload body",
        description="Увеличивает client_max_body_size для больших файлов.",
        summary="Если сервис принимает бэкапы, видео, дампы, архивы или другие тяжёлые файлы, этой опцией снимается базовое ограничение тела запроса.",
        contexts="443 · server",
        category="Производительность",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Загрузки больше стандартного лимита HUB.",
        when_not_to_use="Если сервис только читает данные и не принимает крупные файлы.",
        examples=(
            "Загрузка бэкапов, импорт больших архивов, видеофайлы, пакеты обновлений.",
        ),
        practices=(
            "После включения обязательно проверяйте и лимиты самого downstream-приложения, а не только nginx.",
        ),
        directives=(
            "client_max_body_size 100m;",
        ),
    ),
    ServiceOptionDefinition(
        key="forward_authorization_header",
        title="Forward Authorization header",
        description="Пробрасывает заголовок Authorization в downstream.",
        summary="Нужна для API и приложений, которые принимают Bearer, Basic или иные схемы авторизации в HTTP-заголовке и должны увидеть их после reverse proxy.",
        contexts="443 · location",
        category="API",
        recommendation=RECOMMENDATION_MEDIUM,
        when_to_use="API-gateway сценарии, REST/GraphQL, токены в заголовке Authorization.",
        when_not_to_use="Если downstream не использует Authorization header и лишний проброс не нужен.",
        examples=(
            "Внутренние API, self-hosted панели с bearer-токенами, developer portals.",
        ),
        practices=(
            "Полезно, когда сервис внешне открывается через HUB, но внутри использует собственный API-token flow.",
        ),
        directives=(
            "proxy_set_header Authorization $http_authorization;",
        ),
    ),
    ServiceOptionDefinition(
        key="omit_forwarded_for",
        title="Clear X-Forwarded-For",
        description="Очищает X-Forwarded-For, если downstream конфликтует с proxy-цепочкой.",
        summary="Некоторые сервисы ломаются, если видят реальную цепочку proxy в `X-Forwarded-For`. Для таких случаев заголовок обнуляется целиком.",
        contexts="443 · location",
        category="Совместимость",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Сервис явно требует пустой `X-Forwarded-For` или перестаёт работать с proxy-цепочкой.",
        when_not_to_use="Если downstream нормально понимает реальный клиентский IP и proxy-цепочку.",
        conflicts=("append_forwarded_for",),
        examples=(
            "Home Assistant в конфигурациях, где доверенные прокси настроены жёстко и приложение отвечает 400.",
        ),
        practices=(
            "Сначала пробуйте стандартный режим; включайте эту опцию только после подтверждённой ошибки совместимости.",
        ),
        directives=(
            'proxy_set_header X-Forwarded-For "";',
        ),
    ),
    ServiceOptionDefinition(
        key="append_forwarded_for",
        title="Append X-Forwarded-For chain",
        description="Передаёт полную proxy-цепочку через `$proxy_add_x_forwarded_for`.",
        summary="Даёт downstream полную историю hops, если приложению важна цепочка источников, а не только текущий IP клиента.",
        contexts="443 · location",
        category="Совместимость",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Логи, ACL или приложение ожидают именно полную цепочку `X-Forwarded-For`.",
        when_not_to_use="Если downstream чувствителен к proxy-цепочке или нужно, наоборот, спрятать её.",
        conflicts=("omit_forwarded_for",),
        examples=(
            "Некоторые API-шлюзы и аудит-системы, которым важен полный путь запроса.",
        ),
        practices=(
            "Никогда не включайте одновременно с `Clear X-Forwarded-For`.",
        ),
        directives=(
            "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        ),
    ),
    ServiceOptionDefinition(
        key="upstream_host_header",
        title="Use upstream host in Host header",
        description="Подменяет `Host` на адрес upstream, а не внешний публичный домен.",
        summary="Некоторые старые приложения ожидают увидеть в `Host` именно внутренний адрес или DNS upstream, а не внешний домен HUB.",
        contexts="443 · location",
        category="Совместимость",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Сервис строит ссылки, редиректы или ACL строго от внутреннего host header.",
        when_not_to_use="Если приложению нужен внешний публичный домен или оно корректно работает с `$host`.",
        examples=(
            "Старые админки, легаси-панели и нестандартные веб-интерфейсы за внутренним IP.",
        ),
        practices=(
            "После включения проверяйте login form, редиректы, absolute URLs и callback-ссылки.",
        ),
        directives=(
            "proxy_set_header Host <upstream-host>;",
        ),
    ),
    ServiceOptionDefinition(
        key="proxy_redirect_off",
        title="proxy_redirect off",
        description="Отключает автоматическую правку Location/Refresh заголовков nginx.",
        summary="Полезно, когда downstream сам отдаёт нужные заголовки и стандартная подмена nginx делает редиректы странными или цикличными.",
        contexts="443 · location",
        category="Совместимость",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Подозрительные редиректы, кривые absolute URL, legacy веб-приложения.",
        when_not_to_use="Если текущая схема редиректов уже работает корректно.",
        examples=(
            "Старые панели, которые сами жёстко строят Location-ответы.",
        ),
        practices=(
            "После включения проверяйте логин, logout, редиректы после submit и переходы по вложенным разделам.",
        ),
        directives=(
            "proxy_redirect off;",
        ),
    ),
    ServiceOptionDefinition(
        key="disable_next_upstream",
        title="Disable proxy_next_upstream retries",
        description="Запрещает nginx повторять запросы к upstream по внутренним retry-правилам.",
        summary="Нужна, когда повторный запрос может навредить: например, у легаси-приложения есть чувствительные POST-операции и лишние retry нежелательны.",
        contexts="443 · location",
        category="Надёжность",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Чувствительные POST/PUT операции, легаси backend, строгий контроль повторных запросов.",
        when_not_to_use="Если стандартное поведение без retry-проблем вас устраивает.",
        examples=(
            "Формы оплаты, редкие управляющие API, внутренние операции изменения состояния.",
        ),
        practices=(
            "Включайте после проверки, что downstream сам корректно обрабатывает единичные неуспехи без повторов на стороне nginx.",
        ),
        directives=(
            "proxy_next_upstream off;",
        ),
    ),
    ServiceOptionDefinition(
        key="insecure_upstream_tls",
        title="Disable upstream TLS verify",
        description="Отключает проверку TLS-сертификата upstream.",
        summary="Нужна только для внутренних HTTPS-сервисов с self-signed или некорректной локальной PKI, когда сертификат нельзя штатно провалидировать на стороне nginx.",
        contexts="443 · location",
        category="TLS upstream",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="HTTPS upstream с self-signed сертификатом или локальной CA, которой нет у nginx.",
        when_not_to_use="Когда у upstream уже нормальный валидный сертификат или есть возможность доверить локальную CA корректно.",
        examples=(
            "Frigate или другие локальные панели, поднятые на HTTPS без публичной цепочки доверия.",
        ),
        practices=(
            "Считайте эту опцию исключением. Если можно выдать нормальный сертификат или доверить CA, лучше сделать это.",
        ),
        directives=(
            "proxy_ssl_verify off;",
        ),
    ),
    ServiceOptionDefinition(
        key="tls13_upstream_only",
        title="TLSv1.3 only for upstream",
        description="Разрешает nginx к upstream только TLSv1.3.",
        summary="Используется, когда внутренний HTTPS upstream принимает только TLSv1.3 и отказывается говорить на TLSv1.2.",
        contexts="443 · location",
        category="TLS upstream",
        recommendation=RECOMMENDATION_LOW,
        when_to_use="Upstream реально требует только TLSv1.3.",
        when_not_to_use="Если upstream нормально работает на стандартном наборе TLS-протоколов.",
        examples=(
            "Некоторые современные self-hosted панели с жёстко ограниченным набором шифров/протоколов.",
        ),
        practices=(
            "Перед включением подтверждайте это через `openssl s_client` или тестовый `curl`, а не наугад.",
        ),
        directives=(
            "proxy_ssl_protocols TLSv1.3;",
        ),
    ),
    ServiceOptionDefinition(
        key="path_prefix_proxy",
        title="Nested path prefix routing",
        description="Автоматически строит отдельные `location` для upstream, опубликованного из вложенного пути.",
        summary="Если upstream сидит не в корне, а например на `/v2/`, портал строит корректный набор location-блоков и проксирует префикс без ручного конфига.",
        contexts="443 · location",
        category="Маршрутизация",
        recommendation=RECOMMENDATION_HIGH,
        when_to_use="Срабатывает автоматически, когда в upstream URL есть вложенный путь.",
        when_not_to_use="Не требует ручного включения и не должна использоваться отдельно.",
        examples=(
            "Внутреннее приложение на `http://10.20.30.40/app/`.",
        ),
        practices=(
            "Для сервисов на подкаталоге сначала настраивайте правильный upstream URL; опция включится системой сама.",
        ),
        directives=(
            "location / { proxy_pass http://upstream/prefix/; }",
            "location = /prefix { return 302 /prefix/; }",
            "location ^~ /prefix/ { proxy_pass http://upstream; }",
        ),
        locked=True,
        auto_when_path_prefix=True,
    ),
)


SERVICE_PROFILE_DEFINITIONS: tuple[ServiceProfileDefinition, ...] = (
    ServiceProfileDefinition(
        key="standard_web",
        title="Стандартный web-сервис",
        description="Базовый профиль для обычной внутренней панели, админки или SPA без специальных странностей.",
        when_to_use="Начинайте с него, если не уверены, что приложению нужны дополнительные режимы.",
        recommendation=RECOMMENDATION_HIGH,
        features=("security_headers", "websocket_support", "long_timeouts"),
        examples=("CRM, NetBox, большинство внутренних веб-интерфейсов.",),
    ),
    ServiceProfileDefinition(
        key="live_dashboard",
        title="Realtime / dashboard",
        description="Профиль для панелей с живыми обновлениями, WebSocket и медленными backend-ответами.",
        when_to_use="Мониторинг, операционные панели, уведомления, интерфейсы с live-state.",
        recommendation=RECOMMENDATION_MEDIUM,
        features=("security_headers", "websocket_support", "long_timeouts", "proxy_buffering_off"),
        examples=("Frigate, Home Assistant, live-панели мониторинга.",),
    ),
    ServiceProfileDefinition(
        key="sse_longpoll",
        title="SSE / long-poll",
        description="Профиль для сервисов, которым нужен именно потоковый HTTP-ответ без буферизации.",
        when_to_use="SSE, long-poll, live-ленты событий, хвосты логов, presence/heartbeat UI.",
        recommendation=RECOMMENDATION_MEDIUM,
        features=("security_headers", "long_timeouts", "streaming_mode"),
        caution="Этот профиль осознанно убирает лишние буферы и может менять характер сетевого обмена.",
        examples=("Операторские live-события, event stream endpoints, потоковые панели.",),
    ),
    ServiceProfileDefinition(
        key="file_exchange",
        title="Файлы и крупные загрузки",
        description="Профиль для сервисов, принимающих большие файлы или долгие POST-запросы.",
        when_to_use="Импорт, резервные копии, архивы, видео, обновления, медленные загрузки.",
        recommendation=RECOMMENDATION_MEDIUM,
        features=("security_headers", "long_timeouts", "large_uploads", "proxy_request_buffering_off"),
        examples=("Панели резервного копирования, загрузка архивов, обмен большими файлами.",),
    ),
    ServiceProfileDefinition(
        key="legacy_proxy_compat",
        title="Легаси за reverse proxy",
        description="Профиль совместимости для старых приложений, которые плохо переносят стандартные proxy-заголовки и редиректы.",
        when_to_use="Странные 400, кривые редиректы, жёсткая привязка к внутреннему host header.",
        recommendation=RECOMMENDATION_LOW,
        features=("security_headers", "long_timeouts", "websocket_support", "omit_forwarded_for", "upstream_host_header", "proxy_redirect_off"),
        caution="Используйте после подтверждённой проблемы совместимости, а не как базовый шаблон.",
        examples=("Локальные legacy-панели, старые админки, сервисы со строгим доверием к proxy.",),
    ),
    ServiceProfileDefinition(
        key="https_self_signed",
        title="Внутренний HTTPS с self-signed",
        description="Профиль для HTTPS upstream, у которого сертификат невалиден для nginx.",
        when_to_use="Внутренний сервис уже работает на HTTPS, но сертификат self-signed или локальный.",
        recommendation=RECOMMENDATION_LOW,
        features=("security_headers", "websocket_support", "long_timeouts", "insecure_upstream_tls"),
        caution="Это компромисс совместимости. При возможности лучше доверить нормальную CA и убрать этот флаг.",
        examples=("Локальные панели на HTTPS без публичной цепочки доверия.",),
    ),
    ServiceProfileDefinition(
        key="https_tls13_only",
        title="HTTPS upstream только на TLSv1.3",
        description="Профиль для редких upstream, которые принимают только TLSv1.3.",
        when_to_use="Когда тесты явно показывают отказ upstream на обычном TLS-наборе и успешную работу только на TLSv1.3.",
        recommendation=RECOMMENDATION_LOW,
        features=("security_headers", "websocket_support", "long_timeouts", "tls13_upstream_only"),
        caution="Перед включением подтверждайте поведение upstream отдельной сетевой проверкой.",
        examples=("Некоторые современные локальные панели и self-hosted приложения.",),
    ),
)


SERVICE_OPTION_MAP = {item.key: item for item in SERVICE_OPTION_DEFINITIONS}
SERVICE_OPTION_ORDER = {item.key: index for index, item in enumerate(SERVICE_OPTION_DEFINITIONS)}


def _resolve_titles(keys: Iterable[str]) -> list[str]:
    return [SERVICE_OPTION_MAP[key].title for key in keys if key in SERVICE_OPTION_MAP]


def _build_option_payload(definition: ServiceOptionDefinition) -> dict:
    return {
        "key": definition.key,
        "title": definition.title,
        "description": definition.description,
        "summary": definition.summary,
        "contexts": definition.contexts,
        "category": definition.category,
        "recommendation": definition.recommendation,
        "recommendation_label": RECOMMENDATION_LABELS[definition.recommendation],
        "recommendation_note": RECOMMENDATION_NOTES[definition.recommendation],
        "when_to_use": definition.when_to_use,
        "when_not_to_use": definition.when_not_to_use,
        "dependencies": list(definition.dependencies),
        "dependencies_titles": _resolve_titles(definition.dependencies),
        "conflicts": list(definition.conflicts),
        "conflicts_titles": _resolve_titles(definition.conflicts),
        "examples": list(definition.examples),
        "practices": list(definition.practices),
        "directives": list(definition.directives),
        "locked": definition.locked,
        "default_enabled": definition.default_enabled,
        "auto_when_path_prefix": definition.auto_when_path_prefix,
    }


def get_service_option_catalog() -> dict[str, dict]:
    return {item.key: _build_option_payload(item) for item in SERVICE_OPTION_DEFINITIONS}


def get_service_option_choices() -> list[tuple[str, str]]:
    return [(item.key, item.title) for item in SERVICE_OPTION_DEFINITIONS if not item.locked]


def normalize_service_features(
    feature_keys: Iterable[str] | None,
    *,
    upstream_url: str = "",
) -> list[str]:
    requested = {key for key in (feature_keys or ()) if key in SERVICE_OPTION_MAP}
    if not requested:
        requested.update(DEFAULT_SERVICE_FEATURES)

    resolved = set(requested)
    changed = True
    while changed:
        changed = False
        for key in tuple(resolved):
            definition = SERVICE_OPTION_MAP.get(key)
            if not definition:
                continue
            for dependency in definition.dependencies:
                if dependency not in resolved:
                    resolved.add(dependency)
                    changed = True

    for definition in SERVICE_OPTION_DEFINITIONS:
        if definition.locked and definition.default_enabled:
            resolved.add(definition.key)

    path_prefix = (urlparse(upstream_url).path or "").strip()
    if path_prefix and path_prefix != "/":
        resolved.add("path_prefix_proxy")

    for definition in SERVICE_OPTION_DEFINITIONS:
        if definition.key not in resolved:
            continue
        for conflict in definition.conflicts:
            if conflict in resolved:
                resolved.discard(conflict)

    return sorted(resolved, key=lambda item: SERVICE_OPTION_ORDER.get(item, 999))


def get_service_option_rows(
    feature_keys: Iterable[str] | None,
    *,
    upstream_url: str = "",
) -> list[dict]:
    active_keys = set(normalize_service_features(feature_keys, upstream_url=upstream_url))
    rows: list[dict] = []
    for definition in SERVICE_OPTION_DEFINITIONS:
        row = _build_option_payload(definition)
        row.update(
            {
                "active": definition.key in active_keys,
                "auto": definition.auto_when_path_prefix and definition.key in active_keys,
            }
        )
        rows.append(row)
    return rows


def get_service_profile_rows() -> list[dict]:
    rows: list[dict] = []
    for definition in SERVICE_PROFILE_DEFINITIONS:
        feature_keys = normalize_service_features(definition.features, upstream_url="")
        rows.append(
            {
                "key": definition.key,
                "title": definition.title,
                "description": definition.description,
                "when_to_use": definition.when_to_use,
                "recommendation": definition.recommendation,
                "recommendation_label": RECOMMENDATION_LABELS[definition.recommendation],
                "recommendation_note": RECOMMENDATION_NOTES[definition.recommendation],
                "feature_keys": feature_keys,
                "feature_titles": _resolve_titles(feature_keys),
                "caution": definition.caution,
                "examples": list(definition.examples),
            }
        )
    return rows
