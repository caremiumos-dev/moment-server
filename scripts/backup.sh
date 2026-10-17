#!/bin/sh
# Snapshot the database and media directory into a timestamped tarball.
#
#   ./scripts/backup.sh [output-dir]     (default: ./backups)
#
# Safe to run while the service is up: sqlite's .backup command takes a
# consistent snapshot even with WAL writers active, which copying the file
# would not.
set -eu

output_dir="${1:-./backups}"
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$output_dir"
target="$output_dir/moment-$stamp.tar.gz"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if command -v docker >/dev/null 2>&1 && docker compose ps --quiet moment >/dev/null 2>&1; then
    # Running under docker compose: use the container's python/sqlite.
    docker compose exec -T moment python - "$tmp/moment.db" <<'PY'
import sqlite3
import sys

destination = sqlite3.connect(sys.argv[1])
source = sqlite3.connect("/data/moment.db")
with destination:
    source.backup(destination)
destination.close()
source.close()
PY
    docker compose cp moment:/data/media "$tmp/media" >/dev/null
else
    python - "$tmp/moment.db" <<'PY'
import os
import sqlite3
import sys

source_path = os.environ.get("MOMENT_DATABASE_URL", "sqlite:///moment.db").replace("sqlite:///", "")
destination = sqlite3.connect(sys.argv[1])
source = sqlite3.connect(source_path)
with destination:
    source.backup(destination)
destination.close()
source.close()
PY
    media_dir="${MOMENT_MEDIA_DIR:-media}"
    [ -d "$media_dir" ] && cp -r "$media_dir" "$tmp/media"
fi

tar -czf "$target" -C "$tmp" .
echo "backup written to $target"
