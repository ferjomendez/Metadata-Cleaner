"""Container health checks. Usage: python -m app.healthcheck api|worker"""

from __future__ import annotations

import sys
import time
import urllib.request

from .worker import HEARTBEAT_FILE


def main() -> int:
    role = sys.argv[1] if len(sys.argv) > 1 else "api"
    if role == "worker":
        try:
            age = time.time() - int(HEARTBEAT_FILE.read_text().strip())
        except (OSError, ValueError):
            return 1
        return 0 if age < 60 else 1
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=4) as resp:
            return 0 if resp.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
