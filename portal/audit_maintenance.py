from __future__ import annotations

import errno
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings


class AuditMaintenanceError(RuntimeError):
    pass


class AuditMaintenanceBusyError(AuditMaintenanceError):
    pass


def get_audit_maintenance_lock_path() -> Path:
    return Path(settings.HUB_PORTAL_AUDIT_MAINTENANCE_LOCK)


def is_audit_maintenance_active() -> bool:
    lock_path = get_audit_maintenance_lock_path()
    if not lock_path.exists():
        return False

    try:
        raw_value = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return True

    if not raw_value:
        return True

    try:
        pid = int(raw_value.splitlines()[0].strip())
    except (TypeError, ValueError):
        return True

    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


def _parse_helper_payload(*streams: str) -> dict:
    for stream in streams:
        for candidate in reversed([line.strip() for line in stream.splitlines() if line.strip()]):
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return {}


def clear_audit_trail() -> dict:
    helper_path = settings.HUB_PORTAL_CLEAR_AUDIT_HELPER
    try:
        completed = subprocess.run(
            ["sudo", "-n", helper_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise AuditMaintenanceError("Очистка журнала превысила допустимое время.") from exc
    except OSError as exc:
        raise AuditMaintenanceError("Не удалось запустить серверную очистку журнала.") from exc

    payload = _parse_helper_payload(completed.stdout, completed.stderr)
    message = (payload.get("message") or completed.stderr or completed.stdout or "").strip()

    if completed.returncode == 0:
        if not payload:
            raise AuditMaintenanceError("Сервер очистки вернул пустой ответ.")
        return payload
    if completed.returncode == 3:
        raise AuditMaintenanceBusyError(message or "Очистка уже выполняется.")
    raise AuditMaintenanceError(message or "Не удалось очистить журнал.")
