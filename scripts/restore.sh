#!/usr/bin/env bash
# Restore the database from a backup made by scripts/backup.sh.
# Usage: bash scripts/restore.sh backups/metadata-cleaner-<timestamp>.sqlite3
# Stops the stack, replaces the database, starts the stack again.
# Uploaded/cleaned files are not restored (they are temporary by design);
# jobs that referenced them expire on the next cleanup.

set -euo pipefail
cd "$(dirname "$0")/.."
file="${1:-}"
[ -f "$file" ] || { echo "usage: bash scripts/restore.sh backups/<file>.sqlite3"; exit 1; }
python3 - "$file" <<'PY' || { echo "backup failed integrity check"; exit 1; }
import sqlite3, sys
ok = sqlite3.connect(sys.argv[1]).execute("PRAGMA integrity_check").fetchone()[0]
print("integrity_check:", ok); sys.exit(0 if ok == "ok" else 1)
PY
read -r -p "This replaces the current database. Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "aborted"; exit 1; }
volume="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["volumes"]["mc_data"]["name"])')"
docker compose stop
# Stream through stdin so the backup file permissions do not matter.
docker run --rm -i --network none --user 10001:10001 -v "$volume:/data" --entrypoint sh \
  metadata-cleaner-worker:local -c \
  'rm -f /data/metadata-cleaner.sqlite3-wal /data/metadata-cleaner.sqlite3-shm && cat > /data/metadata-cleaner.sqlite3 && chmod 600 /data/metadata-cleaner.sqlite3' \
  < "$file"
docker compose start
echo "restored from $file; check with: docker compose ps && docker compose exec api python -m app.cli list-users"
