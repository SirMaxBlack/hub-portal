#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="${HUB_PORTAL_REPOSITORY:-SirMaxBlack/hub-portal}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/manage.py" && -f "$SCRIPT_DIR/deploy/scripts/install.sh" ]]; then
    exec bash "$SCRIPT_DIR/deploy/scripts/install.sh" --source "$SCRIPT_DIR" "$@"
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
    github_curl_config="$tmp_dir/github-curl.conf"
    install -m 600 /dev/null "$github_curl_config"
    printf 'header = "Authorization: Bearer %s"\n' "$GITHUB_TOKEN" > "$github_curl_config"
    auth_args=(--config "$github_curl_config")
fi
github_request() {
    local url="$1" accept="$2" output="$3" timeout="${4:-120}" status rc
    set +e
    status="$(curl --silent --show-error --location --connect-timeout 10 --max-time "$timeout" \
        --retry 2 --retry-all-errors "${auth_args[@]}" \
        -H "Accept: $accept" -H 'X-GitHub-Api-Version: 2022-11-28' \
        --output "$output" --write-out '%{http_code}' "$url")"
    rc=$?
    set -e
    [[ $rc -eq 0 ]] || { echo "GitHub недоступен: curl завершился с кодом $rc." >&2; exit 2; }
    [[ "$status" =~ ^2[0-9][0-9]$ ]] && return 0
    if [[ "$status" == "404" && ${#auth_args[@]} -eq 0 ]]; then
        echo "GitHub Release недоступен (HTTP 404). Для приватного репозитория задайте GITHUB_TOKEN." >&2
    elif [[ "$status" == "401" || "$status" == "403" ]]; then
        echo "GitHub token отклонён (HTTP $status). Проверьте срок действия и право чтения $REPOSITORY." >&2
    else
        echo "GitHub вернул HTTP $status для $url." >&2
    fi
    exit 2
}

requested_version="${HUB_PORTAL_VERSION:-latest}"
api_file="$tmp_dir/release.json"
if [[ "$requested_version" == "latest" ]]; then
    github_request "https://api.github.com/repos/$REPOSITORY/releases?per_page=20" \
        'application/vnd.github+json' "$api_file"
    release_mode="latest"
else
    github_request "https://api.github.com/repos/$REPOSITORY/releases/tags/$requested_version" \
        'application/vnd.github+json' "$api_file"
    release_mode="tag"
fi
release_fields="$(python3 - "$release_mode" "$api_file" <<'PY'
import json
import sys

mode, payload_path = sys.argv[1:]
try:
    with open(payload_path, encoding="utf-8") as source:
        payload = json.load(source)
except (OSError, json.JSONDecodeError):
    raise SystemExit(2)
if mode == "latest":
    candidates = [
        item for item in payload
        if isinstance(item, dict) and not item.get("draft")
    ] if isinstance(payload, list) else []
    release = max(
        candidates,
        key=lambda item: item.get("published_at") or item.get("created_at") or "",
        default=None,
    )
else:
    release = payload if isinstance(payload, dict) else None
if not release:
    raise SystemExit(3)
assets = release.get("assets", [])
archive = next((item.get("url", "") for item in assets if isinstance(item, dict) and str(item.get("name", "")).endswith(".tar.gz")), "")
checksum = next((item.get("url", "") for item in assets if isinstance(item, dict) and str(item.get("name", "")).endswith(".sha256")), "")
print("\t".join((str(release.get("tag_name", "")), archive, checksum)))
PY
)" || { echo "GitHub вернул некорректное описание релиза." >&2; exit 2; }
IFS=$'\t' read -r tag archive_url checksum_url <<<"$release_fields"
[[ -n "$tag" && -n "$archive_url" && -n "$checksum_url" ]] || {
    echo "В последнем релизе отсутствует установочный архив или checksum." >&2
    exit 2
}
echo "[HUB] Выбран релиз: $tag"

github_request "$archive_url" 'application/octet-stream' "$tmp_dir/hub-portal.tar.gz" 900
github_request "$checksum_url" 'application/octet-stream' "$tmp_dir/hub-portal.tar.gz.sha256" 120
(cd "$tmp_dir" && awk '{print $1 "  hub-portal.tar.gz"}' hub-portal.tar.gz.sha256 | sha256sum -c -)
mkdir -p "$tmp_dir/source"
tar -xzf "$tmp_dir/hub-portal.tar.gz" -C "$tmp_dir/source" --strip-components=1
exec bash "$tmp_dir/source/deploy/scripts/install.sh" --source "$tmp_dir/source" "$@"
