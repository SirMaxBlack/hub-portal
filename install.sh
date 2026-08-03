#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="${HUB_PORTAL_REPOSITORY:-SirMaxBlack/hub-portal}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/manage.py" && -x "$SCRIPT_DIR/deploy/scripts/install.sh" ]]; then
    exec "$SCRIPT_DIR/deploy/scripts/install.sh" --source "$SCRIPT_DIR" "$@"
fi

command -v curl >/dev/null 2>&1 || { echo "curl не установлен." >&2; exit 127; }
command -v tar >/dev/null 2>&1 || { echo "tar не установлен." >&2; exit 127; }
if ! command -v python3 >/dev/null 2>&1; then
    [[ $EUID -eq 0 ]] || { echo "Для bootstrap нужен python3 или запуск через sudo." >&2; exit 127; }
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
auth_args=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    auth_args=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi

requested_version="${HUB_PORTAL_VERSION:-latest}"
if [[ "$requested_version" == "latest" ]]; then
    release_json="$(curl -fsSL "${auth_args[@]}" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPOSITORY/releases?per_page=20" \
        | python3 -c 'import json,sys; releases=json.load(sys.stdin); print(json.dumps(next((item for item in releases if not item.get("draft")), {})))')"
else
    release_json="$(curl -fsSL "${auth_args[@]}" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPOSITORY/releases/tags/$requested_version")"
fi
read -r tag archive_url checksum_url < <(
    python3 -c 'import json,sys; p=json.load(sys.stdin); a=p.get("assets",[]); arc=next((x["url"] for x in a if x["name"].endswith(".tar.gz")), ""); sha=next((x["url"] for x in a if x["name"].endswith(".sha256")), ""); print(p.get("tag_name", ""), arc, sha)' <<<"$release_json"
)
[[ -n "$tag" && -n "$archive_url" && -n "$checksum_url" ]] || {
    echo "В последнем релизе отсутствует установочный архив или checksum." >&2
    exit 2
}

curl -fsSL "${auth_args[@]}" -H "Accept: application/octet-stream" -o "$tmp_dir/hub-portal.tar.gz" "$archive_url"
curl -fsSL "${auth_args[@]}" -H "Accept: application/octet-stream" -o "$tmp_dir/hub-portal.tar.gz.sha256" "$checksum_url"
(cd "$tmp_dir" && awk '{print $1 "  hub-portal.tar.gz"}' hub-portal.tar.gz.sha256 | sha256sum -c -)
mkdir -p "$tmp_dir/source"
tar -xzf "$tmp_dir/hub-portal.tar.gz" -C "$tmp_dir/source" --strip-components=1
exec "$tmp_dir/source/deploy/scripts/install.sh" --source "$tmp_dir/source" "$@"
