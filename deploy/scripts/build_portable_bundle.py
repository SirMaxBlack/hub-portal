from __future__ import annotations

import argparse
import hashlib
import shutil
import tarfile
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = SOURCE_ROOT / "dist"

FILES_TO_COPY = (
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "VERSION",
    "install.sh",
    "manage.py",
    "requirements.txt",
)

DIRECTORIES_TO_COPY = (
    ".github",
    "deploy",
    "docs",
    "hub_portal",
    "portal",
)

IGNORED_NAMES = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "dist",
    "runtime",
    "staticfiles",
}


def ignore_runtime(_directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in IGNORED_NAMES}
    ignored.update(name for name in names if name.endswith((".pyc", ".pyo", ".token")))
    ignored.update(name for name in names if name in {"db.sqlite3", ".release-id"})
    return ignored


def copy_release_tree(target_root: Path) -> None:
    for relative_path in FILES_TO_COPY:
        source = SOURCE_ROOT / relative_path
        if not source.is_file():
            raise FileNotFoundError(f"Required release file is missing: {relative_path}")
        destination = target_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    for relative_path in DIRECTORIES_TO_COPY:
        source = SOURCE_ROOT / relative_path
        if not source.is_dir():
            raise FileNotFoundError(f"Required release directory is missing: {relative_path}")
        shutil.copytree(source, target_root / relative_path, ignore=ignore_runtime)

    executable_paths = [target_root / "install.sh", target_root / "deploy/scripts/hubctl"]
    executable_paths.extend((target_root / "deploy/scripts").glob("*.sh"))
    for executable_path in executable_paths:
        executable_path.chmod(0o755)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_tar_metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
    normalized_name = member.name.replace("\\", "/")
    is_executable = normalized_name.endswith("/install.sh") or (
        "/deploy/scripts/" in normalized_name
        and (normalized_name.endswith(".sh") or normalized_name.endswith("/hubctl"))
    )
    if member.isdir() or is_executable:
        member.mode = 0o755
    elif member.isfile():
        member.mode = 0o644
    member.uid = 0
    member.gid = 0
    member.uname = "root"
    member.gname = "root"
    return member


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a portable HUB Portal release archive.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    version = (SOURCE_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError("VERSION is empty")

    output_root = args.output.resolve()
    release_name = f"hub-portal-{version}"
    release_root = output_root / release_name
    archive_path = output_root / f"{release_name}.tar.gz"
    checksum_path = Path(f"{archive_path}.sha256")

    output_root.mkdir(parents=True, exist_ok=True)
    if release_root.exists():
        shutil.rmtree(release_root)
    archive_path.unlink(missing_ok=True)
    checksum_path.unlink(missing_ok=True)

    release_root.mkdir()
    copy_release_tree(release_root)
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(release_root, arcname=release_name, filter=normalize_tar_metadata)

    checksum_path.write_text(
        f"{sha256(archive_path)}  {archive_path.name}\n",
        encoding="ascii",
    )
    print(archive_path)
    print(checksum_path)


if __name__ == "__main__":
    main()
