from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RELEASE_ID = "dev"
_CACHED_RELEASE_ID: str | None = None
_CACHED_RELEASE_FILE_TOKEN: tuple[str, int, int] | None = None


def get_release_id_file() -> Path:
    configured_path = os.environ.get("HUB_PORTAL_RELEASE_ID_FILE", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return BASE_DIR / ".release-id"


def get_release_id_file_token(path: Path) -> tuple[str, int, int] | None:
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), stat_result.st_mtime_ns, stat_result.st_size)


def get_release_id() -> str:
    global _CACHED_RELEASE_ID, _CACHED_RELEASE_FILE_TOKEN

    env_value = os.environ.get("HUB_PORTAL_RELEASE_ID", "").strip()
    if env_value:
        return env_value

    release_id_file = get_release_id_file()
    file_token = get_release_id_file_token(release_id_file)
    if _CACHED_RELEASE_ID and _CACHED_RELEASE_FILE_TOKEN == file_token:
        return _CACHED_RELEASE_ID

    try:
        file_value = release_id_file.read_text(encoding="utf-8").strip()
    except OSError:
        file_value = ""

    _CACHED_RELEASE_ID = file_value or DEFAULT_RELEASE_ID
    _CACHED_RELEASE_FILE_TOKEN = file_token
    return _CACHED_RELEASE_ID
