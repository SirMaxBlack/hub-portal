from __future__ import annotations

import logging
import socket
import ssl
import time
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection

from django.utils import timezone

from .models import Service, ServiceHealthState


logger = logging.getLogger(__name__)

TECHNICAL_PATH_PREFIXES = (
    "/assets/",
    "/static/",
    "/favicon",
    "/manifest",
    "/api/health",
    "/health",
    "/probe",
)
TECHNICAL_SUFFIXES = (
    ".js",
    ".css",
    ".ico",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".webp",
    ".json",
)


@dataclass
class HealthCheckResult:
    success: bool
    error: str = ""
    status_code: int | None = None


def should_log_service_request(path: str) -> bool:
    normalized = (path or "/").lower()
    if normalized.startswith("/api/"):
        return False
    if normalized.startswith(TECHNICAL_PATH_PREFIXES):
        return False
    return not normalized.endswith(TECHNICAL_SUFFIXES)


def get_state(service: Service) -> ServiceHealthState:
    state, _ = ServiceHealthState.objects.get_or_create(service=service)
    return state


def get_effective_status(service: Service, *, state: ServiceHealthState | None = None, now=None) -> str:
    now = now or timezone.now()
    if service.is_external_link:
        return "up"
    if not getattr(service, "is_enabled", True):
        return "down"
    state = state or getattr(service, "health_state", None)
    if state is None:
        return "unknown"
    if not state.last_checked_at:
        return "unknown"
    max_age = max(service.interval_seconds * 3, 15)
    if (now - state.last_checked_at).total_seconds() > max_age:
        return "unknown"
    return state.status


def build_http_connection(service: Service):
    timeout = service.timeout_seconds
    if service.check_type == "https":
        return HTTPSConnection(
            service.health_host,
            service.health_port,
            timeout=timeout,
            context=ssl._create_unverified_context(),
        )
    return HTTPConnection(service.health_host, service.health_port, timeout=timeout)


def perform_health_check(service: Service) -> HealthCheckResult:
    if service.is_external_link:
        return HealthCheckResult(success=True)
    if service.check_type == "tcp":
        try:
            with socket.create_connection((service.health_host, service.health_port), timeout=service.timeout_seconds):
                return HealthCheckResult(success=True)
        except OSError as exc:
            return HealthCheckResult(success=False, error=str(exc))

    connection = build_http_connection(service)
    try:
        connection.request("GET", service.health_path or "/")
        response = connection.getresponse()
        allowed_codes = set(service.get_allowed_status_codes())
        success = response.status in allowed_codes
        return HealthCheckResult(
            success=success,
            error="" if success else f"HTTP {response.status}",
            status_code=response.status,
        )
    except OSError as exc:
        return HealthCheckResult(success=False, error=str(exc))
    finally:
        connection.close()


def update_service_health(service: Service, *, now=None) -> ServiceHealthState:
    now = now or timezone.now()
    state = get_state(service)
    result = perform_health_check(service)
    state.last_checked_at = now

    previous_status = state.status
    if result.success:
        state.consecutive_successes += 1
        state.consecutive_failures = 0
        state.last_success_at = now
        state.last_error = ""
        if state.consecutive_successes >= service.success_threshold:
            state.status = "up"
    else:
        state.consecutive_failures += 1
        state.consecutive_successes = 0
        state.last_failure_at = now
        state.last_error = result.error[:1000]
        if state.consecutive_failures >= service.failure_threshold:
            state.status = "down"

    if previous_status != state.status:
        logger.warning(
            "Service health changed for %s: %s -> %s (%s)",
            service.domain,
            previous_status,
            state.status,
            result.error or result.status_code or "ok",
        )

    state.save()
    return state


def update_all_service_health() -> None:
    for service in Service.objects.filter(is_active=True, kind="proxy").order_by("sort_order", "title"):
        update_service_health(service)
        time.sleep(0.05)
