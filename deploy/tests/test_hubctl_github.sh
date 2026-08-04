#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

export HUBCTL_SOURCE_ONLY=1
export HUB_PORTAL_GITHUB_TOKEN_FILE="$TEMP_DIR/github.token"
# shellcheck disable=SC1091
source "$ROOT/deploy/scripts/hubctl"

cat > "$TEMP_DIR/releases.json" <<'JSON'
[
  {"tag_name":"v0.1.0-alpha.9","draft":true,"assets":[]},
  {"tag_name":"v0.1.0-alpha.8","draft":false,"assets":[
    {"name":"hub-portal-0.1.0-alpha.8.tar.gz","url":"https://api.example/archive"},
    {"name":"hub-portal-0.1.0-alpha.8.tar.gz.sha256","url":"https://api.example/checksum"}
  ]}
]
JSON

expected=$'v0.1.0-alpha.8\thttps://api.example/archive\thttps://api.example/checksum'
actual="$(github_release_fields latest "$TEMP_DIR/releases.json")"
[[ "$actual" == "$expected" ]]

printf '{broken' > "$TEMP_DIR/broken.json"
if github_release_fields latest "$TEMP_DIR/broken.json" >/dev/null 2>&1; then
    echo "Некорректный JSON ошибочно принят." >&2
    exit 1
fi

unset GITHUB_TOKEN
prepare_github_auth "$TEMP_DIR"
[[ "$GITHUB_AUTH_ACTIVE" == "0" ]]

mkdir -p "$TEMP_DIR/bin"
cat > "$TEMP_DIR/bin/curl" <<'SH'
#!/usr/bin/env bash
printf '%s' "${MOCK_HTTP_STATUS:-404}"
SH
chmod 700 "$TEMP_DIR/bin/curl"
original_path="$PATH"
PATH="$TEMP_DIR/bin:$PATH"
set +e
error="$(github_request 'https://api.github.com/repos/example/private/releases' 'application/json' "$TEMP_DIR/error.json" 2>&1)"
request_rc=$?
set -e
PATH="$original_path"
[[ $request_rc -ne 0 ]]
[[ "$error" == *"sudo hubctl github-token set"* ]]
[[ "$error" != *"Traceback"* ]]

printf '%s\n' 'github_pat_testtoken123' > "$HUB_PORTAL_GITHUB_TOKEN_FILE"
prepare_github_auth "$TEMP_DIR"
[[ "$GITHUB_AUTH_ACTIVE" == "1" ]]
[[ -s "$TEMP_DIR/github-curl.conf" ]]

echo "hubctl GitHub helpers: OK"
