#!/usr/bin/env bash
# Verification on the server: test suites inside hardened containers, checks
# of the running deployment, worker self-test and an end-to-end cleaning run
# with generated fixtures through the real API.
#
# Usage (from the repository directory, after "docker compose up -d"):
#     bash scripts/verify.sh
# It creates a temporary user "verify-<random>", deletes its jobs and the user
# at the end. Nothing else in the database is touched. Paste the output back.

set -u
cd "$(dirname "$0")/.." || exit 1

RESULTS=()
step() { echo; echo "== $*"; }
record() { RESULTS+=("$1  $2"); echo "$1  $2"; }

step "build test images (first run takes a few minutes)"
docker build -q -f backend/Dockerfile --target api-test -t metadata-cleaner-api-test:local . >/dev/null \
  && record PASS "api-test image built" || record FAIL "api-test image build"
docker build -q -f backend/Dockerfile.worker --target worker-test -t metadata-cleaner-worker-test:local . >/dev/null \
  && record PASS "worker-test image built" || record FAIL "worker-test image build"

HARDEN=(--read-only --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256)

step "api tests (pytest, hardened container, no network)"
out=$(docker run --rm --network none "${HARDEN[@]}" --tmpfs /tmp:size=256m,mode=1777 --memory 512m \
      metadata-cleaner-api-test:local 2>&1); rc=$?
echo "$out" | grep -E "^(FAILED|ERROR)|passed|failed" | tail -15
[ $rc -eq 0 ] && record PASS "api tests" || record FAIL "api tests"

step "worker tests (pytest: every format and mode, hardened container, no network)"
out=$(docker run --rm --network none "${HARDEN[@]}" --tmpfs /tmp:size=1g,mode=1777 --memory 1536m --cpus 1.5 \
      metadata-cleaner-worker-test:local nice -n 10 python3 -m pytest -q -p no:cacheprovider tests/worker tests/common 2>&1); rc=$?
echo "$out" | grep -E "^(FAILED|ERROR)|passed|failed" | tail -15
[ $rc -eq 0 ] && record PASS "worker tests" || record FAIL "worker tests"

step "running deployment"
API=$(docker compose ps -q api 2>/dev/null); WORKER=$(docker compose ps -q worker 2>/dev/null)
if [ -z "$API" ] || [ -z "$WORKER" ]; then
  record FAIL "stack not running (docker compose up -d) - deployment checks skipped"
else
  for c in api worker; do
    h=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q $c)")
    [ "$h" = "healthy" ] && record PASS "$c healthy" || record FAIL "$c health: $h"
  done
  ports=$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$API")
  echo "   api port bindings: $ports"
  echo "$ports" | grep -q '"HostIp":"0.0.0.0"\|"HostIp":""' && record FAIL "api published on all interfaces" \
    || record PASS "api published only on the configured address"
  [ "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$WORKER")" = "none" ] && record PASS "worker has no network" \
    || record FAIL "worker network mode"
  [ -z "$(docker inspect -f '{{json .NetworkSettings.Ports}}' "$WORKER" | tr -d '{}null')" ] && record PASS "worker publishes no ports" \
    || record FAIL "worker publishes ports"
  for c in "$API" "$WORKER"; do
    n=$(docker inspect -f '{{.Name}}' "$c")
    [ "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$c")" = "true" ] && record PASS "$n read-only root" || record FAIL "$n read-only root"
    [ "$(docker inspect -f '{{.HostConfig.Privileged}}' "$c")" = "false" ] && record PASS "$n not privileged" || record FAIL "$n privileged"
    docker inspect -f '{{json .HostConfig.CapDrop}}' "$c" | grep -q ALL && record PASS "$n drops all capabilities" || record FAIL "$n capabilities"
    docker inspect -f '{{json .HostConfig.SecurityOpt}}' "$c" | grep -q no-new-privileges && record PASS "$n no-new-privileges" || record FAIL "$n no-new-privileges"
    [ "$(docker inspect -f '{{.Config.User}}' "$c")" = "10001:10001" ] && record PASS "$n runs as uid 10001" || record FAIL "$n user"
    docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "$c" | grep -q docker.sock && record FAIL "$n mounts docker socket" || true
  done
  echo "   memory limits: api=$(docker inspect -f '{{.HostConfig.Memory}}' "$API") worker=$(docker inspect -f '{{.HostConfig.Memory}}' "$WORKER") bytes"

  step "worker self-test inside the running worker"
  docker compose exec -T worker python3 -m app.selftest | grep -E "FAIL|SELFTEST"
  [ "${PIPESTATUS[0]}" -eq 0 ] && record PASS "worker self-test" || record FAIL "worker self-test"

  step "end-to-end: upload generated fixtures, both modes, download, verify with ExifTool, delete"
  USERNAME="verify-$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  PW="$(head -c 24 /dev/urandom | base64 | tr -d '/+=\n')"
  if printf '%s\n' "$PW" | docker compose exec -T api python -m app.cli create-user --username "$USERNAME" --password-stdin >/dev/null; then
    NET=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$API")
    printf '%s\n%s\n' "$USERNAME" "$PW" | docker run --rm -i --network "$NET" --memory 1g \
      metadata-cleaner-worker-test:local python3 -m tests.e2e.smoke http://api:8000 | grep -E "^FAIL|SMOKE"
    [ "${PIPESTATUS[1]}" -eq 0 ] && record PASS "end-to-end smoke test" || record FAIL "end-to-end smoke test"
    docker compose exec -T api python -m app.cli delete-user "$USERNAME" --yes >/dev/null \
      && echo "   temporary user removed" || echo "   WARNING: could not remove $USERNAME, remove it in the admin page"
  else
    record FAIL "could not create temporary user"
  fi
  left=$(docker compose exec -T api sh -c 'find /work -type f | wc -l' | tr -d '\r ')
  echo "   files currently in work volume: $left (only files of other active jobs should remain)"
fi

step "summary"
fails=0
for r in "${RESULTS[@]}"; do case "$r" in FAIL*) echo "$r"; fails=$((fails+1));; esac; done
echo "VERIFY $([ $fails -eq 0 ] && echo PASS || echo FAIL): $(( ${#RESULTS[@]} - fails ))/${#RESULTS[@]} checks passed  (commit $(git rev-parse --short HEAD 2>/dev/null))"
[ $fails -eq 0 ]
