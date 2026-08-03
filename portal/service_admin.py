from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from urllib.parse import urlparse

from django.conf import settings

from .models import Service, ServiceRevision
from .service_options import normalize_service_features


MANAGED_SNAPSHOT_FIELDS = (
    "title",
    "slug",
    "description",
    "kind",
    "domain",
    "upstream_url",
    "external_url",
    "precheck_url",
    "icon_key",
    "check_type",
    "health_host",
    "health_port",
    "health_path",
    "timeout_seconds",
    "allowed_status_codes",
    "interval_seconds",
    "failure_threshold",
    "success_threshold",
    "cert_fullchain_path",
    "cert_privkey_path",
    "config_mode",
    "managed_features",
    "manual_config",
    "sort_order",
    "is_enabled",
    "is_active",
)


@dataclass
class ServiceOperationResult:
    ok: bool
    message: str
    output: str = ""
    details: dict = field(default_factory=dict)


def get_runtime_dir() -> Path:
    return Path(
        getattr(
            settings,
            "HUB_PORTAL_SERVICE_ADMIN_RUNTIME_DIR",
            settings.BASE_DIR / ".service-admin",
        )
    )


def get_staging_dir() -> Path:
    path = get_runtime_dir() / "staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_site_available_path(domain: str) -> Path:
    return Path(
        getattr(
            settings,
            "HUB_PORTAL_SERVICE_SITE_AVAILABLE_DIR",
            "/etc/nginx/sites-available",
        )
    ) / domain


def get_site_enabled_path(domain: str) -> Path:
    return Path(
        getattr(
            settings,
            "HUB_PORTAL_SERVICE_SITE_ENABLED_DIR",
            "/etc/nginx/sites-enabled",
        )
    ) / domain


def get_helper_path() -> Path:
    return Path(
        getattr(
            settings,
            "HUB_PORTAL_SERVICE_ADMIN_HELPER",
            "/usr/local/bin/hub-portal-service-admin",
        )
    )


def read_live_service_config(domain: str) -> str:
    path = get_site_available_path(domain)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def get_effective_manual_config(service: Service) -> str:
    manual_text = (service.manual_config or "").strip()
    if manual_text:
        return manual_text
    live_text = read_live_service_config(service.domain)
    if live_text.strip():
        return live_text
    return render_managed_service_config(service)


def write_staged_config(domain: str, config_text: str) -> Path:
    path = get_staging_dir() / f"{domain}.conf"
    path.write_text(config_text.strip() + "\n", encoding="utf-8")
    return path


def build_service_snapshot(service: Service) -> dict:
    snapshot: dict = {}
    for field_name in MANAGED_SNAPSHOT_FIELDS:
        value = getattr(service, field_name)
        if isinstance(value, list):
            snapshot[field_name] = list(value)
        else:
            snapshot[field_name] = value
    return snapshot


def apply_service_snapshot(service: Service, snapshot: dict) -> Service:
    for field_name in MANAGED_SNAPSHOT_FIELDS:
        if field_name in snapshot:
            setattr(service, field_name, snapshot[field_name])
    return service


def create_service_revision(
    *,
    service: Service | None,
    actor=None,
    revision_kind: str,
    summary: str,
    rendered_config: str,
    validated_ok: bool,
    snapshot: dict | None = None,
) -> ServiceRevision:
    resolved_snapshot = snapshot or (build_service_snapshot(service) if service else {})
    return ServiceRevision.objects.create(
        service=service,
        service_title_snapshot=resolved_snapshot.get("title", getattr(service, "title", "")),
        service_domain_snapshot=resolved_snapshot.get("domain", getattr(service, "domain", "")) or "",
        created_by=actor if getattr(actor, "pk", None) else None,
        revision_kind=revision_kind,
        summary=summary,
        config_mode=resolved_snapshot.get("config_mode", getattr(service, "config_mode", "managed")),
        rendered_config=rendered_config,
        snapshot=resolved_snapshot,
        validated_ok=validated_ok,
    )


def invoke_service_helper(*args: str) -> ServiceOperationResult:
    helper_path = get_helper_path()
    if not helper_path.exists():
        return ServiceOperationResult(
            ok=False,
            message=f"Server helper не найден: {helper_path}",
        )

    command = ["sudo", "-n", str(helper_path), *args]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
    if completed.returncode != 0:
        return ServiceOperationResult(
            ok=False,
            message=output or "Серверная операция завершилась ошибкой.",
            output=output,
            details={"returncode": completed.returncode},
        )
    return ServiceOperationResult(
        ok=True,
        message=output or "Операция выполнена.",
        output=output,
    )


