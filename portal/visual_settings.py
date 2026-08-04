from __future__ import annotations

from .models import PortalVisualSettings


def get_portal_visual_settings() -> PortalVisualSettings:
    return PortalVisualSettings.get_solo()


def build_particle_visual_context(*, blocked_page: bool = False) -> dict[str, int]:
    settings_row = get_portal_visual_settings()
    return {
        "login_particle_desktop_count": (
            settings_row.blocked_particles_desktop_count
            if blocked_page
            else settings_row.login_particles_desktop_count
        ),
        "login_particle_mobile_count": (
            settings_row.blocked_particles_mobile_count
            if blocked_page
            else settings_row.login_particles_mobile_count
        ),
        "login_particle_calm_speed_px": int(settings_row.calm_speed_px or 38),
        "login_particle_alert_speed_px": int(settings_row.alert_speed_px or 189),
        "login_particle_invalid_alert_duration_ms": int(settings_row.invalid_alert_duration_seconds or 2) * 1000,
        "login_particle_lock_alert_duration_ms": int(settings_row.lock_alert_duration_seconds or 5) * 1000,
        "login_particle_fade_duration_ms": int(settings_row.alert_fade_duration_seconds or 1) * 1000,
    }
