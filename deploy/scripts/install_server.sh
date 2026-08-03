#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
echo "install_server.sh устарел; запускается новый интерактивный install.sh." >&2
exec "$SCRIPT_DIR/install.sh" --source "$SOURCE_ROOT" "$@"