def parse_upstream_components(upstream_url: str) -> tuple[str, str, str]:
    parsed = urlparse(upstream_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    raw_path = parsed.path or ""
    if raw_path and raw_path != "/":
        prefix = raw_path.rstrip("/")
        proxy_root = f"{base}{prefix}/"
    else:
        prefix = ""
        proxy_root = upstream_url
    return base, prefix, proxy_root


def render_proxy_location(service: Service) -> str:
    base, path_prefix, proxy_root = parse_upstream_components(service.upstream_url)
    features = set(normalize_service_features(service.managed_features, upstream_url=service.upstream_url))
    ssl_extra_lines: list[str] = []
    parsed = urlparse(service.upstream_url)
    if parsed.scheme == "https":
        ssl_extra_lines.append("proxy_ssl_server_name on;")
        if "tls13_upstream_only" in features:
            ssl_extra_lines.append("proxy_ssl_protocols TLSv1.3;")
        if "insecure_upstream_tls" in features:
            ssl_extra_lines.append("proxy_ssl_verify off;")
    host_header_value = "$host"
    if "upstream_host_header" in features:
        host_header_value = parsed.netloc or parsed.hostname or "$host"

    forwarded_for_line = 'proxy_set_header X-Forwarded-For $remote_addr;'
    if "append_forwarded_for" in features:
        forwarded_for_line = "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;"
    if "omit_forwarded_for" in features:
        forwarded_for_line = 'proxy_set_header X-Forwarded-For "";'

    timeout_seconds = 300 if "long_timeouts" in features or "streaming_mode" in features else 60
    connect_timeout = 10 if "long_timeouts" in features or "streaming_mode" in features else 5
    buffering_line = "proxy_buffering off;" if "proxy_buffering_off" in features or "streaming_mode" in features else "proxy_buffering on;"
    request_buffering_line = (
        "proxy_request_buffering off;"
        if "proxy_request_buffering_off" in features or "streaming_mode" in features
        else "proxy_request_buffering on;"
    )
    redirect_line = "proxy_redirect off;" if "proxy_redirect_off" in features else ""
    next_upstream_line = "proxy_next_upstream off;" if "disable_next_upstream" in features else ""
    upgrade_lines: list[str] = []
    if "websocket_support" in features:
        upgrade_lines = [
            "proxy_set_header Upgrade $http_upgrade;",
            "proxy_set_header Connection $connection_upgrade;",
        ]
    extra_header_lines: list[str] = []
    if "sse_response_headers" in features or "streaming_mode" in features:
        extra_header_lines = [
            'add_header Cache-Control "no-store" always;',
            'add_header X-Accel-Buffering "no" always;',
        ]
    authorization_line = (
        "proxy_set_header Authorization $http_authorization;"
        if "forward_authorization_header" in features
        else ""
    )

    common_lines = [
        "auth_request /_portal_auth;",
        "error_page 401 = /_portal_login;",
        "error_page 403 = /_portal_forbidden;",
        "",
        "proxy_http_version 1.1;",
        f"proxy_set_header Host {host_header_value};",
        "proxy_set_header Cookie $hub_upstream_cookie;",
        "proxy_set_header X-Real-IP $remote_addr;",
        forwarded_for_line,
        "proxy_set_header X-Forwarded-Proto https;",
        "proxy_set_header X-Forwarded-Host $host;",
        "proxy_set_header X-Forwarded-Port 443;",
        authorization_line,
        *upgrade_lines,
        *ssl_extra_lines,
        buffering_line,
        request_buffering_line,
        redirect_line,
        next_upstream_line,
        *extra_header_lines,
        f"proxy_connect_timeout {connect_timeout}s;",
        f"proxy_send_timeout {timeout_seconds}s;",
        f"proxy_read_timeout {timeout_seconds}s;",
    ]

    def build_location(path: str, proxy_pass: str, *, prefix: bool = False) -> str:
        marker = "^~ " if prefix else ""
        body = [f"location {marker}{path} {{", f"    proxy_pass {proxy_pass};"]
        for line in common_lines:
            if line:
                body.append(f"    {line}")
            else:
                body.append("")
        body.append("}")
        return "\n".join(body)

    if path_prefix:
        return "\n\n".join(
            [
                build_location("/", proxy_root),
                f"location = {path_prefix} {{\n    return 302 {path_prefix}/;\n}}",
                build_location(f"{path_prefix}/", base, prefix=True),
            ]
        )
    return build_location("/", service.upstream_url)


def render_managed_service_config(service: Service) -> str:
    if service.is_external_link:
        return ""
    features = set(normalize_service_features(service.managed_features, upstream_url=service.upstream_url))
    client_max_body_size = "100m" if "large_uploads" in features else "20m"
    security_headers_include = (
        "    include /etc/nginx/snippets/hub-security-headers.conf;\n"
        if "security_headers" in features
        else ""
    )
    internal_auth_key = settings.HUB_PORTAL_INTERNAL_AUTH_KEY
    return (
        f"server {{\n"
        f"    listen 80;\n"
        f"    listen [::]:80;\n"
        f"    server_name {service.domain};\n\n"
        f"    location /.well-known/acme-challenge/ {{\n"
        f"        root /var/www/hub-portal/acme;\n"
        f"    }}\n\n"
        f"    location / {{\n"
        f"        return 301 https://$host$request_uri;\n"
        f"    }}\n"
        f"}}\n\n"
        f"server {{\n"
        f"    listen 443 ssl http2;\n"
        f"    listen [::]:443 ssl http2;\n"
        f"    server_name {service.domain};\n\n"
        f"    ssl_certificate {service.cert_fullchain_path};\n"
        f"    ssl_certificate_key {service.cert_privkey_path};\n"
        f"    include /etc/nginx/snippets/hub-tls.conf;\n\n"
        f"    include /etc/nginx/hub-log-mode/services/{service.domain}.conf;\n\n"
        f"    client_max_body_size {client_max_body_size};\n"
        f"{security_headers_include}"
        f"    location = /_portal_auth {{\n"
        f"        internal;\n"
        f"        proxy_pass http://hub_portal_app/internal/authz;\n"
        f"        proxy_pass_request_body off;\n"
        f"        proxy_pass_request_headers off;\n"
        f"        proxy_set_header Content-Length \"\";\n"
        f"        proxy_set_header Host {settings.HUB_PORTAL_PUBLIC_HOST};\n"
        f"        proxy_set_header X-Portal-Auth-Key {internal_auth_key};\n"
        f"        proxy_set_header X-Original-Service {service.domain};\n"
        f"        proxy_set_header X-Original-URI $request_uri;\n"
        f"        proxy_set_header X-Original-Method $request_method;\n"
        f"        proxy_set_header Cookie $hub_portal_gate_cookie;\n"
        f"        proxy_set_header X-Real-IP $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-For $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-Proto https;\n"
        f"    }}\n\n"
        f"{render_proxy_location(service)}\n\n"
        f"    location = /_portal_forbidden {{\n"
        f"        internal;\n"
        f"        proxy_pass http://hub_portal_app/forbidden/;\n"
        f"        proxy_pass_request_body off;\n"
        f"        proxy_pass_request_headers off;\n"
        f"        proxy_set_header Content-Length \"\";\n"
        f"        proxy_set_header Host {settings.HUB_PORTAL_PUBLIC_HOST};\n"
        f"        proxy_set_header X-Real-IP $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-For $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-Proto https;\n"
        f"    }}\n\n"
        f"    location = /_portal_login {{\n"
        f"        proxy_pass http://hub_portal_app/internal/login-state;\n"
        f"        proxy_pass_request_body off;\n"
        f"        proxy_pass_request_headers off;\n"
        f"        proxy_set_header Content-Length \"\";\n"
        f"        proxy_set_header Host {settings.HUB_PORTAL_PUBLIC_HOST};\n"
        f"        proxy_set_header X-Portal-Auth-Key {internal_auth_key};\n"
        f"        proxy_set_header X-Original-Service {service.domain};\n"
        f"        proxy_set_header X-Original-URI $request_uri;\n"
        f"        proxy_set_header X-Real-IP $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-For $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-Proto https;\n"
        f"    }}\n"
        f"}}\n"
    )


def render_effective_service_config(service: Service) -> str:
    if service.config_mode == "manual":
        return get_effective_manual_config(service)
    return render_managed_service_config(service)


def validate_service_candidate(service: Service, config_text: str) -> ServiceOperationResult:
    if service.is_external_link:
        return ServiceOperationResult(ok=True, message="Внешняя ссылка не требует nginx-конфига.")
    staged_path = write_staged_config(service.domain, config_text)
    return invoke_service_helper("validate", "--domain", service.domain, "--source", str(staged_path))


def apply_service_candidate(service: Service, config_text: str) -> ServiceOperationResult:
    if service.is_external_link:
        return ServiceOperationResult(ok=True, message="Внешняя ссылка опубликована в каталоге.")
    staged_path = write_staged_config(service.domain, config_text)
    mode_flag = "--enable" if service.is_enabled else "--disable"
    return invoke_service_helper("apply", "--domain", service.domain, "--source", str(staged_path), mode_flag)


def enable_service_candidate(service: Service) -> ServiceOperationResult:
    if service.is_external_link:
        return ServiceOperationResult(ok=True, message="Внешняя ссылка включена.")
    config_text = render_effective_service_config(service)
    staged_path = write_staged_config(service.domain, config_text)
    return invoke_service_helper("apply", "--domain", service.domain, "--source", str(staged_path), "--enable")


def disable_service_candidate(service: Service) -> ServiceOperationResult:
    if service.is_external_link:
        return ServiceOperationResult(ok=True, message="Внешняя ссылка отключена.")
    return invoke_service_helper("disable", "--domain", service.domain)


def delete_service_candidate(service: Service) -> ServiceOperationResult:
    if service.is_external_link:
        return ServiceOperationResult(ok=True, message="Внешняя ссылка удалена.")
    return invoke_service_helper("remove", "--domain", service.domain)


def issue_service_certificate(domain: str) -> ServiceOperationResult:
    result = invoke_service_helper("issue-cert", "--domain", domain)
    if result.ok:
        result.details.update(
            {
                "cert_fullchain_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
                "cert_privkey_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
            }
        )
    return result
