from __future__ import annotations

from django.http import HttpRequest, HttpResponse

from .audit_maintenance import is_audit_maintenance_active
from .constants import SESSION_AUTH_METHOD_KEY
from .models import AuditEvent, Service


def get_request_auth_method(request: HttpRequest) -> str:
    return request.session.get(SESSION_AUTH_METHOD_KEY, "")


def get_client_ip(request: HttpRequest) -> str:
    real_ip = (request.META.get("HTTP_X_REAL_IP") or "").strip()
    if real_ip:
        return real_ip
    return (request.META.get("REMOTE_ADDR") or "").strip()


def build_target_label(host: str, path: str, fallback: str = "") -> str:
    if host and path:
        return f"{host}{path}"
    if host:
        return host
    if path:
        return path
    return fallback


def log_audit_event(
    request: HttpRequest,
    *,
    user=None,
    service: Service | None = None,
    username_snapshot: str | None = None,
    target_label: str = "",
    auth_method: str = "",
    event_kind: str = "",
    result: str = "",
    host: str = "",
    path: str = "",
    category: str = "",
    action: str = "",
    is_business_event: bool = True,
    http_method: str = "",
    status_code: int | None = None,
    request_id: str = "",
    user_agent: str = "",
    metadata: dict | None = None,
) -> AuditEvent | None:
    if is_audit_maintenance_active():
        return None

    resolved_host = (host or request.headers.get("X-Original-Host") or request.get_host() or "").strip().lower()
    resolved_path = (path or request.headers.get("X-Original-URI") or request.get_full_path() or "").strip()
    resolved_target = target_label or build_target_label(resolved_host, resolved_path, fallback="Портал")
    resolved_username = user.username if user and username_snapshot is None else (username_snapshot or "")
    return AuditEvent.objects.create(
        user=user if getattr(user, "pk", None) else None,
        service=service,
        username_snapshot=resolved_username,
        ip_address=get_client_ip(request) or None,
        target_label=resolved_target,
        is_business_event=is_business_event,
        category=category,
        action=action,
        auth_method=auth_method,
        event_kind=event_kind,
        result=result,
        host=resolved_host,
        path=resolved_path,
        http_method=http_method or request.META.get("REQUEST_METHOD", ""),
        status_code=status_code,
        user_agent=(user_agent or request.META.get("HTTP_USER_AGENT") or "")[:512],
        request_id=request_id or (request.META.get("HTTP_X_REQUEST_ID") or "")[:128],
        metadata=metadata or {},
    )


def apply_no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response
