from __future__ import annotations

import shutil
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]
TARGET_ROOT = SOURCE_ROOT.parent / "hub_portal_portable"

FILES_TO_COPY = [
    "manage.py",
    "requirements.txt",
    "PORTABLE_DEPLOY.md",
    ".release-id",
]

DIRECTORIES_TO_COPY = [
    "hub_portal",
    "portal",
    "deploy",
    "staticfiles",
]

OPTIONAL_DIRECTORIES = [
    "templates",
]


def copy_file(relative_path: str) -> None:
    source = SOURCE_ROOT / relative_path
    if not source.exists():
        return
    destination = TARGET_ROOT / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_directory(relative_path: str) -> None:
    source = SOURCE_ROOT / relative_path
    if not source.exists():
        return
    destination = TARGET_ROOT / relative_path
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "*.pyo",
            ".venv",
            "db.sqlite3",
            ".qa",
            ".tmp-*",
        ),
    )


def main() -> None:
    if TARGET_ROOT.exists():
        shutil.rmtree(TARGET_ROOT)
    TARGET_ROOT.mkdir(parents=True, exist_ok=True)

    for file_path in FILES_TO_COPY:
        copy_file(file_path)

    for directory in DIRECTORIES_TO_COPY:
        copy_directory(directory)

    for directory in OPTIONAL_DIRECTORIES:
        copy_directory(directory)

    print(TARGET_ROOT)


if __name__ == "__main__":
    main()
