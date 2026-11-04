#!/usr/bin/env sh
# Load test (audit item 12) on the test server build (Caddy, gunicorn, DEBUG off) on a stack of
# its own with its own database, removed afterwards; nothing of the test server is touched.
#   ./scripts/loadtest.sh                 50 employees for 3 minutes
#   USERS=150 MINUTES=5 ./scripts/loadtest.sh
# The backend, the database and Caddy get the 4 processors and 8 GB of the test server between
# them (deploy/loadtest.yml), so the numbers are close to that server's, not to this computer's.
# Data: loadtest/seed.py; the employees: loadtest/staff.js (k6).
set -eu
cd "$(dirname "$0")/.."

export POSTGRES_PASSWORD=local-check-only
export C4R_DB_APP_PASSWORD=local-check-only-app
compose="docker compose -p c4rsys-load -f docker-compose.prod.yml -f deploy/prod-check.yml -f deploy/loadtest.yml"
trap '$compose --profile load down -v --remove-orphans' EXIT

echo "== the test server build"
$compose up -d --build --wait db backend web

echo "== data"
seed="$($compose exec -T -e C4R_LOAD_VEHICLES="${C4R_LOAD_VEHICLES:-50}" \
  -e C4R_LOAD_CLIENTS="${C4R_LOAD_CLIENTS:-500}" -e C4R_LOAD_HISTORY="${C4R_LOAD_HISTORY:-2000}" \
  backend python manage.py shell < loadtest/seed.py | grep '^C4R_LOAD_')"
echo "$seed" | grep RENTALS
C4R_LOAD_COMPANY="$(echo "$seed" | sed -n 's/^C4R_LOAD_COMPANY=//p')"
C4R_LOAD_SESSION="$(echo "$seed" | sed -n 's/^C4R_LOAD_SESSION=//p')"
export C4R_LOAD_COMPANY C4R_LOAD_SESSION

echo "== ${USERS:-50} employees for ${MINUTES:-3} minutes"
$compose stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' > /dev/null
( while sleep 20; do $compose stats --no-stream --format '   {{.Name}} cpu {{.CPUPerc}} mem {{.MemUsage}}'; done ) &
watcher=$!
$compose --profile load run --rm -e USERS="${USERS:-50}" -e MINUTES="${MINUTES:-3}" \
  -e C4R_LOAD_COMPANY -e C4R_LOAD_SESSION k6 || status=$?
kill "$watcher" 2>/dev/null || true
wait "$watcher" 2>/dev/null || true
exit "${status:-0}"
