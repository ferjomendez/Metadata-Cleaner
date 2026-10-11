#!/usr/bin/env bash
# Read-only preflight checks for the target server. Changes nothing.
# Usage (from the repository directory):  bash scripts/preflight.sh
# Paste the whole output back for review.

set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0; WARN=0; FAIL=0
ok()   { echo "PASS  $*"; PASS=$((PASS+1)); }
warn() { echo "WARN  $*"; WARN=$((WARN+1)); }
bad()  { echo "FAIL  $*"; FAIL=$((FAIL+1)); }
info() { echo "INFO  $*"; }

echo "== Metadata Cleaner preflight $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname)"
info "kernel $(uname -r); $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
info "git commit $(git rev-parse --short HEAD 2>/dev/null || echo 'n/a')"

# --- docker ------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
  if docker compose version >/dev/null 2>&1; then ok "$(docker compose version --short 2>/dev/null | sed 's/^/compose /')"
  else bad "docker compose plugin not available"; fi
  if docker info >/dev/null 2>&1; then ok "docker daemon reachable by $(id -un)"
  else bad "cannot talk to the docker daemon (is $(id -un) in the docker group?)"; fi
else
  bad "docker not installed"
fi

# --- .env ----------------------------------------------------------------------
BIND=""; PORT="8480"
if [ -f .env ]; then
  perm=$(stat -c %a .env)
  [ "$perm" = "600" ] && ok ".env present (mode 600)" || warn ".env mode is $perm (recommended: chmod 600 .env)"
  BIND=$(grep -E '^BIND_ADDRESS=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')
  P=$(grep -E '^HOST_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')
  [ -n "$P" ] && PORT="$P"
  SECRET=$(grep -E '^SESSION_SECRET=' .env | tail -1 | cut -d= -f2-)
  [ "${#SECRET}" -ge 32 ] && ok "SESSION_SECRET set (${#SECRET} chars)" || bad "SESSION_SECRET missing or shorter than 32 chars (openssl rand -hex 32)"
  case "$BIND" in
    ""|0.0.0.0|"::") bad "BIND_ADDRESS must be the server LAN IP, not '${BIND:-empty}'";;
    *) if ip -o addr show 2>/dev/null | grep -qw "$BIND"; then ok "BIND_ADDRESS $BIND is configured on this host"
       else bad "BIND_ADDRESS $BIND is not an address of this host"; fi;;
  esac
else
  bad ".env missing (cp .env.example .env && chmod 600 .env, then edit it)"
fi

# --- port ------------------------------------------------------------------------
if command -v ss >/dev/null 2>&1; then
  used=$(ss -Htln "sport = :$PORT" 2>/dev/null)
  if [ -z "$used" ]; then ok "port $PORT is free"
  elif docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -q "metadata-cleaner-api.*:$PORT->"; then
    ok "port $PORT is used by this app (already running)"
  else bad "port $PORT already in use: $(echo "$used" | awk '{print $4}' | tr '\n' ' ')"; fi
else
  warn "ss not available, port check skipped"
fi

# --- resources -------------------------------------------------------------------
root_dir=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
[ -d "$root_dir" ] || root_dir=/
avail_gb=$(df -BG --output=avail "$root_dir" 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$avail_gb" ]; then
  if [ "$avail_gb" -ge 35 ]; then ok "free disk for $root_dir: ${avail_gb} GB"
  else warn "free disk for $root_dir: ${avail_gb} GB (app refuses uploads below MIN_FREE_DISK_GB, default 20)"; fi
fi
mem_avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
swap_used=$(awk '/SwapTotal/ {t=$2} /SwapFree/ {f=$2} END {print int((t-f)/1024)}' /proc/meminfo)
[ "$mem_avail" -ge 2200 ] && ok "available RAM ${mem_avail} MB (app needs up to ~2 GB: api 512 MB + worker 1.5 GB)" \
  || warn "available RAM ${mem_avail} MB is low for api 512 MB + worker 1.5 GB"
info "swap in use ${swap_used} MB"
info "CPUs $(nproc); load $(cut -d' ' -f1-3 /proc/loadavg)"
info "user.max_user_namespaces=$(cat /proc/sys/user/max_user_namespaces 2>/dev/null || echo n/a)"

# --- existing containers (informational, not touched) ----------------------------
info "running containers: $(docker ps --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')"

# --- worker image self-test (only if already built) -------------------------------
if docker image inspect metadata-cleaner-worker:local >/dev/null 2>&1; then
  echo "-- worker self-test (hardened: no network, read-only, no capabilities)"
  docker run --rm --network none --read-only --tmpfs /tmp:size=64m,mode=1777 --cap-drop ALL \
    --security-opt no-new-privileges:true --pids-limit 128 --memory 512m \
    metadata-cleaner-worker:local python3 -m app.selftest | sed 's/^/   /'
  [ "${PIPESTATUS[0]}" -eq 0 ] && ok "worker self-test" || bad "worker self-test"
  echo "-- bubblewrap inside an unprivileged container (expected to FAIL; informational)"
  docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true --entrypoint sh \
    metadata-cleaner-worker:local -c 'unshare --user --map-root-user true 2>&1 && echo "user namespaces: allowed" || echo "user namespaces: blocked (expected)"' \
    2>/dev/null | sed 's/^/   /' || info "namespace probe skipped"
else
  info "worker image not built yet: run 'docker compose build' and rerun this script for the MAT2/ExifTool/FFmpeg self-test"
fi

echo "== preflight summary: ${PASS} pass, ${WARN} warn, ${FAIL} fail"
[ "$FAIL" -eq 0 ]
