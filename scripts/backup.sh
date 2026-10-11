#!/usr/bin/env bash
# Online backup of the database and .env (never the uploaded/cleaned files).
# Usage (from the repository directory, stack running):  bash scripts/backup.sh
# Result: backups/metadata-cleaner-<UTC timestamp>.sqlite3 and .env copy (mode 600).

set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
ts=$(date -u +%Y%m%dT%H%M%SZ)
name="metadata-cleaner-$ts.sqlite3"

# sqlite3 online backup API: consistent copy while the app keeps running.
docker compose exec -T api python - "$name" <<'PY'
import sqlite3, sys
src = sqlite3.connect("/data/metadata-cleaner.sqlite3")
dst = sqlite3.connect("/tmp/" + sys.argv[1])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
print("integrity_check:", ok)
sys.exit(0 if ok == "ok" else 1)
PY
# /tmp is a tmpfs, which "docker cp" cannot read: stream the file instead.
docker compose exec -T api cat "/tmp/$name" > "backups/$name"
docker compose exec -T api rm -f "/tmp/$name"
cp .env "backups/env-$ts"
chmod 600 "backups/$name" "backups/env-$ts"
echo "backup written: backups/$name ($(du -h "backups/$name" | cut -f1)) and backups/env-$ts"
echo "copy the backups/ folder off the server (it contains password hashes and the session secret)"
